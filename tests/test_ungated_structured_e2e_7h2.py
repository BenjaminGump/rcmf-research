from __future__ import annotations

from pathlib import Path

from rcmf.training.ungated_structured_e2e_7h2 import (
    classify_distribution_shift,
    domain_classifier_audit,
    feature_distribution_report,
    freeze_fresh_test_manifest,
    freeze_transition_shuffle,
)


def test_transition_shuffle_is_deterministic_and_class_distinct() -> None:
    ids = ["m1", "m2", "m3", "m4"]
    classes = {"m1": "c1", "m2": "c1", "m3": "c2", "m4": "c3"}
    first = freeze_transition_shuffle(ids, classes)
    second = freeze_transition_shuffle(ids, classes)
    assert first == second
    assert set(first) == set(ids)
    assert all(source != target for source, target in first.items())
    assert all(classes[source] != classes[target] for source, target in first.items())


def test_feature_audit_reports_shift_without_outcomes() -> None:
    report = feature_distribution_report(
        names=["a", "b"],
        train_values=[[0.0, 0.0], [1.0, 1.0]],
        validation_values=[[0.0, 0.0], [0.2, 0.2], [0.4, 0.4]],
        live_values=[[2.0, 0.0], [2.2, 0.2], [2.4, 0.4]],
        standardizer_mean=[0.5, 0.5],
        standardizer_std=[0.5, 0.5],
    )
    assert report["feature_count"] == 2
    assert report["top_absolute_standardized_mean_difference"][0]["feature"] == "a"
    assert (
        report["top_absolute_standardized_mean_difference"][0][
            "live_out_of_training_range_fraction"
        ]
        == 1.0
    )


def test_domain_classifier_and_shift_classification() -> None:
    validation = [[float(index) / 100.0, 0.0] for index in range(80)]
    live = [[5.0 + float(index) / 100.0, 0.0] for index in range(160)]
    audit = domain_classifier_audit(validation, live)
    assert audit["heldout_auc"] > 0.95
    diagnosis = classify_distribution_shift(
        domain_auc=audit["heldout_auc"],
        feature_rows=[{"absolute_standardized_mean_difference": 2.0}],
    )
    assert diagnosis["classification"] == "broad_feature_state_distribution_shift"


def test_fresh_manifest_does_not_fabricate_unavailable_tasks() -> None:
    all_ids = [f"t{index}" for index in range(40)]
    exposed = {task_id: ["historical-full-bare"] for task_id in all_ids}
    manifest = freeze_fresh_test_manifest(
        all_task_ids=all_ids, exposed=exposed, count=37
    )
    assert manifest["status"] == "insufficient_untouched_tasks"
    assert manifest["selected_task_count"] == 0
    assert manifest["task_ids"] == []


def test_ungated_runner_freezes_model_and_uses_multiplier_one() -> None:
    source = Path("scripts/run_ungated_structured_compiler_first37_7h2.py").read_text(
        encoding="utf-8"
    )
    assert "forced_multiplier = torch.ones" in source
    assert '"student_prompt_contains_raw_transition": False' in source
    assert "freeze_transition_shuffle" in source
    assert "model.parameters()" in source
    assert "requires_grad" in source


def test_gate_audit_never_reads_first37_success_labels() -> None:
    source = Path("scripts/prepare_ungated_structured_e2e_7h2.py").read_text(
        encoding="utf-8"
    )
    assert 'task["success"]' not in source
    assert 'step["gate"]' in source
    assert '"first37_outcomes_used": False' in source
