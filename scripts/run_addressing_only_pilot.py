from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
import random
import statistics
import time
from typing import Any

import _bootstrap  # noqa: F401

import torch

from rcmf.config import load_config, save_resolved_config
from rcmf.training.addressing_only import (
    AddressingLossWeights,
    AddressingOnlyModel,
    baseline_frozen_qwen_cosine,
    baseline_global_mean_train_utility,
    baseline_random,
    evaluate_scores,
    geometry_diagnostics,
    addressing_losses,
    rho_only_scores,
    rows_to_tensors,
    task_balanced_batches,
)
from rcmf.utils.serialization import (
    append_jsonl,
    atomic_write_json,
    atomic_write_text,
    maybe_git_commit,
    read_jsonl,
    sha256_file,
)


PILOT_VERSION = "stage_b_addressing_only_pilot_v1"


def _load_rows(path: Path) -> list[dict[str, Any]]:
    return list(read_jsonl(path))


def _load_representation_cache(
    path: Path,
    *,
    expected_count: int,
    expected_source_path: Path | None,
    model_name: str,
    accepted_formats: set[str],
) -> tuple[torch.Tensor, dict[str, Any]]:
    payload = torch.load(path, map_location="cpu")
    fmt = str(payload.get("format"))
    if fmt not in accepted_formats:
        raise ValueError(f"Unsupported representation cache format {fmt} at {path}")
    representations = payload.get("representations")
    if not isinstance(representations, torch.Tensor) or representations.dim() != 2:
        raise ValueError(f"Representation cache missing 2D representations tensor: {path}")
    if representations.shape[0] != expected_count:
        raise ValueError(
            f"Representation row count {representations.shape[0]} does not match expected {expected_count}: {path}"
        )
    if payload.get("model_name") != model_name:
        raise ValueError(f"Representation model mismatch: {payload.get('model_name')} != {model_name}")
    if expected_source_path is not None and expected_source_path.exists():
        expected_hash = sha256_file(expected_source_path)
        if payload.get("source_sha256") != expected_hash:
            raise ValueError(f"Representation source hash mismatch for {path}")
    metadata = {
        key: value
        for key, value in payload.items()
        if key not in {"representations", "chunk_representations", "owner_indices", "chunk_token_counts"}
    }
    metadata["path"] = str(path)
    metadata["representation_shape"] = list(representations.shape)
    return representations.to(torch.float32), metadata


def _split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    if not train or not validation:
        raise ValueError("Stage-B labels must contain train and validation rows")
    return train, validation


def _state_reps_for_rows(state_representations: torch.Tensor, rows: list[dict[str, Any]], device: torch.device) -> torch.Tensor:
    indices = torch.tensor([int(row["state_index"]) for row in rows], dtype=torch.long)
    return state_representations[indices].to(device=device, dtype=torch.float32)


def _batch_tensors(
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    rows: list[dict[str, Any]],
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor, dict[str, torch.Tensor]]:
    labels = rows_to_tensors(rows, device=device)
    state_reps = state_representations[labels["state_indices"].cpu()].to(device=device, dtype=torch.float32)
    return state_reps, memory_representations.to(device=device, dtype=torch.float32), labels


def _metric_mean(metrics: dict[str, Any], key: str) -> float:
    value = metrics.get(key, {})
    if isinstance(value, dict):
        mean = value.get("mean")
        return float(mean) if mean is not None else 0.0
    return float(value or 0.0)


def _evaluate_model(
    model: AddressingOnlyModel,
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    rows: list[dict[str, Any]],
    device: torch.device,
    *,
    shuffle_seed: int | None = None,
) -> tuple[dict[str, Any], dict[str, torch.Tensor]]:
    labels = rows_to_tensors(rows, device=device)
    state_reps = _state_reps_for_rows(state_representations, rows, device)
    if shuffle_seed is not None and state_reps.shape[0] > 1:
        generator = torch.Generator(device="cpu")
        generator.manual_seed(shuffle_seed)
        order = torch.randperm(state_reps.shape[0], generator=generator).to(device)
        state_reps = state_reps[order]
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    model.eval()
    with torch.no_grad():
        payload = model(state_reps, memory_reps)
        metrics = evaluate_scores(payload["q"], labels)
    return metrics, payload


