from __future__ import annotations

import copy
import inspect

import pytest
import torch
from torch import nn

from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    CalibratedFieldReaderHooks,
    PositiveFieldRecord,
    ReversiblePositiveRCMFField,
    calibrate_residual,
    candidate_manifest,
    compile_positive_field,
    derive_layer_caps,
    read_confidence_field,
    read_positive_field,
    tau_for_median_confidence,
)
from rcmf.training.rcmf_joint_full_bank_9a import (
    FieldReaderHooks,
    SLOT_COUNT,
    StandardFieldCrossAttentionReader,
)
from scripts.run_rcmf_benefit_preserving_calibration_9b import (
    critical_benefit_gate,
    derive_unlabeled_calibration,
)
from scripts.analyze_rcmf_benefit_preserving_gain_loss_9b import (
    FINDINGS,
    attempt_ids,
    dominant_sign_audit,
    first_text_divergence,
    git_safe_check,
    git_safe_findings,
    git_safe_redact,
)


class _Block(nn.Module):
    def __init__(self, width: int) -> None:
        super().__init__()
        self.projection = nn.Linear(width, width, bias=False)

    def forward(self, hidden_states: torch.Tensor) -> tuple[torch.Tensor]:
        return (hidden_states + 0.05 * self.projection(hidden_states),)


class _Backbone(nn.Module):
    def __init__(self, layers: int, width: int) -> None:
        super().__init__()
        self.layers = nn.ModuleList(_Block(width) for _ in range(layers))


class _Model(nn.Module):
    def __init__(self, layers: int = 4, width: int = 8) -> None:
        super().__init__()
        self.model = _Backbone(layers, width)

    def forward(self, hidden: torch.Tensor) -> torch.Tensor:
        for layer in self.model.layers:
            hidden = layer(hidden)[0]
        return hidden


def _reader() -> StandardFieldCrossAttentionReader:
    reader = StandardFieldCrossAttentionReader(
        insertion_layers=(0, 1, 2, 3),
        model_dim=8,
        payload_dim=6,
        attention_dim=8,
        heads=2,
    )
    for adapter in reader.adapters.values():
        nn.init.normal_(adapter.output.weight, std=0.03)
    return reader


def test_candidate_library_is_fixed_and_contains_no_runtime_shortcut() -> None:
    manifest = candidate_manifest()
    ids = [row["candidate_id"] for row in manifest["candidates"]]
    assert ids == [
        "R0-original", "R0-bare", "R0-shuffled",
        "G100", "G075", "G050", "G025",
        "L1", "L2", "L3", "L4",
        "LOO7", "LOO14", "LOO21", "LOO28",
        "C50", "C75", "C90",
        "Q50", "Q75", "Q90", "E-positive",
    ]
    assert manifest["heldout_live_candidate_limit"] == 4
    assert manifest["first37_candidate_limit"] == 2
    assert not manifest["runtime_retrieval"]
    assert not manifest["runtime_per_memory_scoring"]
    assert not manifest["raw_memory_prompt"]
    assert not manifest["learned_or_hard_gate"]


def test_g100_is_exact_original_path_and_preserves_attention() -> None:
    torch.manual_seed(25101)
    model = _Model()
    reader = _reader()
    cloned_model = copy.deepcopy(model)
    cloned_reader = copy.deepcopy(reader)
    hidden = torch.randn(1, 5, 8)
    slots = torch.randn(1, SLOT_COUNT, 6)
    original_hooks = FieldReaderHooks(
        model=model, reader=reader, slots=slots
    )
    with original_hooks:
        original = model(hidden)
    calibrated_hooks = CalibratedFieldReaderHooks(
        model=cloned_model,
        reader=cloned_reader,
        slots=slots,
        layer_scales=(1.0, 1.0, 1.0, 1.0),
    )
    with calibrated_hooks:
        calibrated = cloned_model(hidden)
    assert torch.equal(calibrated, original)
    assert all(
        torch.equal(
            calibrated_hooks.probabilities[layer],
            original_hooks.probabilities[layer],
        )
        for layer in (0, 1, 2, 3)
    )


