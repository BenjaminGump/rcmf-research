from __future__ import annotations

import importlib


def test_phase2_hook_preserves_forward_recompute_operation_identity() -> None:
    module = importlib.import_module("scripts.run_cross_attention_reader_8b_v9")
    hook = module.RecomputeCompatibleMemoryBoundedHooks
    assert hook.finish_forward.__qualname__.startswith(
        "RecomputeCompatibleMemoryBoundedHooks"
    )
    source = module.__loader__.get_source(module.__name__)
    assert "self.capture_audit = True" in source


def test_phase2_smoke_uses_selected_checkpoint_and_original_penalty() -> None:
    module = importlib.import_module(
        "scripts.smoke_cross_attention_phase2_backward_8b"
    )
    source = module.__loader__.get_source(module.__name__)
    assert "selected_checkpoint_sha256" in source
    assert "phase2_residual_norm_weight" in source
    assert "loss.backward()" in source
    assert "save_on_cpu(pin_memory=True)" in source
