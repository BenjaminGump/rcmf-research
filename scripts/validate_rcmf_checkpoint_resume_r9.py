from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
from typing import Any, Mapping

os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import _bootstrap  # noqa: F401
import torch
from torch import Tensor, nn

from rcmf.training.oracle_decoder_5fc import module_state_sha256
from rcmf.training.state_conditioned_program_direct_7dg import seed_everything
from rcmf.utils.serialization import atomic_write_json, sha256_file
from scripts.run_rcmf_joint_full_bank_9a import (
    GLOBAL_SEED,
    _atomic_torch_save,
    _checkpoint_payload,
    _restore_checkpoint,
)


FORMAT = "exp037a_r9_cross_process_resume_equivalence_v1"
UNIT_IDS = ["diagnostic-unit-0", "diagnostic-unit-1", "diagnostic-unit-2"]
SOURCE_HASHES = {"diagnostic_source": hashlib.sha256(b"r9-source").hexdigest()}


class _ToyWriter(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.input = nn.Linear(5, 7)
        self.dropout = nn.Dropout(0.25)
        self.output = nn.Linear(7, 4)

    def forward(self, value: Tensor) -> Tensor:
        return self.output(self.dropout(torch.tanh(self.input(value))))


class _ToyReader(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.input = nn.Linear(4, 6)
        self.output = nn.Linear(6, 3)

    def forward(self, value: Tensor) -> Tensor:
        return self.output(torch.tanh(self.input(value)))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("synthetic", "uninterrupted", "prefix", "resume"),
        required=True,
    )
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    return parser.parse_args()


def _device(value: str) -> torch.device:
    if value == "auto":
        value = "cuda" if torch.cuda.is_available() else "cpu"
    result = torch.device(value)
    if result.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is not available")
    return result


def _build(device: torch.device) -> tuple[nn.Module, nn.Module, torch.optim.Optimizer]:
    seed_everything(GLOBAL_SEED)
    torch.use_deterministic_algorithms(True, warn_only=False)
    writer = _ToyWriter().to(device)
    reader = _ToyReader().to(device)
    optimizer = torch.optim.AdamW(
        [
            {"params": writer.parameters(), "lr": 1.0e-3},
            {"params": reader.parameters(), "lr": 7.5e-4},
        ],
        weight_decay=1.0e-4,
    )
    return writer, reader, optimizer


def _update_hash(digest: Any, value: Any) -> None:
    if isinstance(value, Tensor):
        tensor = value.detach().cpu().contiguous()
        digest.update(b"tensor")
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(json.dumps(list(tensor.shape)).encode("ascii"))
        digest.update(tensor.numpy().tobytes(order="C"))
    elif isinstance(value, Mapping):
        digest.update(b"mapping")
        for key in sorted(value, key=lambda item: str(item)):
            _update_hash(digest, str(key))
            _update_hash(digest, value[key])
    elif isinstance(value, (list, tuple)):
        digest.update(b"sequence")
        for item in value:
            _update_hash(digest, item)
    else:
        digest.update(type(value).__name__.encode("ascii"))
        digest.update(repr(value).encode("utf-8"))


def _state_hash(value: Any) -> str:
    digest = hashlib.sha256()
    _update_hash(digest, value)
    return digest.hexdigest()


def _run_unit(
    *,
    index: int,
    device: torch.device,
    writer: nn.Module,
    reader: nn.Module,
    optimizer: torch.optim.Optimizer,
) -> float:
    optimizer.zero_grad(set_to_none=True)
    cpu_random = torch.rand((2, 5), device="cpu").to(device)
    device_random = torch.rand((2, 5), device=device)
    python_scale = 0.5 + random.random()
    value = cpu_random + device_random + float(index) / 10.0
    target = torch.linspace(-0.5, 0.5, steps=6, device=device).reshape(2, 3)
    output = reader(writer(value))
    loss = (output - target).square().mean() * python_scale
    loss.backward()
    optimizer.step()
    if not bool(torch.isfinite(loss.detach())):
        raise RuntimeError("Synthetic resume diagnostic produced a nonfinite loss")
    return float(loss.detach().cpu())


def _summary(
    *,
    device: torch.device,
    writer: nn.Module,
    reader: nn.Module,
    optimizer: torch.optim.Optimizer,
    history: list[dict[str, Any]],
    completed: int,
) -> dict[str, Any]:
    cpu_rng = torch.get_rng_state().clone()
    cuda_rng = (
        [value.clone() for value in torch.cuda.get_rng_state_all()]
        if torch.cuda.is_available()
        else []
    )
    return {
        "device": str(device),
        "completed_unit_ids": [str(row["unit_id"]) for row in history],
        "loss_history": [float(row["loss"]) for row in history],
        "writer_sha256": module_state_sha256(writer),
        "reader_sha256": module_state_sha256(reader),
        "optimizer_sha256": _state_hash(optimizer.state_dict()),
        "cpu_rng_sha256": _state_hash(cpu_rng),
        "cuda_rng_sha256": [_state_hash(value) for value in cuda_rng],
        "next_python_random": random.random(),
        "next_cpu_random": torch.rand(4, device="cpu").tolist(),
        "next_cuda_random": (
            torch.rand(4, device=device).detach().cpu().tolist()
            if device.type == "cuda"
            else []
        ),
        "backward_count": completed,
        "optimizer_step_count": completed,
    }


def _execute_worker(mode: str, output_root: Path, device: torch.device) -> None:
    writer, reader, optimizer = _build(device)
    checkpoint_path = output_root / "resume_checkpoint.pt"
    if mode == "resume":
        payload = torch.load(checkpoint_path, map_location=device, weights_only=False)
        completed, history, _ = _restore_checkpoint(
            payload=payload,
            writer=writer,
            reader=reader,
            optimizer=optimizer,
            unit_ids=UNIT_IDS,
            source_hashes=SOURCE_HASHES,
        )
        output_path = output_root / "resumed.json"
        stop = len(UNIT_IDS)
    else:
        completed = 0
        history = []
        output_path = output_root / (
            "uninterrupted.json" if mode == "uninterrupted" else "prefix.json"
        )
        stop = len(UNIT_IDS) if mode == "uninterrupted" else len(UNIT_IDS) - 1
    for index in range(completed, stop):
        loss = _run_unit(
            index=index,
            device=device,
            writer=writer,
            reader=reader,
            optimizer=optimizer,
        )
        history.append({"unit_id": UNIT_IDS[index], "loss": loss})
        completed = index + 1
    if mode == "prefix":
        payload = _checkpoint_payload(
            writer=writer,
            reader=reader,
            optimizer=optimizer,
            completed_units=completed,
            unit_ids=UNIT_IDS,
            history=history,
            shuffle_nll={},
            source_hashes=SOURCE_HASHES,
        )
        _atomic_torch_save(payload, checkpoint_path)
    atomic_write_json(
        output_path,
        {
            "format": FORMAT,
            "mode": mode,
            **_summary(
                device=device,
                writer=writer,
                reader=reader,
                optimizer=optimizer,
                history=history,
                completed=completed,
            ),
        },
    )


def _run_subprocess(mode: str, output_root: Path, device: str) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--mode",
        mode,
        "--output-root",
        str(output_root),
        "--device",
        device,
    ]
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(GLOBAL_SEED)
    result = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "mode": mode,
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _run_synthetic(output_root: Path, device_name: str) -> None:
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(f"Diagnostic output root is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    process_rows = [
        _run_subprocess(mode, output_root, device_name)
        for mode in ("uninterrupted", "prefix", "resume")
    ]
    if any(row["returncode"] != 0 for row in process_rows):
        atomic_write_json(output_root / "processes.json", {"processes": process_rows})
        raise RuntimeError(f"Cross-process worker failed: {process_rows}")
    uninterrupted = json.loads(
        (output_root / "uninterrupted.json").read_text(encoding="utf-8")
    )
    resumed = json.loads((output_root / "resumed.json").read_text(encoding="utf-8"))
    fields = [
        "completed_unit_ids",
        "loss_history",
        "writer_sha256",
        "reader_sha256",
        "optimizer_sha256",
        "cpu_rng_sha256",
        "cuda_rng_sha256",
        "next_python_random",
        "next_cpu_random",
        "next_cuda_random",
        "backward_count",
        "optimizer_step_count",
    ]
    comparisons = {field: uninterrupted[field] == resumed[field] for field in fields}
    summary = {
        "format": FORMAT,
        "global_seed": GLOBAL_SEED,
        "process_count": len(process_rows),
        "fresh_resume_process": True,
        "checkpoint_sha256": sha256_file(output_root / "resume_checkpoint.pt"),
        "comparisons": comparisons,
        "processes": process_rows,
        "uninterrupted": uninterrupted,
        "resumed": resumed,
        "passed": all(comparisons.values()),
    }
    atomic_write_json(output_root / "synthetic_resume_equivalence.json", summary)
    if not summary["passed"]:
        raise RuntimeError(f"RESUME_EQUIVALENCE_FAIL: {comparisons}")
    print(json.dumps(summary, sort_keys=True))


def main() -> None:
    args = _parse_args()
    if args.mode == "synthetic":
        _run_synthetic(args.output_root, args.device)
        return
    args.output_root.mkdir(parents=True, exist_ok=True)
    _execute_worker(args.mode, args.output_root, _device(args.device))


if __name__ == "__main__":
    main()
