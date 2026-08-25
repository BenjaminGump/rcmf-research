from __future__ import annotations

import importlib

from rcmf.training.cross_attention_training_8b import (
    DifferentiableCrossAttentionHooks,
)


def test_training_hook_is_compatible_without_replacing_eval_hook() -> None:
    module = importlib.import_module("scripts.run_cross_attention_reader_8b_v7")
    assert issubclass(
        module.CompatibleMemoryBoundedHooks, DifferentiableCrossAttentionHooks
    )
    source = module.__loader__.get_source(module.__name__)
    assert "base.DifferentiableCrossAttentionHooks =" not in source
    assert "return _ORIGINAL_FORWARD" in source


def test_resume_wrapper_reuses_passed_runtime_gate_and_new_runner() -> None:
    module = importlib.import_module(
        "scripts.run_cross_attention_field_8b_after_smoke_v7"
    )
    source = module.__loader__.get_source(module.__name__)
    assert "exp030a-reader-a4-04-measured-runtime-gate" in source
    assert "scripts/run_cross_attention_reader_8b_v7.py" in source
    assert "automatic_launch_allowed" in source
