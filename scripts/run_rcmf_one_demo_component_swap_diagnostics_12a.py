"""Run token-free EXP-035A module diagnostics on frozen heldout state rows."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from typing import Any

import _bootstrap  # noqa: F401
import torch
from torch import Tensor
import torch.nn.functional as F

from rcmf.benchmarks.appworld.prompt import build_appworld_messages
from rcmf.config import load_config
from rcmf.training.deep_residual_carrier_7e import decoder_layers
from rcmf.training.rcmf_joint_full_bank_9a import (
    assert_frozen_without_gradients,
    read_compiled_field,
    tensor_sha256,
)
from rcmf.training.rcmf_one_demo_component_swap_12a import (
    CONDITIONS,
    condition_parts,
    load_writer_reader_package,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256
from rcmf.training.state_conditioned_transition_6b import AttemptLedger
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_one_demo_component_swap_12a import (
    ComponentSwapRuntime,
    load_backend,
    read_json,
)


FORMAT = "rcmf_one_demo_component_swap_no_generation_diagnostics_12a_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/benchmark/stage_c_rcmf_one_demo_component_swap_12a.yaml"),
    )
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--source-head", required=True)
    return parser.parse_args()


def git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def require_unique_attempt(path: Path, attempt_id: str) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and json.loads(line).get("attempt_id") == attempt_id:
            raise ValueError(f"Duplicate attempt ID: {attempt_id}")


def atomic_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def mean_summary(values: Sequence[float]) -> dict[str, float]:
    work = [float(value) for value in values]
    return {
        "minimum": min(work),
        "maximum": max(work),
        "mean": statistics.fmean(work),
        "median": statistics.median(work),
    }


def spearman(left: Tensor, right: Tensor) -> float:
    left_order = torch.argsort(left)
    right_order = torch.argsort(right)
    left_rank = torch.empty_like(left_order, dtype=torch.float32)
    right_rank = torch.empty_like(right_order, dtype=torch.float32)
    ranks = torch.arange(left.numel(), dtype=torch.float32, device=left.device)
    left_rank[left_order] = ranks
    right_rank[right_order] = ranks
    return float(F.cosine_similarity(
        (left_rank - left_rank.mean()).unsqueeze(0),
        (right_rank - right_rank.mean()).unsqueeze(0),
    ).item())


def historical_state_rows(
    *,
    root: Path,
    heldout_tasks: Sequence[str],
) -> list[dict[str, Any]]:
    manifest = read_json(root / "condition_manifest.json")
    task_set = set(heldout_tasks)
    selected = [
        row
        for row in manifest["conditions"]
        if int(row["epoch"]) == 2
        and str(row["control"]) == "L0_zero"
        and str(row["source_task_id"]) in task_set
    ]
    if len(selected) != 98:
        raise ValueError(f"Expected 98 frozen heldout state rows, found {len(selected)}")
    output = []
    for condition in selected:
        name = hashlib.sha256(str(condition["condition_key"]).encode("utf-8")).hexdigest()
        row = read_json(root / "condition_outputs" / f"{name}.json")
        if row["source_state_id"] != condition["source_state_id"]:
            raise ValueError("Historical state row identity differs")
        output.append(
            {
                "state_id": str(row["source_state_id"]),
                "task_id": str(row["source_task_id"]),
                "task_message": str(row["task_message"]),
                "trajectory_so_far": list(row["trajectory_so_far"]),
                "historical_row_sha256": sha256_file(
                    root / "condition_outputs" / f"{name}.json"
                ),
            }
        )
    order = {task_id: index for index, task_id in enumerate(heldout_tasks)}
    return sorted(
        output,
        key=lambda row: (
            order[row["task_id"]],
            int(str(row["state_id"]).split(":step:")[1].split(":")[0]),
            row["state_id"],
        ),
    )


def query_caches(settings: Mapping[str, Any]) -> dict[str, dict[str, Tensor]]:
    old = torch.load(
        Path(str(settings["shared"]["old_source_cache"])),
        map_location="cpu",
        weights_only=False,
    )
    fresh = torch.load(
        Path(str(settings["selectors"]["fresh"]["factor_path"])),
        map_location="cpu",
        weights_only=False,
    )
    expected = {
        "old": str(old["state_query_sha256"]),
        "fresh": str(settings["selectors"]["fresh"]["state_query_sha256"]),
    }
    payloads = {"old": old, "fresh": fresh}
    result: dict[str, dict[str, Tensor]] = {}
    for name, payload in payloads.items():
        queries = payload["state_queries"].to(torch.float32)
        if tensor_sha256(queries) != expected[name]:
            raise ValueError(f"{name} state-query tensor SHA differs")
        result[name] = {
            str(state_id): queries[index]
            for index, state_id in enumerate(payload["ordered_state_ids"])
        }
    return result


def transition_keys(
    settings: Mapping[str, Any], memory_ids: Sequence[str]
) -> dict[str, Tensor]:
    old = torch.load(
        Path(str(settings["shared"]["old_source_cache"])),
        map_location="cpu",
        weights_only=False,
    )
    fresh = torch.load(
        Path(str(settings["selectors"]["fresh"]["factor_path"])),
        map_location="cpu",
        weights_only=False,
    )
    result = {}
    for name, payload in (("old", old), ("fresh", fresh)):
        index = {str(value): i for i, value in enumerate(payload["ordered_transition_ids"])}
        result[name] = payload["memory_keys"][
            torch.tensor([index[value] for value in memory_ids], dtype=torch.long)
        ].to(torch.float32)
    return result


def payload_diagnostics(
    *,
    settings: Mapping[str, Any],
    memory_ids: Sequence[str],
    device: torch.device,
) -> dict[str, Any]:
    source = torch.load(
        Path(str(settings["shared"]["old_source_cache"])),
        map_location="cpu",
        weights_only=False,
    )
    index = {str(value): i for i, value in enumerate(source["ordered_transition_ids"])}
    views = source["memory_views"][
        torch.tensor([index[value] for value in memory_ids], dtype=torch.long)
    ].to(device)
    values = {}
    for name in ("old", "fresh"):
        checkpoint = settings["writer_readers"][name]
        writer, _, _ = load_writer_reader_package(
            name=name,
            checkpoint_path=Path(str(checkpoint["checkpoint"])),
            expected_checkpoint_sha256=str(checkpoint["checkpoint_sha256"]),
            device=device,
        )
        with torch.no_grad():
            values[name] = writer(views).detach().to(torch.float32).cpu()
    row_cosines = F.cosine_similarity(
        values["old"].flatten(start_dim=1),
        values["fresh"].flatten(start_dim=1),
        dim=1,
    )
    return {
        "memory_count": len(memory_ids),
        "old_payload_norm": float(values["old"].norm()),
        "fresh_payload_norm": float(values["fresh"].norm()),
        "per_memory_payload_cosine": mean_summary(row_cosines.tolist()),
    }


def field_cosines(runtime: ComponentSwapRuntime) -> dict[str, float]:
    result = {}
    for binding in ("C", "S"):
        oo = torch.cat([value.flatten() for value in runtime.fields["OO"][binding]])
        of = torch.cat([value.flatten() for value in runtime.fields["OF"][binding]])
        fo = torch.cat([value.flatten() for value in runtime.fields["FO"][binding]])
        ff = torch.cat([value.flatten() for value in runtime.fields["FF"][binding]])
        result[f"old_selector_old_vs_fresh_writer_{binding}"] = float(
            F.cosine_similarity(oo.unsqueeze(0), of.unsqueeze(0)).item()
        )
        result[f"fresh_selector_old_vs_fresh_writer_{binding}"] = float(
            F.cosine_similarity(fo.unsqueeze(0), ff.unsqueeze(0)).item()
        )
        result[f"old_vs_fresh_selector_old_writer_{binding}"] = float(
            F.cosine_similarity(oo.unsqueeze(0), fo.unsqueeze(0)).item()
        )
        result[f"old_vs_fresh_selector_fresh_writer_{binding}"] = float(
            F.cosine_similarity(of.unsqueeze(0), ff.unsqueeze(0)).item()
        )
    return result


def main() -> None:
    args = parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_12a"]
    if git_head() != args.source_head:
        raise ValueError("Working tree HEAD differs from declared source head")
    if int(settings["global_seed"]) != 25101:
        raise ValueError("EXP-035A seed differs")
    require_unique_attempt(args.artifact_dir / "attempts.jsonl", args.attempt_id)
    task_manifest = read_json(args.artifact_dir / "manifests/heldout_tasks.json")
    package_manifest = read_json(
        args.artifact_dir / "manifests/component_package_manifest.json"
    )
    heldout_tasks = [str(value) for value in task_manifest["task_ids"]]
    rows = historical_state_rows(
        root=Path(str(settings["shared"]["heldout_state_rows_root"])),
        heldout_tasks=heldout_tasks,
    )
    q_caches = query_caches(settings)

    started = time.perf_counter()
    with AttemptLedger(
        args.artifact_dir,
        run_uuid=str(settings["run_uuid"]),
        attempt_id=args.attempt_id,
        phase="no_generation_module_diagnostics",
        command=list(os.sys.argv),
        local_head=args.source_head,
        github_head=args.source_head,
        lambda_head=args.source_head,
        tmux_session=os.environ.get("TMUX", "none"),
        config_sha256=sha256_file(args.config),
        data_manifest_hashes={
            "component_package_manifest": sha256_file(
                args.artifact_dir / "manifests/component_package_manifest.json"
            ),
            "heldout_task_manifest": sha256_file(
                args.artifact_dir / "manifests/heldout_tasks.json"
            ),
        },
        parent_attempt_id="exp035a-preflight-002",
        resume_checkpoint="atomic_state_rows",
        scientific_parameter_changed=False,
        heartbeat_interval_s=240,
    ) as attempt:
        backend = load_backend(cfg)
        runtime = ComponentSwapRuntime(
            settings_9a=cfg.raw["stage_c_9a"],
            settings_12a=settings,
            backend=backend,
            package_manifest=package_manifest,
        )
        field_payload = torch.load(
            Path(str(package_manifest["fields"]["OO"]["path"])),
            map_location="cpu",
            weights_only=False,
        )
        memory_ids = [str(value) for value in field_payload["memory_ids"]]
        keys = {
            name: value.to(backend.device)
            for name, value in transition_keys(settings, memory_ids).items()
        }
        payload_report = payload_diagnostics(
            settings=settings,
            memory_ids=memory_ids,
            device=backend.device,
        )

        output_root = args.artifact_dir / "preflight/no_generation_state_rows"
        output_root.mkdir(parents=True, exist_ok=True)
        layers = decoder_layers(backend.model)
        insertion_layers = tuple(runtime.readers["old"].insertion_layers)
        if insertion_layers != tuple(runtime.readers["fresh"].insertion_layers):
            raise ValueError("Reader insertion layers differ")

        completed_rows = []
        for row_index, source in enumerate(rows):
            output_path = output_root / (
                hashlib.sha256(source["state_id"].encode("utf-8")).hexdigest() + ".json"
            )
            if output_path.exists():
                existing = read_json(output_path)
                if (
                    existing.get("format") != FORMAT
                    or existing.get("state_id") != source["state_id"]
                    or len(existing.get("conditions", [])) != len(CONDITIONS)
                ):
                    raise ValueError("Existing diagnostic state row identity differs")
                completed_rows.append(existing)
                continue

            messages = build_appworld_messages(
                task_message=source["task_message"],
                trajectory_so_far=source["trajectory_so_far"],
                prompt_profile=str(settings["prompt_profile"]),
                max_context_turns=int(cfg.raw["stage_c_9a"]["appworld"]["max_context_turns"]),
            )
            tokenized = backend.tokenize_messages(messages, add_generation_prompt=True)
            if int(tokenized.attention_mask.sum()) >= int(
                cfg.raw["stage_c_9a"]["appworld"]["context_limit"]
            ):
                raise ValueError("Frozen diagnostic state is over context")

            captured: dict[int, Tensor] = {}
            handles = []
            for layer_index in insertion_layers:
                def capture(_module: Any, _args: Any, output: Any, index: int = layer_index):
                    hidden = output[0] if isinstance(output, tuple) else output
                    captured[index] = hidden[:, -1:, :].detach()
                    return output

                handles.append(layers[layer_index].register_forward_hook(capture))
            base_model = getattr(backend.model, "model", backend.model)
            with torch.inference_mode(), torch.autocast(
                device_type="cuda",
                dtype=torch.bfloat16,
                enabled=backend.device.type == "cuda",
            ):
                base_model(
                    input_ids=tokenized.input_ids,
                    attention_mask=tokenized.attention_mask,
                    use_cache=False,
                )
            for handle in reversed(handles):
                handle.remove()
            if set(captured) != set(insertion_layers):
                raise ValueError("Not all reader insertion-layer states were captured")

            state_id = source["state_id"]
            conditions = []
            for condition in CONDITIONS:
                cell, binding, selector_name, writer_reader_name = condition_parts(condition)
                query = q_caches[selector_name][state_id].to(backend.device)
                A, B = runtime.fields[cell][binding]
                slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
                per_layer = {}
                for layer_index in insertion_layers:
                    adapter = runtime.readers[writer_reader_name].adapters[str(layer_index)]
                    with torch.inference_mode(), torch.autocast(
                        device_type="cuda",
                        dtype=torch.bfloat16,
                        enabled=backend.device.type == "cuda",
                    ):
                        _, probabilities, delta = adapter(captured[layer_index], slots)
                    work = probabilities.to(torch.float32).clamp_min(1.0e-12)
                    per_layer[str(layer_index)] = {
                        "reader_residual_norm": float(delta.to(torch.float32).norm()),
                        "attention_entropy": float(
                            (-(work * work.log()).sum(dim=-1).mean()).item()
                        ),
                        "finite": bool(
                            torch.isfinite(delta).all() and torch.isfinite(probabilities).all()
                        ),
                    }
                conditions.append(
                    {
                        "condition": condition,
                        "cell": cell,
                        "binding": binding,
                        "selector_package": selector_name,
                        "writer_reader_package": writer_reader_name,
                        "query_norm": float(query.norm()),
                        "query_sha256": tensor_sha256(query.detach().cpu()),
                        "field_slot_norm": float(slots.to(torch.float32).norm()),
                        "field_slot_sha256": tensor_sha256(slots.detach().cpu()),
                        "field_slot_exact_zero_rate": float((slots == 0).to(torch.float32).mean()),
                        "finite": bool(torch.isfinite(query).all() and torch.isfinite(slots).all()),
                        "reader_layers": per_layer,
                    }
                )

            old_query = q_caches["old"][state_id].to(backend.device)
            fresh_query = q_caches["fresh"][state_id].to(backend.device)
            old_scores = old_query @ keys["old"].T
            fresh_scores = fresh_query @ keys["fresh"].T
            selector_comparison = {
                "score_spearman": spearman(old_scores, fresh_scores),
                "top_overlap": {
                    str(k): len(
                        set(torch.topk(old_scores, k).indices.tolist())
                        & set(torch.topk(fresh_scores, k).indices.tolist())
                    )
                    for k in (1, 4, 8)
                },
            }
            result = {
                "format": FORMAT,
                "run_uuid": str(settings["run_uuid"]),
                "source_head": args.source_head,
                "state_id": state_id,
                "task_id": source["task_id"],
                "historical_row_sha256": source["historical_row_sha256"],
                "prompt_profile": str(settings["prompt_profile"]),
                "prompt_tokens": int(tokenized.attention_mask.sum()),
                "rendered_prompt_sha256": hashlib.sha256(
                    tokenized.metadata["text"].encode("utf-8")
                ).hexdigest(),
                "generated_tokens": 0,
                "appworld_executions": 0,
                "hidden_diagnostic_scope": "final_prompt_token_at_each_insertion_layer",
                "selector_comparison": selector_comparison,
                "conditions": conditions,
            }
            result["row_sha256"] = canonical_sha256(result)
            atomic_write_json(output_path, result)
            completed_rows.append(result)
            attempt.progress(
                status="no_generation_state_forward",
                completed_units=row_index + 1,
                total_units=len(rows),
                latest_validated_checkpoint=str(output_path),
            )

        all_conditions = [
            condition for row in completed_rows for condition in row["conditions"]
        ]
        by_condition = {}
        for condition_name in CONDITIONS:
            selected = [
                value for value in all_conditions if value["condition"] == condition_name
            ]
            residuals = [
                layer["reader_residual_norm"]
                for value in selected
                for layer in value["reader_layers"].values()
            ]
            entropies = [
                layer["attention_entropy"]
                for value in selected
                for layer in value["reader_layers"].values()
            ]
            by_condition[condition_name] = {
                "state_count": len(selected),
                "query_norm": mean_summary([value["query_norm"] for value in selected]),
                "field_slot_norm": mean_summary(
                    [value["field_slot_norm"] for value in selected]
                ),
                "reader_residual_norm": mean_summary(residuals),
                "attention_entropy": mean_summary(entropies),
                "finite": all(value["finite"] for value in selected)
                and all(
                    layer["finite"]
                    for value in selected
                    for layer in value["reader_layers"].values()
                ),
                "exact_zero_rate": statistics.fmean(
                    value["field_slot_exact_zero_rate"] for value in selected
                ),
            }

        summary = {
            "format": FORMAT,
            "run_uuid": str(settings["run_uuid"]),
            "source_head": args.source_head,
            "state_count": len(completed_rows),
            "condition_count": len(CONDITIONS),
            "forward_count": len(completed_rows),
            "generated_tokens": 0,
            "appworld_executions": 0,
            "hidden_diagnostic_scope": "final_prompt_token_at_each_insertion_layer",
            "conditions": by_condition,
            "selector_score_spearman": mean_summary(
                [row["selector_comparison"]["score_spearman"] for row in completed_rows]
            ),
            "selector_top_overlap": {
                str(k): mean_summary(
                    [
                        row["selector_comparison"]["top_overlap"][str(k)]
                        for row in completed_rows
                    ]
                )
                for k in (1, 4, 8)
            },
            "payload_diagnostics": payload_report,
            "field_cosines": field_cosines(runtime),
            "elapsed_seconds": time.perf_counter() - started,
            "optimizer_steps": 0,
            "parameter_updates": 0,
            "passed": all(value["finite"] for value in by_condition.values()),
        }
        summary["summary_sha256"] = canonical_sha256(summary)
        atomic_write_json(
            args.artifact_dir / "preflight/no_generation_module_diagnostics.json",
            summary,
        )
        atomic_jsonl(
            args.artifact_dir / "preflight/no_generation_module_diagnostics_rows.jsonl",
            completed_rows,
        )
        assert_frozen_without_gradients(backend.model)
        for reader in runtime.readers.values():
            assert_frozen_without_gradients(reader)
        attempt.progress(
            status="no_generation_diagnostics_complete",
            completed_units=len(completed_rows),
            total_units=len(completed_rows),
            latest_validated_checkpoint=str(
                args.artifact_dir / "preflight/no_generation_module_diagnostics.json"
            ),
        )
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