def _evaluate_baselines(
    model: AddressingOnlyModel,
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    device: torch.device,
    seed: int,
) -> dict[str, Any]:
    train_labels = rows_to_tensors(train_rows, device=device)
    validation_labels = rows_to_tensors(validation_rows, device=device)
    validation_state_reps = _state_reps_for_rows(state_representations, validation_rows, device)
    memory_reps = memory_representations.to(device=device, dtype=torch.float32)
    with torch.no_grad():
        model_payload = model(validation_state_reps, memory_reps)
        global_scores = baseline_global_mean_train_utility(train_labels, validation_labels)
        cosine_scores = baseline_frozen_qwen_cosine(validation_state_reps, memory_reps)
        random_scores = baseline_random(tuple(global_scores.shape), seed=seed, device=device)
        rho_scores = rho_only_scores(model_payload, state_count=len(validation_rows))
    return {
        "global_mean_train_utility": evaluate_scores(global_scores, validation_labels),
        "frozen_qwen_hidden_cosine": evaluate_scores(cosine_scores, validation_labels),
        "deterministic_random": evaluate_scores(random_scores, validation_labels),
        "rho_only": evaluate_scores(rho_scores, validation_labels),
    }


def _save_checkpoint(
    path: Path,
    model: AddressingOnlyModel,
    optimizer: torch.optim.Optimizer,
    epoch: int,
    extra: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    torch.save(
        {
            "format": PILOT_VERSION,
            "epoch": epoch,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "extra": extra,
            "git_commit": maybe_git_commit(),
        },
        tmp_path,
    )
    tmp_path.replace(path)


def _train_one_seed(
    *,
    cfg: Any,
    seed: int,
    train_rows: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    output_dir: Path,
    loss_weights: AddressingLossWeights,
    max_epochs: int,
    batch_size: int,
    eval_every: int,
    patience: int,
    lr: float,
    weight_decay: float,
    grad_clip: float,
    device: torch.device,
) -> dict[str, Any]:
    torch.manual_seed(seed)
    random.seed(seed)
    model = AddressingOnlyModel(cfg, representation_dim=int(state_representations.shape[1])).to(device)
    program_before = {key: value.detach().clone() for key, value in model.compiler.program_head.state_dict().items()}
    optimizer = torch.optim.AdamW(
        [param for param in model.parameters() if param.requires_grad],
        lr=lr,
        weight_decay=weight_decay,
    )
    metrics_path = output_dir / f"seed_{seed}" / "metrics.jsonl"
    best_metric = -1.0
    best_epoch = 0
    bad_evals = 0
    best_path = output_dir / f"seed_{seed}" / "checkpoint_best.pt"
    final_path = output_dir / f"seed_{seed}" / "checkpoint_final.pt"
    start = time.perf_counter()
    for epoch in range(1, max_epochs + 1):
        model.train()
        rng = random.Random(seed * 100_000 + epoch)
        train_losses: list[float] = []
        for batch_indices in task_balanced_batches(train_rows, batch_size=batch_size, rng=rng):
            batch_rows = [train_rows[index] for index in batch_indices]
            state_batch, memory_batch, labels = _batch_tensors(
                state_representations,
                memory_representations,
                batch_rows,
                device,
            )
            payload = model(state_batch, memory_batch)
            loss, loss_metrics = addressing_losses(payload["q"], labels, loss_weights)
            if not torch.isfinite(loss.detach()).all().item():
                raise FloatingPointError(f"Non-finite loss at seed={seed} epoch={epoch}")
            optimizer.zero_grad(set_to_none=True)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(
                [param for param in model.parameters() if param.requires_grad],
                grad_clip,
            )
            optimizer.step()
            train_losses.append(float(loss.detach().cpu()))
        if epoch == 1 or epoch % eval_every == 0 or epoch == max_epochs:
            validation_metrics, payload = _evaluate_model(
                model,
                state_representations,
                memory_representations,
                validation_rows,
                device,
            )
            primary = _metric_mean(validation_metrics, "ndcg@4")
            row = {
                "seed": seed,
                "epoch": epoch,
                "train_loss_mean": statistics.mean(train_losses) if train_losses else None,
                "validation_ndcg@4": primary,
                "validation_positive_mass@4": _metric_mean(validation_metrics, "positive_mass_coverage@4"),
                "validation_mrr": _metric_mean(validation_metrics, "mrr"),
                "elapsed_s": time.perf_counter() - start,
                **loss_metrics,
            }
            append_jsonl(metrics_path, row)
            print(
                f"seed={seed} epoch={epoch}/{max_epochs} "
                f"train_loss={row['train_loss_mean']:.4f} val_ndcg4={primary:.4f}",
                flush=True,
            )
            if primary > best_metric + 1.0e-6:
                best_metric = primary
                best_epoch = epoch
                bad_evals = 0
                _save_checkpoint(
                    best_path,
                    model,
                    optimizer,
                    epoch,
                    {"primary_metric": "validation_ndcg@4", "primary_value": primary},
                )
            else:
                bad_evals += 1
                if bad_evals >= patience:
                    break
    _save_checkpoint(final_path, model, optimizer, epoch, {"best_epoch": best_epoch})
    best_payload = torch.load(best_path, map_location=device)
    model.load_state_dict(best_payload["model"])
    validation_metrics, payload = _evaluate_model(
        model,
        state_representations,
        memory_representations,
        validation_rows,
        device,
    )
    shuffled_metrics, shuffled_payload = _evaluate_model(
        model,
        state_representations,
        memory_representations,
        validation_rows,
        device,
        shuffle_seed=seed + 10_000,
    )
    baseline_metrics = _evaluate_baselines(
        model,
        state_representations,
        memory_representations,
        train_rows,
        validation_rows,
        device,
        seed,
    )
    geometry = geometry_diagnostics(payload["state_address"], payload["alpha"], payload["rho"])
    shuffled_score_delta = float(
        (payload["q"].detach().to(torch.float32) - shuffled_payload["q"].detach().to(torch.float32))
        .abs()
        .mean()
        .cpu()
    )
    program_after = model.compiler.program_head.state_dict()
    program_delta = max(
        float((program_after[key].detach().cpu() - program_before[key].cpu()).abs().max().item())
        for key in program_before
    )
    if program_delta != 0.0:
        raise AssertionError(f"program_head changed during addressing-only training: max_delta={program_delta}")
    summary = {
        "format": PILOT_VERSION,
        "seed": seed,
        "best_epoch": best_epoch,
        "epochs_ran": epoch,
        "best_checkpoint": str(best_path),
        "final_checkpoint": str(final_path),
        "trainable_parameter_names": model.trainable_parameter_names(),
        "program_head_max_abs_delta": program_delta,
        "injector": "not_constructed",
        "q_definition": "rho_i * dot(b(s), alpha_i)",
        "validation_metrics": validation_metrics,
        "shuffled_state_metrics": shuffled_metrics,
        "baseline_metrics": baseline_metrics,
        "geometry": geometry,
        "correct_vs_shuffled_score_abs_delta_mean": shuffled_score_delta,
        "runtime_s": time.perf_counter() - start,
    }
    atomic_write_json(output_dir / f"seed_{seed}" / "summary.json", summary)
    return summary


def _run_overfit(
    *,
    cfg: Any,
    train_rows: list[dict[str, Any]],
    state_representations: torch.Tensor,
    memory_representations: torch.Tensor,
    output_dir: Path,
    device: torch.device,
    loss_weights: AddressingLossWeights,
    epochs: int,
    lr: float,
) -> dict[str, Any]:
    rows = [row for row in train_rows if sum(row["positive_gain"]) > 0][:8]
    if len(rows) < 2:
        rows = train_rows[: min(8, len(train_rows))]
    summary = _train_one_seed(
        cfg=cfg,
        seed=123,
        train_rows=rows,
        validation_rows=rows,
        state_representations=state_representations,
        memory_representations=memory_representations,
        output_dir=output_dir / "overfit",
        loss_weights=loss_weights,
        max_epochs=epochs,
        batch_size=min(8, len(rows)),
        eval_every=max(1, epochs // 5),
        patience=epochs + 1,
        lr=lr,
        weight_decay=0.0,
        grad_clip=1.0,
        device=device,
    )
    summary["overfit_rows"] = len(rows)
    atomic_write_json(output_dir / "overfit_summary.json", summary)
    return summary


def _aggregate_seed_summaries(seed_summaries: list[dict[str, Any]]) -> dict[str, Any]:
    def collect(path: tuple[str, ...]) -> list[float]:
        values = []
        for summary in seed_summaries:
            obj: Any = summary
            for key in path:
                obj = obj[key]
            mean = obj["mean"] if isinstance(obj, dict) else obj
            if mean is not None:
                values.append(float(mean))
        return values

    metric_paths = {
        "learned_ndcg@4": ("validation_metrics", "ndcg@4"),
        "learned_positive_mass@4": ("validation_metrics", "positive_mass_coverage@4"),
        "learned_mrr": ("validation_metrics", "mrr"),
        "shuffled_ndcg@4": ("shuffled_state_metrics", "ndcg@4"),
        "shuffled_positive_mass@4": ("shuffled_state_metrics", "positive_mass_coverage@4"),
        "global_ndcg@4": ("baseline_metrics", "global_mean_train_utility", "ndcg@4"),
        "global_positive_mass@4": (
            "baseline_metrics",
            "global_mean_train_utility",
            "positive_mass_coverage@4",
        ),
        "rho_ndcg@4": ("baseline_metrics", "rho_only", "ndcg@4"),
        "rho_positive_mass@4": ("baseline_metrics", "rho_only", "positive_mass_coverage@4"),
        "cosine_ndcg@4": ("baseline_metrics", "frozen_qwen_hidden_cosine", "ndcg@4"),
        "random_ndcg@4": ("baseline_metrics", "deterministic_random", "ndcg@4"),
    }
    aggregate: dict[str, Any] = {}
    for name, path in metric_paths.items():
        values = collect(path)
        aggregate[name] = {
            "values": values,
            "mean": statistics.mean(values) if values else None,
            "std": statistics.pstdev(values) if len(values) > 1 else 0.0 if values else None,
        }
    learned_ndcg = aggregate["learned_ndcg@4"]["mean"] or 0.0
    learned_mass = aggregate["learned_positive_mass@4"]["mean"] or 0.0
    strongest_baseline_ndcg = max(
        aggregate["global_ndcg@4"]["mean"] or 0.0,
        aggregate["rho_ndcg@4"]["mean"] or 0.0,
    )
    strongest_baseline_mass = max(
        aggregate["global_positive_mass@4"]["mean"] or 0.0,
        aggregate["rho_positive_mass@4"]["mean"] or 0.0,
    )
    shuffled_ndcg = aggregate["shuffled_ndcg@4"]["mean"] or 0.0
    shuffled_mass = aggregate["shuffled_positive_mass@4"]["mean"] or 0.0
    aggregate["scientific_gate"] = {
        "passed": bool(
            learned_ndcg > strongest_baseline_ndcg + 0.01
            and learned_mass > strongest_baseline_mass + 0.01
            and shuffled_ndcg < learned_ndcg - 0.02
            and shuffled_mass < learned_mass - 0.02
        ),
        "criterion": (
            "learned mean NDCG@4 and positive_mass@4 must exceed max(global,rho-only) "
            "by >0.01, and shuffled-state must be lower than learned by >0.02 on both metrics"
        ),
        "learned_ndcg@4": learned_ndcg,
        "strongest_baseline_ndcg@4": strongest_baseline_ndcg,
        "shuffled_ndcg@4": shuffled_ndcg,
        "learned_positive_mass@4": learned_mass,
        "strongest_baseline_positive_mass@4": strongest_baseline_mass,
        "shuffled_positive_mass@4": shuffled_mass,
    }
    return aggregate


def _report(summary: dict[str, Any]) -> str:
    aggregate = summary["aggregate"]
    gate = aggregate["scientific_gate"]
    lines = [
        "# Stage-B Addressing-Only Pilot",
        "",
        f"- format: `{summary['format']}`",
        f"- labels: `{summary['labels_dir']}`",
        f"- effective memory bank size: {summary['effective_memory_count']}",
        f"- seeds: {summary['seeds']}",
        f"- scientific gate passed: `{gate['passed']}`",
        f"- q definition: `rho_i * dot(b(s), alpha_i)`",
        "- Qwen action loss: disabled",
        "- program head: frozen",
        "- additive-token injector: not constructed",
        "",
        "## Three-Seed Aggregate",
        "",
        "| metric | mean | std |",
        "| --- | ---: | ---: |",
    ]
    for key, value in aggregate.items():
        if key == "scientific_gate":
            continue
        lines.append(f"| `{key}` | `{value['mean']}` | `{value['std']}` |")
    lines.extend(
        [
            "",
            "## Scientific Gate",
            "",
            f"```json\n{gate}\n```",
            "",
            "## Artifacts",
            "",
        ]
    )
    for seed_summary in summary["seed_summaries"]:
        lines.append(f"- seed {seed_summary['seed']} best checkpoint: `{seed_summary['best_checkpoint']}`")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage-B addressing-only RCMF pilot.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--labels-dir", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--representation-cache-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[1, 2, 3])
    parser.add_argument("--max-epochs", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--eval-every", type=int, default=5)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--lr", type=float, default=1.0e-3)
    parser.add_argument("--weight-decay", type=float, default=1.0e-4)
    parser.add_argument("--grad-clip", type=float, default=1.0)
    parser.add_argument("--smoke-epochs", type=int, default=10)
    parser.add_argument("--overfit-epochs", type=int, default=80)
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    cfg = load_config(args.config)
    labels_dir = Path(args.labels_dir)
    data_dir = Path(args.data)
    repr_dir = Path(args.representation_cache_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_rows(labels_dir / "student_labels.jsonl")
    memory_bank = _load_rows(labels_dir / "effective_memory_bank.jsonl")
    train_rows, validation_rows = _split_rows(rows)
    memory_indices = [int(row["memory_index"]) for row in memory_bank]
    state_reps, state_cache_metadata = _load_representation_cache(
        repr_dir / "decision_state_representations.pt",
        expected_count=len(rows),
        expected_source_path=data_dir / "decision_examples.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"pooled_qwen_hidden_v1", "pooled_qwen_hidden_v2"},
    )
    all_memory_reps, memory_cache_metadata = _load_representation_cache(
        repr_dir / "memory_record_representations.pt",
        expected_count=46,
        expected_source_path=data_dir / "memory_records.jsonl",
        model_name=cfg.model.name,
        accepted_formats={"chunked_qwen_hidden_v1", "record_qwen_hidden_v2"},
    )
    memory_reps = all_memory_reps[memory_indices]
    if state_reps.shape[1] != memory_reps.shape[1]:
        raise ValueError("State and memory representation dimensions differ")

    device = torch.device(args.device)
    loss_weights = AddressingLossWeights()
    atomic_write_json(
        output_dir / "run_metadata.json",
        {
            "format": PILOT_VERSION,
            "config": args.config,
            "labels_dir": str(labels_dir),
            "data_dir": str(data_dir),
            "representation_cache_dir": str(repr_dir),
            "state_cache_metadata": state_cache_metadata,
            "memory_cache_metadata": memory_cache_metadata,
            "effective_memory_count": len(memory_bank),
            "train_rows": len(train_rows),
            "validation_rows": len(validation_rows),
            "seeds": args.seeds,
            "git_commit": maybe_git_commit(),
        },
    )
    save_resolved_config(cfg, output_dir / "resolved_config.yaml")

    print("running tiny overfit test", flush=True)
    overfit_summary = _run_overfit(
        cfg=cfg,
        train_rows=train_rows,
        state_representations=state_reps,
        memory_representations=memory_reps,
        output_dir=output_dir,
        device=device,
        loss_weights=loss_weights,
        epochs=args.overfit_epochs,
        lr=args.lr,
    )

    print("running short hyperparameter smoke", flush=True)
    smoke_summary = _train_one_seed(
        cfg=cfg,
        seed=999,
        train_rows=train_rows,
        validation_rows=validation_rows,
        state_representations=state_reps,
        memory_representations=memory_reps,
        output_dir=output_dir / "smoke",
        loss_weights=loss_weights,
        max_epochs=args.smoke_epochs,
        batch_size=args.batch_size,
        eval_every=max(1, args.smoke_epochs // 2),
        patience=args.smoke_epochs + 1,
        lr=args.lr,
        weight_decay=args.weight_decay,
        grad_clip=args.grad_clip,
        device=device,
    )

    seed_summaries = []
    for seed in args.seeds:
        print(f"running addressing-only pilot seed={seed}", flush=True)
        seed_summaries.append(
            _train_one_seed(
                cfg=cfg,
                seed=seed,
                train_rows=train_rows,
                validation_rows=validation_rows,
                state_representations=state_reps,
                memory_representations=memory_reps,
                output_dir=output_dir,
                loss_weights=loss_weights,
                max_epochs=args.max_epochs,
                batch_size=args.batch_size,
                eval_every=args.eval_every,
                patience=args.patience,
                lr=args.lr,
                weight_decay=args.weight_decay,
                grad_clip=args.grad_clip,
                device=device,
            )
        )
    aggregate = _aggregate_seed_summaries(seed_summaries)
    summary = {
        "format": PILOT_VERSION,
        "labels_dir": str(labels_dir),
        "effective_memory_count": len(memory_bank),
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "seeds": args.seeds,
        "overfit_summary": overfit_summary,
        "smoke_summary": smoke_summary,
        "seed_summaries": seed_summaries,
        "aggregate": aggregate,
        "checkpoint_dirs": [str(output_dir / f"seed_{seed}") for seed in args.seeds],
        "git_commit": maybe_git_commit(),
    }
    atomic_write_json(output_dir / "summary.json", summary)
    atomic_write_text(output_dir / "report.md", _report(summary))
    print(f"Wrote addressing-only pilot to {output_dir}")


if __name__ == "__main__":
    main()
