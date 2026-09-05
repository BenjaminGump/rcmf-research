from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import torch

from rcmf.benchmarks.appworld.paired_causal_runtime_14k import (
    resolve_effective_paired_causal_runtime,
)
from rcmf.benchmarks.appworld.reproducible_stages_14b import (
    formal_stage_output_paths,
)
from rcmf.benchmarks.appworld.reproducible_config_14b import (
    build_arm_runtime_config,
)
from rcmf.config import load_config
from rcmf.model.backends.hf_qwen import HFQwenBackend
from scripts.prepare_appworld_structured_rescue_7hr import _render_and_count
from scripts.run_appworld_train_causal_gate_7hr import _build_manifest
from scripts.run_procedural_causal_audit_7b import _run_condition


def _replay_config() -> dict[str, object]:
    return {
        "stage_c_7b": {
            "legacy": {"executable": "python"},
            "replay": {"subprocess_timeout_seconds": 60},
            "causal_audit": {
                "generation": {
                    "model_name": "Qwen/Qwen3-8B",
                    "dtype": "bfloat16",
                    "device_map": None,
                    "temperature": 0.0,
                    "top_p": 1.0,
                    "do_sample": False,
                    "enable_thinking": False,
                    "max_new_tokens": 1024,
                    "context_limit": 40960,
                    "prompt_profile": "full_demo",
                }
            },
        }
    }


def _arm_config(profile: str) -> dict[str, object]:
    return {
        "benchmark": {"prompt_profile": profile},
        "stage_c_7c": {"generation": {"prompt_profile": profile}},
        "stage_c_7hr": {
            "expected_model_name": "Qwen/Qwen3-8B",
            "appworld": {
                "prompt_profile": profile,
                "context_limit": 40960,
                "temperature": 0.0,
                "top_p": 1.0,
                "do_sample": False,
                "enable_thinking": False,
            },
        },
        "stage_c_9a": {"appworld": {"prompt_profile": profile}},
        "stage_c_11b": {"prompt_profile": profile},
    }


def _resolve(arm_id: str, profile: str) -> tuple[dict[str, object], dict[str, object]]:
    return resolve_effective_paired_causal_runtime(
        replay_config=_replay_config(),
        arm_config=_arm_config(profile),
        arm_id=arm_id,
        arm_config_path=f"arm_{arm_id}.yaml",
        arm_config_sha256="a" * 64,
        replay_config_path="stage_c_replay_clean_rebuild_7b.yaml",
        replay_config_sha256="b" * 64,
    )


def test_effective_three_demo_generation_is_exact_legacy_behavior() -> None:
    source = _replay_config()
    source_before = copy.deepcopy(source)
    effective, provenance = _resolve("3d", "full_demo")
    assert source == source_before
    assert (
        effective["causal_audit"]["generation"]
        == source["stage_c_7b"]["causal_audit"]["generation"]
    )
    assert provenance["changed_execution_fields"] == []
    assert provenance["three_demo_effective_generation_diff"] == 0
    assert (
        provenance["legacy_causal_generation_config_sha256"]
        == provenance["effective_causal_generation_config_sha256"]
    )


def test_effective_one_demo_generation_changes_only_prompt_profile() -> None:
    effective, provenance = _resolve("1d", "full_demo_first_only")
    legacy = _replay_config()["stage_c_7b"]["causal_audit"]["generation"]
    generation = effective["causal_audit"]["generation"]
    assert provenance["changed_execution_fields"] == ["prompt_profile"]
    assert generation["prompt_profile"] == "full_demo_first_only"
    assert {
        key: value for key, value in generation.items() if key != "prompt_profile"
    } == {key: value for key, value in legacy.items() if key != "prompt_profile"}


def test_actual_14j_configs_resolve_only_the_preregistered_prompt_difference(
    tmp_path: Path,
) -> None:
    pipeline = load_config(
        Path("configs/pipeline/rcmf_appworld_repro_14j.yaml")
    ).raw
    for arm in ("3d", "1d"):
        include = Path("configs/pipeline") / str(pipeline["arms"][arm]["include"])
        pipeline["arms"][arm] = load_config(include).raw
    replay = load_config(
        Path("configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml")
    ).raw
    generations = {}
    for arm, profile in (("3d", "full_demo"), ("1d", "full_demo_first_only")):
        resolved = build_arm_runtime_config(pipeline, tmp_path, arm)
        effective, provenance = resolve_effective_paired_causal_runtime(
            replay_config=replay,
            arm_config=resolved,
            arm_id=arm,
            arm_config_path=f"arm_{arm}.yaml",
            arm_config_sha256="a" * 64,
            replay_config_path="replay.yaml",
            replay_config_sha256="b" * 64,
        )
        generations[arm] = effective["causal_audit"]["generation"]
        assert provenance["effective_runtime_prompt_profile"] == profile
    legacy = replay["stage_c_7b"]["causal_audit"]["generation"]
    assert generations["3d"] == legacy
    assert {
        key: value for key, value in generations["1d"].items()
        if key != "prompt_profile"
    } == {key: value for key, value in legacy.items() if key != "prompt_profile"}


@pytest.mark.parametrize("profile", ["", "minimal", "unknown"])
def test_effective_runtime_rejects_missing_or_unknown_profile(profile: str) -> None:
    with pytest.raises(ValueError, match="Missing or unknown"):
        _resolve("1d", profile)


