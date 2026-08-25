from __future__ import annotations

import importlib


def test_posttrain_uses_bounded_training_and_original_evaluation() -> None:
    module = importlib.import_module(
        "scripts.validate_cross_attention_reader_posttrain_8b_v5"
    )
    source = module.__loader__.get_source(module.__name__)
    assert "bounded_checkpoint_reader_forward" in source
    assert "return _ORIGINAL_FORWARD" in source
    assert "save_on_cpu(pin_memory=True)" in source


def test_resume_starts_after_verified_phase1_checkpoint() -> None:
    module = importlib.import_module(
        "scripts.run_cross_attention_field_8b_after_phase1_v8"
    )
    source = module.__loader__.get_source(module.__name__)
    assert "selected_checkpoint_sha256" in source
    assert '"phase1_posttrain_validation_bounded"' in source
    assert '"phase2_specificity_bounded"' in source
    assert '"phase1_utilization_bounded"' not in source