def test_zero_scale_and_zero_field_reproduce_bare_exactly() -> None:
    torch.manual_seed(25101)
    model = _Model()
    reader = _reader()
    hidden = torch.randn(1, 5, 8)
    bare = model(hidden)
    with CalibratedFieldReaderHooks(
        model=model,
        reader=reader,
        slots=torch.randn(1, SLOT_COUNT, 6),
        layer_scales=(0.0, 0.0, 0.0, 0.0),
    ):
        scale_zero = model(hidden)
    with CalibratedFieldReaderHooks(
        model=model,
        reader=reader,
        slots=torch.zeros(1, SLOT_COUNT, 6),
    ):
        field_zero = model(hidden)
    assert torch.equal(scale_zero, bare)
    assert torch.equal(field_zero, bare)


def test_scaling_and_trust_region_cap_are_deterministic() -> None:
    hidden = torch.ones(2, 3, 8)
    delta = torch.full_like(hidden, 2.0)
    scaled, scaled_stats = calibrate_residual(
        hidden=hidden, delta=delta, scale=0.5
    )
    assert torch.equal(scaled, torch.ones_like(delta))
    assert torch.allclose(
        scaled_stats["ratio_after_cap"], torch.ones(2, 3, 1), atol=2.0e-6
    )
    _, stats = calibrate_residual(
        hidden=hidden, delta=delta, scale=1.0, cap=0.25
    )
    assert float(stats["ratio_after_cap"].max()) == pytest.approx(
        0.25, abs=2.0e-6
    )
    assert bool((stats["cap_multiplier"] < 1.0).all())
    rows = {
        layer: torch.tensor([0.1, 0.2, 0.3])
        for layer in (7, 14, 21, 28)
    }
    caps = derive_layer_caps(rows, 0.5)
    assert all(value == pytest.approx(0.2) for value in caps.values())


def test_pre_rms_confidence_targets_and_default_exact_slots() -> None:
    torch.manual_seed(25101)
    query = torch.randn(960)
    A = torch.randn(960, 8, 256)
    B = torch.randn(8, 256)
    original, audit = read_confidence_field(
        query=query, A=A, B=B, tau=None, nonempty=True
    )
    tau = tau_for_median_confidence(float(audit["raw_rms"]), 0.75)
    changed, changed_audit = read_confidence_field(
        query=query, A=A, B=B, tau=tau, nonempty=True
    )
    assert float(changed_audit["confidence"]) == pytest.approx(
        0.75, abs=1.0e-6
    )
    assert torch.allclose(changed, original * 0.75, atol=2.0e-6)


def _positive_record(
    identity: str, parent: str, seed: int
) -> PositiveFieldRecord:
    generator = torch.Generator().manual_seed(seed)
    return PositiveFieldRecord(
        memory_id=identity,
        parent_id=parent,
        key=torch.randn(960, generator=generator),
        payload=torch.randn(8, 256, generator=generator),
        rho=0.5 if parent == "p1" else 1.0,
    )


def test_positive_field_matches_batch_algebra_and_is_reversible() -> None:
    records = [
        _positive_record("a", "p1", 1),
        _positive_record("b", "p1", 2),
        _positive_record("c", "p2", 3),
    ]
    field = ReversiblePositiveRCMFField()
    for record in records:
        field.add_memory_fast(record)
    keys = torch.stack([record.key for record in records])
    payloads = torch.stack([record.payload for record in records])
    rho = torch.tensor([record.rho for record in records])
    numerator, normalizer = compile_positive_field(
        keys=keys, payloads=payloads, rho=rho
    )
    query = torch.randn(960)
    expected = read_positive_field(
        query=query,
        numerator=numerator,
        normalizer=normalizer,
        nonempty=True,
    )
    assert torch.allclose(field.numerator, numerator, atol=2.0e-5)
    assert torch.allclose(field.normalizer, normalizer, atol=2.0e-5)
    assert torch.allclose(field.read(query), expected, atol=2.0e-5)
    baseline = field.read(query).clone()
    removed = field.remove_parent_fast("p1")
    field.restore_parent_fast(removed)
    assert torch.allclose(field.read(query), baseline, atol=2.0e-5)
    rebuilt_n, rebuilt_z = field.audit_rebuild()
    assert torch.allclose(field.numerator, rebuilt_n, atol=2.0e-5)
    assert torch.allclose(field.normalizer, rebuilt_z, atol=2.0e-5)
    assert field.field_shape == {"N": (960, 8, 256), "Z": (960,)}


