#!/usr/bin/env python3
"""Restore EXP-031A from its detached archive and verify D0/D1 token identity."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


CHECKOUT = Path("/lambda/nfs/rcmf-persist/project/archives/exp031a_rcmf_joint_full_bank_57d2a347/restoration_checkout")
ARCHIVE = Path("/lambda/nfs/rcmf-persist/project/archives/exp031a_rcmf_joint_full_bank_57d2a347")
ARTIFACT = ARCHIVE / "artifacts"
OUTPUT = ARCHIVE / "restoration_smoke"
EXPECTED_HEAD = "57d2a3479ff292dd8f89bdd0ea9f9417abc42a48"
EXPECTED_CHECKPOINT = "d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1"
EXPECTED_FIELD = "5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e"
EXPECTED_ENSEMBLE = "c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f"

sys.path.insert(0, str(CHECKOUT))
sys.path.insert(0, str(CHECKOUT / "scripts"))
os.chdir(CHECKOUT)
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import torch  # noqa: E402
from rcmf.config import load_config  # noqa: E402
from rcmf.training.rcmf_joint_full_bank_9a import (  # noqa: E402
    FrozenSelectorDecomposition,
    assert_frozen_without_gradients,
    read_compiled_field,
)
from rcmf.utils.serialization import sha256_file  # noqa: E402
from scripts.run_raw_memory_first37_7f import FrozenDeploymentSelector  # noqa: E402
from scripts.run_rcmf_joint_full_bank_9a import _build_backend, _build_components  # noqa: E402
from scripts.run_rcmf_joint_full_bank_first37_9a import _generate  # noqa: E402


def canonical_sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def token_sha(values):
    return hashlib.sha256(json.dumps(values, separators=(",", ":")).encode()).hexdigest()


started = time.time()
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=CHECKOUT, text=True).strip()
status = subprocess.check_output(["git", "status", "--short"], cwd=CHECKOUT, text=True).strip()
config_path = CHECKOUT / "configs/benchmark/stage_c_rcmf_joint_full_bank_9a.yaml"
checkpoint_path = ARTIFACT / "joint_training/checkpoints/epoch_02.pt"
field_path = ARTIFACT / "deployment_field/complete_37_task_field.pt"
selector_root = ARTIFACT / "external_dependencies/signature_balanced_field_7c_20260818_001/selector"
ensemble_path = selector_root / "ensemble_scores.pt"
preflight = {
    "format": "exp031a_archive_restoration_smoke_preflight_v1",
    "purpose": "restore detached EXP-031A code plus archived checkpoint/field and reproduce one D0 and one D1 deterministic generation",
    "source_head": head,
    "config_path": str(config_path),
    "config_sha256": sha256_file(config_path),
    "data_case": "archived first37/smoke_v2 task 0d01c76_1 step 1 under D0 and D1",
    "checkpoint_path": str(checkpoint_path),
    "checkpoint_sha256": sha256_file(checkpoint_path),
    "deployment_field_path": str(field_path),
    "deployment_field_sha256": sha256_file(field_path),
    "selector_ensemble_sha256": sha256_file(ensemble_path),
    "hardware": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "no-cuda",
    "global_seed": 25101,
    "historical_generation_seconds": {"D0": 4.13439209992066, "D1": 4.140350563917309},
    "expected_wall_minutes": 5,
    "conservative_wall_minutes": 15,
    "expected_h100_hours": 0.25,
    "may_exceed_18_hours": False,
    "restart_plan": "no optimizer or mutable scientific state; atomic result write; a failed smoke is safely rerunnable from the immutable inputs",
}
atomic_json(OUTPUT / "preflight.json", preflight)
if head != EXPECTED_HEAD or status:
    raise SystemExit(f"detached checkout identity failure: head={head}, status={status!r}")
if preflight["checkpoint_sha256"] != EXPECTED_CHECKPOINT:
    raise SystemExit("archived checkpoint hash mismatch")
if preflight["deployment_field_sha256"] != EXPECTED_FIELD:
    raise SystemExit("archived deployment field hash mismatch")
if preflight["selector_ensemble_sha256"] != EXPECTED_ENSEMBLE:
    raise SystemExit("archived selector ensemble hash mismatch")
if not torch.cuda.is_available():
    raise SystemExit("CUDA required for exact restoration generation")

cfg = load_config(config_path)
backend = _build_backend(cfg)
if hasattr(backend.model, "gradient_checkpointing_disable"):
    backend.model.gradient_checkpointing_disable()
backend.model.config.use_cache = True
backend.model.eval()
assert_frozen_without_gradients(backend.model)

checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
deployment = torch.load(field_path, map_location="cpu", weights_only=False)
writer, reader = _build_components(backend.device)
writer.load_state_dict(checkpoint["writer_state_dict"])
reader.load_state_dict(deployment["reader_state_dict"])
writer.eval()
reader.eval()
for module in (writer, reader):
    for parameter in module.parameters():
        parameter.requires_grad_(False)
if sum(p.numel() for p in writer.parameters()) != 8_949_760:
    raise SystemExit("writer parameter count mismatch")
if sum(p.numel() for p in reader.parameters()) != 17_860_608:
    raise SystemExit("reader parameter count mismatch")

if int(deployment["memory_count"]) != 499:
    raise SystemExit("deployment memory count mismatch")
if tuple(deployment["A"].shape) != (960, 8, 256) or tuple(deployment["B"].shape) != (8, 256):
    raise SystemExit("deployment field shape mismatch")
A = deployment["A"].to(backend.device, torch.float32)
B = deployment["B"].to(backend.device, torch.float32)

ensemble = torch.load(ensemble_path, map_location="cpu", weights_only=False)
selector_checkpoints = []
selector_hashes = []
for row in ensemble["seed_checkpoints"]:
    original = Path(str(row["checkpoint"]))
    archived = selector_root / original.parent.name / original.name
    actual = sha256_file(archived)
    if actual != str(row["checkpoint_sha256"]):
        raise SystemExit(f"archived selector checkpoint mismatch: {archived}")
    selector_checkpoints.append(torch.load(archived, map_location="cpu", weights_only=False))
    selector_hashes.append(actual)
decomposition = FrozenSelectorDecomposition.from_checkpoints(
    selector_checkpoints, ensemble["train_calibration"]
).to(backend.device)
decomposition.eval()
for parameter in decomposition.parameters():
    parameter.requires_grad_(False)


class QueryOnly:
    def __init__(self, backend_value):
        self.backend = backend_value


query_only = QueryOnly(backend)
condition_results = {}
for condition in ("D0", "D1"):
    audit_path = ARTIFACT / f"first37/smoke_v2/{condition}/task_results/0d01c76_1.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    step = audit["steps"][0]
    messages = step["exact_model_message_array"]
    expected_ids = [int(value) for value in step["generated_token_ids"]]
    if condition == "D0":
        actual_ids, _, generation = _generate(
            backend=backend,
            messages=messages,
            max_new_tokens=int(step["generation_config"]["max_new_tokens"]),
            reader=None,
            slots=None,
        )
    else:
        views = FrozenDeploymentSelector._state_values(query_only, messages)
        query = decomposition.query(views)[0]
        slots = read_compiled_field(query=query, A=A, B=B, nonempty=True)
        actual_ids, _, generation = _generate(
            backend=backend,
            messages=messages,
            max_new_tokens=int(step["generation_config"]["max_new_tokens"]),
            reader=reader,
            slots=slots,
        )
    condition_results[condition] = {
        "audit_path": str(audit_path),
        "prompt_sha256": step["rendered_message_sha256"],
        "expected_token_count": len(expected_ids),
        "actual_token_count": len(actual_ids),
        "expected_token_sha256": token_sha(expected_ids),
        "actual_token_sha256": token_sha(actual_ids),
        "exact_generated_token_equality": actual_ids == expected_ids,
        "generation_seconds": generation["generation_seconds"],
        "reader_active": generation["reader"]["active"],
    }

assert_frozen_without_gradients(backend.model)
assert_frozen_without_gradients(writer)
assert_frozen_without_gradients(reader)
passed = all(row["exact_generated_token_equality"] for row in condition_results.values())
result = {
    "format": "exp031a_archive_restoration_smoke_v1",
    "passed": passed,
    "source_head": head,
    "checkpoint_sha256": preflight["checkpoint_sha256"],
    "deployment_field_sha256": preflight["deployment_field_sha256"],
    "field_shapes": {"A": list(A.shape), "B": list(B.shape)},
    "memory_count": int(deployment["memory_count"]),
    "writer_parameter_count": sum(p.numel() for p in writer.parameters()),
    "reader_parameter_count": sum(p.numel() for p in reader.parameters()),
    "selector_checkpoint_sha256": selector_hashes,
    "conditions": condition_results,
    "qwen_frozen_and_gradient_free": True,
    "wall_seconds": time.time() - started,
}
result["identity_sha256"] = canonical_sha(result)
atomic_json(OUTPUT / "restoration_smoke.json", result)
print(json.dumps(result, indent=2, sort_keys=True))
if not passed:
    raise SystemExit("archived deterministic generation mismatch")