def test_effective_runtime_rejects_profile_source_disagreement() -> None:
    arm = _arm_config("full_demo_first_only")
    arm["stage_c_9a"]["appworld"]["prompt_profile"] = "full_demo"
    with pytest.raises(ValueError, match="sources disagree"):
        resolve_effective_paired_causal_runtime(
            replay_config=_replay_config(),
            arm_config=arm,
            arm_id="1d",
            arm_config_path="arm.yaml",
            arm_config_sha256="a" * 64,
            replay_config_path="replay.yaml",
            replay_config_sha256="b" * 64,
        )


def test_effective_runtime_rejects_shared_generation_disagreement() -> None:
    arm = _arm_config("full_demo_first_only")
    arm["stage_c_7hr"]["appworld"]["context_limit"] = 40959
    with pytest.raises(ValueError, match="context_limit"):
        resolve_effective_paired_causal_runtime(
            replay_config=_replay_config(),
            arm_config=arm,
            arm_id="1d",
            arm_config_path="arm.yaml",
            arm_config_sha256="a" * 64,
            replay_config_path="replay.yaml",
            replay_config_sha256="b" * 64,
        )


class _Tokenizer:
    def __init__(self) -> None:
        self.template_calls: list[dict[str, object]] = []
        self.token_calls: list[dict[str, object]] = []

    def apply_chat_template(self, messages, **kwargs):
        self.template_calls.append(dict(kwargs))
        suffix = "no-thinking" if kwargs.get("enable_thinking") is False else "thinking"
        return f"{messages[0]['content']}::{suffix}"

    def __call__(self, text, **kwargs):
        self.token_calls.append(dict(kwargs))
        ids = list(range(len(text)))
        if kwargs.get("return_tensors") == "pt":
            return {
                "input_ids": torch.tensor([ids], dtype=torch.long),
                "attention_mask": torch.ones((1, len(ids)), dtype=torch.long),
            }
        return {"input_ids": ids}


def test_preflight_count_matches_hf_backend_runtime_contract() -> None:
    tokenizer = _Tokenizer()
    messages = [{"role": "user", "content": "hello"}]
    preflight_text, preflight_count = _render_and_count(tokenizer, messages)
    backend = HFQwenBackend(
        model_name="unused", enable_thinking=False, load_model=False
    )
    backend.tokenizer = tokenizer
    runtime = backend.tokenize_messages(messages, add_generation_prompt=True)
    assert preflight_text == runtime.metadata["text"]
    assert preflight_count == runtime.metadata["input_tokens"]
    assert tokenizer.template_calls[0]["enable_thinking"] is False
    assert tokenizer.token_calls[0]["add_special_tokens"] is True


def test_paired_manifest_seals_runtime_provenance() -> None:
    panel = {"state_ids": ["s1"], "expansion_order": []}
    selections = {
        "s1": {
            "scoreable": True,
            "selected_transition_id": "m1",
            "selected_class_id": "c1",
            "state_task_id": "t1",
            "state_step_id": 1,
            "model_split": "model_train",
        }
    }
    provenance = {
        "arm_id": "1d",
        "arm_resolved_prompt_profile": "full_demo_first_only",
        "effective_runtime_prompt_profile": "full_demo_first_only",
        "effective_causal_generation_config_sha256": "c" * 64,
    }
    manifest = _build_manifest(panel, selections, provenance)
    assert manifest["paired_causal_runtime"] == provenance
    assert len(manifest["manifest_sha256"]) == 64


def test_existing_condition_checkpoint_rejects_runtime_provenance_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "condition.json"
    output.write_text(
        json.dumps(
            {
                "paired_causal_runtime": {
                    "arm_id": "1d",
                    "effective_runtime_prompt_profile": "full_demo",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.run_procedural_causal_audit_7b.validate_condition_checkpoint",
        lambda *args, **kwargs: None,
    )
    with pytest.raises(ValueError, match="runtime provenance differs"):
        _run_condition(
            condition={},
            output_path=output,
            stderr_path=tmp_path / "stderr.log",
            attempt_id="attempt",
            ordinal=1,
            settings={"causal_audit": {"generation": {"model_name": "model"}}},
            config_sha256="config",
            corpus_lineage_sha256="lineage",
            condition_manifest={"manifest_sha256": "manifest"},
            example=None,
            record=None,
            transitions={},
            signatures={},
            raw_utility={},
            backend=None,
            semantic_path=tmp_path / "semantic.py",
            bridge_script=tmp_path / "bridge.py",
            runtime_provenance={
                "arm_id": "1d",
                "effective_runtime_prompt_profile": "full_demo_first_only",
            },
        )


@pytest.mark.parametrize(
    "stage_id,arm", [("D06_paired_causal_outcomes", "3d"), ("O06_paired_causal_outcomes", "1d")]
)
def test_paired_stage_declares_effective_runtime_artifact(
    tmp_path: Path, stage_id: str, arm: str
) -> None:
    paired = tmp_path / f"arms/{arm}/paired_causal"
    paired.mkdir(parents=True)
    for name in (
        "effective_runtime_config.json",
        "condition_manifest.json",
        "paired_outcomes.json",
    ):
        (paired / name).write_text("{}", encoding="utf-8")
    paths = formal_stage_output_paths(stage_id, tmp_path)
    assert (
        tmp_path / f"arms/{arm}/paired_causal/effective_runtime_config.json"
        in paths
    )