def test_positive_fast_read_does_not_scan_records() -> None:
    source = inspect.getsource(ReversiblePositiveRCMFField.read)
    assert "for " not in source
    assert "read_positive_field" in source


def test_unlabeled_calibration_locks_values_without_outcomes() -> None:
    settings = {
        "candidates": {
            "cap_quantiles": {
                "C50": 0.5,
                "C75": 0.75,
                "C90": 0.9,
            },
            "median_confidence_targets": {
                "Q50": 0.5,
                "Q75": 0.75,
                "Q90": 0.9,
            },
        }
    }
    rows = [
        {
            "outcome_used": False,
            "raw_field_rms": float(index + 1),
            "layers": {
                str(layer): {"ratio": 0.01 * (index + 1)}
                for layer in (7, 14, 21, 28)
            },
        }
        for index in range(8)
    ]
    result = derive_unlabeled_calibration(rows, settings)
    assert result["state_count"] == 8
    assert not result["outcomes_used"]
    assert result["locked_before_candidate_outcomes"]
    assert set(result["caps"]) == {"C50", "C75", "C90"}
    assert set(result["taus"]) == {"Q50", "Q75", "Q90"}


def test_critical_gate_requires_family_coverage_and_retained_successes() -> None:
    all_pass = {
        task: True
        for task in (
            "0d01c76_3",
            "325d6ec_2",
            "325d6ec_3",
            "634f342_1",
            "634f342_2",
            "634f342_3",
            "8749218_2",
            "8749218_3",
        )
    }
    assert critical_benefit_gate(all_pass)["passed"]
    all_pass["634f342_1"] = False
    assert critical_benefit_gate(all_pass)["passed"]
    all_pass["634f342_2"] = False
    assert not critical_benefit_gate(all_pass)["passed"]


def test_scaling_does_not_mutate_reader_or_direct_attention() -> None:
    torch.manual_seed(25101)
    model = _Model()
    reader = _reader()
    scaled_model = copy.deepcopy(model)
    scaled_reader = copy.deepcopy(reader)
    before = {
        name: value.detach().clone()
        for name, value in scaled_reader.state_dict().items()
    }
    hidden = torch.randn(1, 5, 8)
    slots = torch.randn(1, SLOT_COUNT, 6)
    original_hooks = CalibratedFieldReaderHooks(
        model=model,
        reader=reader,
        slots=slots,
        layer_scales=(1.0, 1.0, 1.0, 1.0),
    )
    with original_hooks:
        model(hidden)
    scaled_hooks = CalibratedFieldReaderHooks(
        model=scaled_model,
        reader=scaled_reader,
        slots=slots,
        layer_scales=(0.5, 0.5, 0.5, 0.5),
    )
    with scaled_hooks:
        scaled_model(hidden)
    assert torch.equal(
        original_hooks.probabilities[0], scaled_hooks.probabilities[0]
    )
    assert all(
        torch.equal(value, scaled_reader.state_dict()[name])
        for name, value in before.items()
    )


def test_positive_field_insertion_order_replace_and_fixed_shape() -> None:
    records = [
        _positive_record(str(index), f"p{index}", index + 1)
        for index in range(5)
    ]
    first = ReversiblePositiveRCMFField()
    second = ReversiblePositiveRCMFField()
    for record in records:
        first.add_memory_fast(record)
    for record in reversed(records):
        second.add_memory_fast(record)
    assert torch.allclose(first.numerator, second.numerator, atol=2.0e-5)
    assert torch.allclose(first.normalizer, second.normalizer, atol=2.0e-5)
    replacement = _positive_record("replacement", "new-parent", 50)
    first.replace_memory_fast("0", replacement)
    assert "0" not in first.records
    assert "replacement" in first.records
    assert first.field_shape == {"N": (960, 8, 256), "Z": (960,)}


