from __future__ import annotations

import importlib


def test_smoke_wrapper_uses_persistent_forward_and_full_scope_offload() -> None:
    module = importlib.import_module("scripts.smoke_cross_attention_reader_backward_8b_v2")
    source = module.__loader__.get_source(module.__name__)
    assert "persistent_checkpoint_reader_forward" in source
    assert "with torch.autograd.graph.save_on_cpu(pin_memory=True):" in source
    assert "base.main()" in source


def test_training_wrapper_offloads_complete_training_phases() -> None:
    module = importlib.import_module("scripts.run_cross_attention_reader_8b_v5")
    source = module.__loader__.get_source(module.__name__)
    assert "base._forward = persistent_checkpoint_reader_forward" in source
    assert "base._phase1 = _offloaded_phase(base._phase1)" in source
    assert "base._phase2 = _offloaded_phase(base._phase2)" in source
    assert "with torch.autograd.graph.save_on_cpu(pin_memory=True):" in source
