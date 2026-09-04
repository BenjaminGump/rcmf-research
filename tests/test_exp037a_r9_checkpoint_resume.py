from __future__ import annotations

import copy
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
import torch
from torch import Tensor, nn

from rcmf.training.oracle_decoder_5fc import module_state_sha256
from scripts.run_rcmf_joint_full_bank_9a import (
    CHECKPOINT_VERSION,
    GLOBAL_SEED,
    _canonical_rng_byte_tensor,
    _checkpoint_payload,
    _restore_checkpoint,
)
from scripts.validate_rcmf_checkpoint_resume_r9 import _state_hash


UNIT_IDS = ["unit-0", "unit-1"]
SOURCE_HASHES = {"source": "a" * 64}


def _components(
    device: torch.device,
) -> tuple[nn.Module, nn.Module, torch.optim.Optimizer]:
    torch.manual_seed(GLOBAL_SEED)
    writer = nn.Linear(4, 4).to(device)
    reader = nn.Linear(4, 2).to(device)
    optimizer = torch.optim.AdamW(
        list(writer.parameters()) + list(reader.parameters()), lr=1.0e-3
    )
    optimizer.zero_grad(set_to_none=True)
    value = torch.arange(8, dtype=torch.float32, device=device).reshape(2, 4)
    reader(writer(value)).square().mean().backward()
    optimizer.step()
    return writer, reader, optimizer


def _payload(
    device: torch.device,
) -> tuple[dict[str, Any], nn.Module, nn.Module, torch.optim.Optimizer]:
    writer, reader, optimizer = _components(device)
    payload = _checkpoint_payload(
        writer=writer,
        reader=reader,
        optimizer=optimizer,
        completed_units=1,
        unit_ids=UNIT_IDS,
        history=[{"completed_units": 1}],
        shuffle_nll={"state": 1.25},
        source_hashes=SOURCE_HASHES,
    )
    return payload, writer, reader, optimizer


def _move_tensors(value: Any, device: torch.device) -> Any:
    if isinstance(value, Tensor):
        return value.to(device)
    if isinstance(value, dict):
        return {key: _move_tensors(item, device) for key, item in value.items()}
    if isinstance(value, list):
        return [_move_tensors(item, device) for item in value]
    if isinstance(value, tuple):
        return tuple(_move_tensors(item, device) for item in value)
    return value


def test_cpu_rng_writer_reader_and_optimizer_restore_exactly() -> None:
    device = torch.device("cpu")
    payload, _, _, _ = _payload(device)
    expected_torch_rng = payload["torch_rng_state"].clone()
    expected_writer = str(payload["writer_sha256"])
    expected_reader = str(payload["reader_sha256"])
    expected_optimizer = _state_hash(payload["optimizer_state_dict"])
    writer, reader, optimizer = _components(device)
    torch.manual_seed(999)
    completed, history, shuffle_nll = _restore_checkpoint(
        payload=payload,
        writer=writer,
        reader=reader,
        optimizer=optimizer,
        unit_ids=UNIT_IDS,
        source_hashes=SOURCE_HASHES,
    )
    assert completed == 1
    assert history == [{"completed_units": 1}]
    assert shuffle_nll == {"state": 1.25}
    assert torch.equal(torch.get_rng_state(), expected_torch_rng)
    assert module_state_sha256(writer) == expected_writer
    assert module_state_sha256(reader) == expected_reader
    assert _state_hash(optimizer.state_dict()) == expected_optimizer


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA required")
def test_cuda_mapped_cpu_and_cuda_rng_states_restore_exactly() -> None:
    device = torch.device("cuda")
    payload, _, _, _ = _payload(device)
    expected_torch_rng = payload["torch_rng_state"].clone()
    expected_cuda_rng = [value.clone() for value in payload["cuda_rng_state"]]
    expected_writer = str(payload["writer_sha256"])
    expected_reader = str(payload["reader_sha256"])
    expected_optimizer = _state_hash(payload["optimizer_state_dict"])
    mapped_payload = _move_tensors(payload, device)
    assert mapped_payload["torch_rng_state"].device.type == "cuda"
    assert all(value.device.type == "cuda" for value in mapped_payload["cuda_rng_state"])
    writer, reader, optimizer = _components(device)
    _restore_checkpoint(
        payload=mapped_payload,
        writer=writer,
        reader=reader,
        optimizer=optimizer,
        unit_ids=UNIT_IDS,
        source_hashes=SOURCE_HASHES,
    )
    assert torch.equal(torch.get_rng_state(), expected_torch_rng)
    assert all(
        torch.equal(actual, expected)
        for actual, expected in zip(torch.cuda.get_rng_state_all(), expected_cuda_rng)
    )
    assert module_state_sha256(writer) == expected_writer
    assert module_state_sha256(reader) == expected_reader
    assert _state_hash(optimizer.state_dict()) == expected_optimizer


@pytest.mark.parametrize(
    ("value", "error"),
    [
        (b"not-a-tensor", TypeError),
        (torch.ones(8, dtype=torch.float32), TypeError),
        (torch.ones((2, 4), dtype=torch.uint8), ValueError),
        (torch.empty(0, dtype=torch.uint8), ValueError),
    ],
)
def test_rng_state_validation_fails_closed(value: Any, error: type[Exception]) -> None:
    with pytest.raises(error):
        _canonical_rng_byte_tensor(value, name="test_rng")


@pytest.mark.parametrize("field", ["format", "global_seed", "unit_ids", "source_hashes"])
def test_checkpoint_identity_mutations_still_fail(field: str) -> None:
    payload, _, _, _ = _payload(torch.device("cpu"))
    payload = copy.deepcopy(payload)
    if field == "format":
        payload[field] = CHECKPOINT_VERSION + "-wrong"
    elif field == "global_seed":
        payload[field] = GLOBAL_SEED + 1
    elif field == "unit_ids":
        payload[field] = ["wrong"]
    else:
        payload[field] = {"source": "b" * 64}
    writer, reader, optimizer = _components(torch.device("cpu"))
    with pytest.raises(ValueError, match="resume identity differs"):
        _restore_checkpoint(
            payload=payload,
            writer=writer,
            reader=reader,
            optimizer=optimizer,
            unit_ids=UNIT_IDS,
            source_hashes=SOURCE_HASHES,
        )


def test_cross_process_resume_matches_uninterrupted(tmp_path: Path) -> None:
    root = tmp_path / "cross-process"
    environment = dict(os.environ)
    environment["PYTHONHASHSEED"] = str(GLOBAL_SEED)
    result = subprocess.run(
        [
            sys.executable,
            "scripts/validate_rcmf_checkpoint_resume_r9.py",
            "--mode",
            "synthetic",
            "--output-root",
            str(root),
            "--device",
            "cpu",
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    summary = __import__("json").loads(
        (root / "synthetic_resume_equivalence.json").read_text(encoding="utf-8")
    )
    assert summary["passed"]
    assert summary["process_count"] == 3
    assert all(summary["comparisons"].values())