def test_frozen_config_keeps_exp031a_and_prohibits_shortcuts() -> None:
    from rcmf.config import load_config

    cfg = load_config(
        "configs/benchmark/"
        "stage_c_rcmf_benefit_preserving_calibration_9b.yaml"
    )
    frozen = cfg.raw["stage_c_9b"]["frozen_contract"]
    assert cfg.raw["stage_c_9a"]["run_uuid"] == (
        "rcmf_joint_full_bank_9a_20260826_001"
    )
    assert cfg.raw["stage_c_9b"]["global_seed"] == 25101
    assert not frozen["runtime_retrieval"]
    assert not frozen["runtime_top_k"]
    assert not frozen["runtime_per_memory_scoring"]
    assert not frozen["raw_memory_prompt"]
    assert not frozen["memory_gate"]
    assert not frozen["optimizer_steps"]


def test_attempt_ids_detect_duplicate_append_only_entries(tmp_path) -> None:
    import json

    from scripts.prepare_rcmf_benefit_preserving_calibration_9b import (
        _attempt_ids,
    )

    path = tmp_path / "attempts.jsonl"
    rows = [
        {"attempt_id": "attempt-1", "event": "start"},
        {"attempt_id": "attempt-1", "event": "end"},
        {"attempt_id": "attempt-2", "event": "start"},
    ]
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    assert _attempt_ids(path) == {"attempt-1", "attempt-2"}

def test_gain_loss_audit_locks_exactly_fourteen_critical_states() -> None:
    from rcmf.config import load_config

    cfg = load_config(
        "configs/benchmark/"
        "stage_c_rcmf_benefit_preserving_calibration_9b.yaml"
    )
    steps = cfg.raw["stage_c_9b"]["gain_loss_audit"]["critical_steps"]
    assert len(steps) == 14
    assert set(steps) == set(FINDINGS)
    assert sum(row["group"] == "gain" for row in steps.values()) == 6
    assert sum(row["group"] == "loss" for row in steps.values()) == 6
    assert sum(row["group"] == "retained" for row in steps.values()) == 2
    assert all(int(row["d1_critical_step"]) > 0 for row in steps.values())


def test_audit_divergence_and_signed_contribution_helpers() -> None:
    d0 = [{"exact_executed_code": "same"}, {"exact_executed_code": "left"}]
    d1 = [{"exact_executed_code": "same"}, {"exact_executed_code": "right"}]
    assert first_text_divergence(d0, d1) == 2
    rows = [
        {
            "step_id": index,
            "field": {
                "top_memory_contributions": {
                    "ranking": [{"signed_address_weight": value}]
                }
            },
        }
        for index, value in enumerate((-0.4, -0.1, 0.2, -0.3), start=1)
    ]
    result = dominant_sign_audit(rows)
    assert result["has_negative"] and result["has_positive"]
    assert result["sign_change_count"] == 2


def test_gain_loss_audit_git_safe_guard() -> None:
    git_safe_check({"code": "password='<REDACTED:CREDENTIAL:SHA256=abc>'"})
    with pytest.raises(ValueError, match="credential"):
        git_safe_check({"code": "password='not-redacted'"})
    with pytest.raises(ValueError, match="JWT"):
        git_safe_check({"observation": "abcdefgh.ijklmnop.qrstuvwx"})
    redacted = git_safe_redact("login(password='synthetic-value')")
    assert "synthetic-value" not in redacted
    git_safe_check({"code": redacted})
    spaced = git_safe_redact("login(password='one two')")
    assert "one two" not in spaced
    git_safe_check({"code": spaced})
    assert git_safe_findings({"metadata": "token='synthetic-value'"})[0][
        "path"
    ] == "$/metadata"
    nested = git_safe_redact({"metadata": {"token": "synthetic-value"}})
    assert nested["metadata"]["token"].startswith("<REDACTED:")
    git_safe_check(nested)

def test_gain_loss_audit_attempt_ids(tmp_path) -> None:
    path = tmp_path / "attempts.jsonl"
    assert attempt_ids(path) == set()
    path.write_text(
        '{"attempt_id":"audit-1"}\n'
        '{"attempt_id":"audit-2"}\n',
        encoding="utf-8",
    )
    assert attempt_ids(path) == {"audit-1", "audit-2"}
