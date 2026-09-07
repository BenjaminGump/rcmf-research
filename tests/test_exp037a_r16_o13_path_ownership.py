from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

import rcmf.benchmarks.appworld.reproducible_stages_14b as stages
from rcmf.utils.serialization import sha256_file


def _config(state_cache: Path) -> SimpleNamespace:
    return SimpleNamespace(
        raw={
            "stage_c_9a": {
                "prompt_dependent_inputs": {"state_cache": str(state_cache)}
            }
        },
        benchmark=SimpleNamespace(prompt_profile="full_demo_first_only"),
    )


def _write_query_inputs(target: Path, state_cache: Path) -> None:
    source = {
        "ordered_state_ids": ["state-a", "state-b"],
        "state_queries": torch.tensor([[1.0, 2.0], [3.0, 4.0]]),
    }
    states = {
        "ordered_ids": ["state-a", "state-b"],
        "rows": [
            {"state_example_id": "state-a", "task_id": "task-a"},
            {"state_example_id": "state-b", "task_id": "task-b"},
        ],
        "representations": {
            "final_layer": torch.tensor([[10.0, 20.0], [30.0, 40.0]])
        },
    }
    (target / "data").mkdir(parents=True)
    state_cache.parent.mkdir(parents=True, exist_ok=True)
    torch.save(source, target / "data/rcmf_source_cache.pt")
    torch.save(states, state_cache)


def test_resolved_state_cache_honors_configured_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "continuation"
    parent_cache = tmp_path / "sealed-parent/state_multiview.pt"
    parent_cache.parent.mkdir(parents=True)
    parent_cache.write_bytes(b"sealed-parent")
    unrelated = run_root / "arms/1d/representation_cache/multiview/state_multiview.pt"
    unrelated.parent.mkdir(parents=True)
    unrelated.write_bytes(b"unrelated-local")
    monkeypatch.setattr(stages, "load_config", lambda _: _config(parent_cache))

    assert stages._resolved_prompt_dependent_input(
        run_root, "1d", "state_cache"
    ) == parent_cache.resolve()


def test_resolved_state_cache_missing_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.pt"
    monkeypatch.setattr(stages, "load_config", lambda _: _config(missing))
    with pytest.raises(FileNotFoundError, match="Configured prompt-dependent input"):
        stages._resolved_prompt_dependent_input(tmp_path, "1d", "state_cache")


def test_resolved_state_cache_unknown_or_missing_ownership_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        stages,
        "load_config",
        lambda _: SimpleNamespace(raw={"stage_c_9a": {}}),
    )
    with pytest.raises(ValueError, match="ownership is not configured"):
        stages._resolved_prompt_dependent_input(tmp_path, "1d", "state_cache")


def test_query_override_is_path_provenance_invariant(tmp_path: Path) -> None:
    target = tmp_path / "arm"
    parent_cache = tmp_path / "parent/state_multiview.pt"
    local_cache = tmp_path / "local/state_multiview.pt"
    _write_query_inputs(target, parent_cache)
    local_cache.parent.mkdir(parents=True)
    local_cache.write_bytes(parent_cache.read_bytes())
    parent_before = sha256_file(parent_cache)

    parent = stages._heldout_query_overrides(
        target, parent_cache, ["task-a", "task-b"]
    )
    local = stages._heldout_query_overrides(
        target, local_cache, ["task-a", "task-b"]
    )

    assert set(parent) == set(local) == {"task-a", "task-b"}
    for task_id in parent:
        assert torch.equal(parent[task_id][0], local[task_id][0])
        assert torch.equal(parent[task_id][1], local[task_id][1])
    assert torch.equal(parent["task-a"][0], torch.tensor([30.0, 40.0]))
    assert torch.equal(parent["task-a"][1], torch.tensor([3.0, 4.0]))
    assert sha256_file(parent_cache) == parent_before


def test_o13_does_not_require_local_o00_cache_when_configured_parent_is_valid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_root = tmp_path / "continuation"
    target = run_root / "arms/1d"
    parent_cache = tmp_path / "parent/state_multiview.pt"
    parent_cache.parent.mkdir(parents=True)
    parent_cache.write_bytes(b"sealed-parent")
    (target / "data").mkdir(parents=True)
    (target / "data/full_bank_data_manifest.json").write_text(
        json.dumps({"heldout_task_ids": [f"task-{index}" for index in range(8)]}),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    monkeypatch.setattr(stages, "load_config", lambda _: _config(parent_cache))

    def fake_overrides(
        actual_target: Path, actual_cache: Path, task_ids: list[str]
    ) -> dict[str, tuple[torch.Tensor, torch.Tensor]]:
        captured.update(
            target=actual_target, state_cache=actual_cache, task_ids=task_ids
        )
        return {}

    monkeypatch.setattr(stages, "_heldout_query_overrides", fake_overrides)
    monkeypatch.setattr(stages, "_run_task_set", lambda **_: {"passed": True})

    result = stages._heldout_full_trajectories(
        run_root, "1d", "a" * 40, "diagnostic-attempt"
    )

    assert result["complete"] is True
    assert captured["target"] == target
    assert captured["state_cache"] == parent_cache.resolve()
    assert captured["task_ids"] == [f"task-{index}" for index in range(8)]
    assert not (
        target / "representation_cache/multiview/state_multiview.pt"
    ).exists()

