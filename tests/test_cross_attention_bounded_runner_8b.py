from __future__ import annotations

import importlib


def test_bounded_runner_dispatches_training_without_changing_evaluation() -> None:
    module = importlib.import_module("scripts.run_cross_attention_reader_8b_v6")
    source = module.__loader__.get_source(module.__name__)
    assert "bounded_checkpoint_reader_forward" in source
    assert "return _ORIGINAL_FORWARD" in source
    assert "track_residual_penalty=False" in source
    assert "track_residual_penalty=True" in source
    assert "save_on_cpu(pin_memory=True)" in source


def test_measured_runtime_gate_precedes_formal_training() -> None:
    module = importlib.import_module(
        "scripts.run_cross_attention_field_8b_after_smoke_v6"
    )
    source = module.__loader__.get_source(module.__name__)
    assert "measured_expected_phase_a_c_h100_hours" in source
    assert "automatic_launch_allowed" in source
    assert "phase1_utilization_bounded" in source
    assert source.index("_measured_runtime_gate(args)") < source.index(
        '"phase1_utilization_bounded"'
    )
