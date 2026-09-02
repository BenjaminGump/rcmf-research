from __future__ import annotations

import sys
import json
import os
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

from rcmf.pipeline.adapter import MockBenchmarkAdapter, ReproducibleBenchmarkAdapter
from rcmf.pipeline.audit import redact_record
from rcmf.pipeline.contracts import ArmContract, PipelineContract, StageSpec
from rcmf.pipeline.monitor import MONITOR_INTERVAL_SECONDS, watchdog_capabilities
from rcmf.pipeline.scheduler import EventDrivenScheduler, subprocess_stage_runner
from rcmf.pipeline.stage_graph import (
    FINAL_STAGES,
    ONE_DEMO_STAGES,
    SHARED_STAGES,
    THREE_DEMO_STAGES,
    build_exp037a_stage_graph,
)
from rcmf.pipeline.validators import (
    evaluate_three_demo_reproduction_gate,
    validate_resolved_arm_diff,
)
from rcmf.benchmarks.appworld.reproducible_config_14b import build_arm_runtime_config
from rcmf.benchmarks.appworld.reproducible_stages_14b import _arm_from_stage
from scripts.prepare_rcmf_reproducible_pipeline_14b import (
    _add_teacher_token_metadata,
    load_resolved,
)
from scripts.supervise_rcmf_reproducible_pipeline_14b import (
    RECOVERABLE_PARENT_EXIT_CODES,
)
from rcmf.utils.serialization import atomic_write_json, sha256_file


def _contract(stages: tuple[StageSpec, ...], hard_cap: float = 200.0) -> PipelineContract:
    return PipelineContract(
        schema_version="test",
        run_uuid="test-run",
        source_commit="a" * 40,
        global_seed=25101,
        hard_cap_hours=hard_cap,
        stages=stages,
        arms={
            "3d": ArmContract("3d", "reference", "arms/3d", "test-3d"),
            "1d": ArmContract("1d", "intervention", "arms/1d", "test-1d"),
        },
    )


def _write_stage_output(stage: StageSpec, stage_dir: Path, source_commit: str) -> None:
    payload_path = stage_dir / "payload.json"
    atomic_write_json(payload_path, {"stage": stage.stage_id})
    atomic_write_json(
        stage_dir / "output_manifest.json",
        {
            "format": "test-output",
            "stage_id": stage.stage_id,
            "source_commit": source_commit,
            "passed": True,
            "outputs": [{"path": "payload.json", "sha256": sha256_file(payload_path)}],
        },
    )


def test_stage_graph_is_complete_and_ordered() -> None:
    stages = build_exp037a_stage_graph()
    ids = [stage.stage_id for stage in stages]
    assert ids == [*SHARED_STAGES, *THREE_DEMO_STAGES, *ONE_DEMO_STAGES, *FINAL_STAGES]
    assert len(stages) == 57
    _contract(stages).validate()


def test_generic_core_has_no_benchmark_specific_import_or_parser() -> None:
    root = Path("rcmf/pipeline")
    text = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = ("import appworld", "from appworld", "apis.")
    assert all(token not in text.lower() for token in forbidden)


def test_mock_adapter_satisfies_protocol() -> None:
    adapter = MockBenchmarkAdapter()
    assert isinstance(adapter, ReproducibleBenchmarkAdapter)
    trajectories = list(adapter.load_successful_training_trajectories())
    assert len(trajectories) == 2
    assert list(adapter.extract_transition_records(trajectories[0]))


def test_appworld_adapter_delegates_serialized_decision_state_rendering(
    monkeypatch,
) -> None:
    from rcmf.benchmarks.appworld.pipeline_adapter import (
        AppWorldReproduciblePipelineAdapter,
    )
    from rcmf.training import datasets

    seen: dict[str, object] = {}

    def fake(example, profile):
        seen["example"] = example
        seen["profile"] = profile
        return [{"role": "user", "content": "canonical"}]

    monkeypatch.setattr(datasets, "_appworld_messages_from_example", fake)
    adapter = AppWorldReproduciblePipelineAdapter(
        corpus_root=".", legacy_root="."
    )
    rendered = adapter.render_state(
        {
            "benchmark": "appworld",
            "episode_id": "trace:task",
            "step_id": 1,
            "state_text": "[QUERY] task",
            "target_text": "pass",
            "target_type": "code",
            "candidate_memory_ids": None,
            "metadata": {},
        },
        "full_demo",
    )
    assert rendered == [{"role": "user", "content": "canonical"}]
    assert seen["profile"] == "full_demo"


def test_teacher_token_metadata_is_fresh_and_untruncated() -> None:
    from rcmf.benchmarks.appworld.transitions import transition_teacher_section
    from rcmf.utils.serialization import sha256_text

    class FakeTokenizer:
        name_or_path = "locked-tokenizer"

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def __call__(self, text: str, **kwargs: object) -> dict[str, list[int]]:
            self.calls.append({"text": text, **kwargs})
            return {"input_ids": list(range(len(text.encode("utf-8"))))}

    transition = {
        "transition_id": "transition-1",
        "source_task_goal": "goal",
        "canonical_pre_action_state": "state",
        "complete_action": "action",
        "complete_post_action_observation": "observation",
    }
    tokenizer = FakeTokenizer()
    rows = _add_teacher_token_metadata([transition], tokenizer)
    section = transition_teacher_section(transition)

    assert "teacher_section_tokens" not in transition
    assert rows[0]["teacher_section_tokens"] == len(section.encode("utf-8"))
    assert rows[0]["teacher_section_sha256"] == sha256_text(section)
    assert rows[0]["tokenizer_name_or_path"] == "locked-tokenizer"
    assert tokenizer.calls == [
        {
            "text": section,
            "truncation": False,
            "add_special_tokens": False,
        }
    ]


def test_arm_diff_rejects_scientific_change() -> None:
    common = {"arm_id": "3d", "task_conditioned_prompt_profile": "reference", "epochs": [1, 2]}
    allowed = {**common, "arm_id": "1d", "task_conditioned_prompt_profile": "intervention"}
    assert validate_resolved_arm_diff(common, allowed)["passed"]
    prohibited = {**allowed, "epochs": [1, 2, 3]}
    result = validate_resolved_arm_diff(common, prohibited)
    assert not result["passed"]
    assert result["prohibited_differences"][0]["path"] == "epochs[2]"


def test_reproduction_gate_pass_and_fail_fixtures() -> None:
    tasks = [f"task-{index}" for index in range(6)]
    bare = {task: index < 1 for index, task in enumerate(tasks)}
    shuffled = {task: index < 1 for index, task in enumerate(tasks)}
    correct = {task: index < 3 for index, task in enumerate(tasks)}
    evidence = {
        "structural_checks": {"identity": True, "leakage": True},
        "complete_evaluation": True,
        "deployable_checkpoint_selected": True,
        "infrastructure_exceptions": 0,
        "bare": bare,
        "correct": correct,
        "shuffled": shuffled,
    }
    passed = evaluate_three_demo_reproduction_gate(evidence)
    assert passed["decision"] == "THREE_DEMO_REPRODUCTION_PASS"
    assert passed["continue_to_one_demo"]
    assert passed["LOO_ranges"]["absolute"]["minimum"] > 0
    assert passed["LOO_ranges"]["specificity"]["minimum"] > 0
    failed = evaluate_three_demo_reproduction_gate({**evidence, "correct": bare})
    assert failed["decision"] == "THREE_DEMO_REPRODUCTION_NOT_ESTABLISHED"
    assert not failed["continue_to_one_demo"]
    no_checkpoint = evaluate_three_demo_reproduction_gate(
        {
            **evidence,
            "complete_evaluation": False,
            "deployable_checkpoint_selected": False,
        }
    )
    assert no_checkpoint["decision"] == "THREE_DEMO_REPRODUCTION_NOT_ESTABLISHED"


def test_event_scheduler_immediately_runs_next_and_resumes(tmp_path: Path) -> None:
    stages = (
        StageSpec("S00", "shared", command=("ignored",)),
        StageSpec("S01", "shared", dependencies=("S00",), command=("ignored",)),
    )
    contract = _contract(stages)
    run_root = tmp_path / "run"
    run_root.mkdir()
    atomic_write_json(run_root / "runtime_authorization.json", {"authorized": True, "hard_cap_hours": 200.0})
    called: list[str] = []

    def runner(stage: StageSpec, command: list[str], stage_dir: Path, env: dict[str, str]) -> int:
        del command
        assert env["PYTHONHASHSEED"] == "25101"
        called.append(stage.stage_id)
        _write_stage_output(stage, stage_dir, contract.source_commit)
        return 0

    scheduler = EventDrivenScheduler(
        contract,
        run_root,
        python_executable=sys.executable,
        config_path=tmp_path / "config.json",
        runner=runner,
        heartbeat_interval_seconds=0.01,
    )
    result = scheduler.run()
    assert result.status == "complete"
    assert called == ["S00", "S01"]
    assert all(row["stage_to_next_transition_seconds"] < 1.0 for row in result.transitions)
    called.clear()
    resumed = scheduler.run()
    assert resumed.status == "complete"
    assert called == []


def test_failed_validator_blocks_downstream(tmp_path: Path) -> None:
    stages = (
        StageSpec("S00", "shared", command=("ignored",)),
        StageSpec("S01", "shared", dependencies=("S00",), command=("ignored",)),
    )
    contract = _contract(stages)
    run_root = tmp_path / "run"
    run_root.mkdir()
    atomic_write_json(run_root / "runtime_authorization.json", {"authorized": True, "hard_cap_hours": 200.0})
    called: list[str] = []

    def runner(stage: StageSpec, command: list[str], stage_dir: Path, env: dict[str, str]) -> int:
        del command, env
        called.append(stage.stage_id)
        _write_stage_output(stage, stage_dir, contract.source_commit)
        return 0

    def validator(stage: StageSpec, stage_dir: Path, source_commit: str) -> dict[str, bool]:
        del stage_dir, source_commit
        return {"passed": stage.stage_id != "S00"}

    result = EventDrivenScheduler(
        contract,
        run_root,
        python_executable=sys.executable,
        config_path=tmp_path / "config.json",
        runner=runner,
        validator=validator,
    ).run()
    assert result.failed_stage == "S00"
    assert called == ["S00"]


def test_watchdog_is_read_only_and_exactly_twenty_minutes() -> None:
    capabilities = watchdog_capabilities()
    assert MONITOR_INTERVAL_SECONDS == 20 * 60
    assert not capabilities["can_launch_stages"]
    assert not capabilities["can_acquire_scheduler_lock"]
    assert not capabilities["can_modify_scientific_state"]


def test_secret_redaction_preserves_hash_ledger() -> None:
    redacted, rows = redact_record({"text": "password=highly-secret-value"})
    assert "highly-secret-value" not in redacted["text"]
    assert rows and rows[0]["raw_sha256"]


def test_exact_resolved_arms_differ_only_on_frozen_allowlist(tmp_path: Path) -> None:
    config = load_resolved(Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"))
    arm_3d = build_arm_runtime_config(config, tmp_path / "run", "3d")
    arm_1d = build_arm_runtime_config(config, tmp_path / "run", "1d")
    expected = {
        "benchmark.prompt_profile",
        "experiment.name",
        "stage_c_11b.artifact_dir",
        "stage_c_11b.prompt_profile",
        "stage_c_11b.run_uuid",
        "stage_c_7c.artifact_dir",
        "stage_c_7c.generation.prompt_profile",
        "stage_c_7c.multiview_cache.output_root",
        "stage_c_7c.run_uuid",
        "stage_c_7hr.appworld.prompt_profile",
        "stage_c_7hr.artifact_dir",
        "stage_c_7hr.parent_exp025c",
        "stage_c_7hr.run_uuid",
        "stage_c_9a.appworld.prompt_profile",
        "stage_c_9a.artifact_dir",
        "stage_c_9a.parent_exp025c",
        "stage_c_9a.parent_exp028a",
        "stage_c_9a.prompt_dependent_inputs.outcomes",
        "stage_c_9a.prompt_dependent_inputs.state_cache",
        "stage_c_9a.prompt_dependent_inputs.teacher_cache",
        "stage_c_9a.run_uuid",
    }
    result = validate_resolved_arm_diff(
        arm_3d, arm_1d, allowlist=expected, allowed_prefixes=()
    )
    assert result["passed"]
    assert {row["path"] for row in result["differences"]} == expected
    assert arm_3d["stage_c_7c"]["multiview_cache"][
        "fresh_rebuild_without_old_cache"
    ]
    assert arm_1d["stage_c_7hr"]["fresh_pipeline_mode"]


def test_all_scientific_stage_ids_resolve_to_one_of_two_arms() -> None:
    stages = build_exp037a_stage_graph()
    for stage in stages:
        if stage.stage_id.startswith("D"):
            assert _arm_from_stage(stage.stage_id) == "3d"
        elif stage.stage_id.startswith("O"):
            assert _arm_from_stage(stage.stage_id) == "1d"
        else:
            assert _arm_from_stage(stage.stage_id) is None


def test_scheduler_gate_controls_immediate_one_demo_launch(tmp_path: Path) -> None:
    def run_case(continue_to_one_demo: bool) -> list[str]:
        root = tmp_path / ("pass" if continue_to_one_demo else "stop")
        root.mkdir()
        stages = (
            StageSpec("D22_three_demo_reproduction_gate", "3d", command=("ignored",)),
            StageSpec(
                "O00_state_representations",
                "1d",
                dependencies=("D22_three_demo_reproduction_gate",),
                command=("ignored",),
                conditional_on="D22_three_demo_reproduction_gate",
            ),
            StageSpec(
                "F00_two_arm_paired_analysis",
                "final",
                dependencies=("D22_three_demo_reproduction_gate",),
                command=("ignored",),
            ),
        )
        contract = _contract(stages)
        atomic_write_json(
            root / "runtime_authorization.json",
            {"authorized": True, "hard_cap_hours": 200.0},
        )
        called: list[str] = []

        def runner(stage: StageSpec, command: list[str], stage_dir: Path, env: dict[str, str]) -> int:
            del command, env
            called.append(stage.stage_id)
            if stage.stage_id == "D22_three_demo_reproduction_gate":
                atomic_write_json(
                    stage_dir / "gate.json",
                    {"continue_to_one_demo": continue_to_one_demo},
                )
            _write_stage_output(stage, stage_dir, contract.source_commit)
            return 0

        result = EventDrivenScheduler(
            contract,
            root,
            python_executable=sys.executable,
            config_path=tmp_path / "config.json",
            runner=runner,
        ).run()
        assert result.status == "complete"
        return called

    assert run_case(True) == [
        "D22_three_demo_reproduction_gate",
        "O00_state_representations",
        "F00_two_arm_paired_analysis",
    ]
    assert run_case(False) == [
        "D22_three_demo_reproduction_gate",
        "F00_two_arm_paired_analysis",
    ]


def test_scheduler_hard_cap_survives_parent_restart(tmp_path: Path) -> None:
    stage = StageSpec("S00", "shared", command=("ignored",))
    contract = _contract((stage,))
    root = tmp_path / "run"
    root.mkdir()
    atomic_write_json(
        root / "runtime_authorization.json",
        {
            "authorized": True,
            "hard_cap_hours": 200.0,
            "run_started_utc": (
                datetime.now(timezone.utc) - timedelta(hours=201)
            ).isoformat(),
        },
    )
    called: list[str] = []

    def runner(stage: StageSpec, command: list[str], stage_dir: Path, env: dict[str, str]) -> int:
        del stage, command, stage_dir, env
        called.append("called")
        return 0

    result = EventDrivenScheduler(
        contract,
        root,
        python_executable=sys.executable,
        config_path=tmp_path / "config.json",
        runner=runner,
    ).run()
    assert result.status == "failed"
    assert result.failed_stage == "S00"
    assert called == []


def test_scheduler_retries_only_explicit_recoverable_exit(tmp_path: Path) -> None:
    stage = StageSpec("S00", "shared", command=("ignored",))
    base = _contract((stage,))
    contract = PipelineContract(
        **{
            **base.__dict__,
            "metadata": {
                "maximum_recoverable_attempts_per_stage": 3,
                "recoverable_retry_delay_seconds": 0,
            },
        }
    )
    root = tmp_path / "run"
    root.mkdir()
    atomic_write_json(
        root / "runtime_authorization.json",
        {"authorized": True, "hard_cap_hours": 200.0},
    )
    calls: list[int] = []

    def runner(stage: StageSpec, command: list[str], stage_dir: Path, env: dict[str, str]) -> int:
        del command
        assert float(env["RCMF_PIPELINE_HARD_DEADLINE_EPOCH"]) > time.time()
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            return 75
        _write_stage_output(stage, stage_dir, contract.source_commit)
        return 0

    result = EventDrivenScheduler(
        contract,
        root,
        python_executable=sys.executable,
        config_path=tmp_path / "config.json",
        runner=runner,
    ).run()
    assert result.status == "complete"
    assert calls == [1, 2, 3]


def test_subprocess_stage_runner_enforces_hard_deadline(tmp_path: Path) -> None:
    stage = StageSpec("S00", "shared", command=(sys.executable,))
    stage_dir = tmp_path / "stage"
    stage_dir.mkdir()
    exit_code = subprocess_stage_runner(
        stage,
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stage_dir,
        {
            **os.environ,
            "RCMF_PIPELINE_HARD_DEADLINE_EPOCH": str(time.time() - 1),
        },
    )
    assert exit_code == 124
    assert (stage_dir / "hard_cap_stop.json").exists()


def test_parent_supervisor_recovery_codes_are_narrow() -> None:
    assert RECOVERABLE_PARENT_EXIT_CODES == {-15, -9, 75, 137, 143}


def test_scheduler_closes_orphan_attempt_before_resume(tmp_path: Path) -> None:
    stage = StageSpec("S00", "shared", command=("ignored",))
    contract = _contract((stage,))
    root = tmp_path / "run"
    root.mkdir()
    atomic_write_json(
        root / "runtime_authorization.json",
        {"authorized": True, "hard_cap_hours": 200.0},
    )
    ledger = __import__(
        "rcmf.pipeline.resume", fromlist=["AppendOnlyAttemptLedger"]
    ).AppendOnlyAttemptLedger(root / "attempts.jsonl")
    ledger.open("orphan", {"stage_id": "S00"})

    def runner(stage: StageSpec, command: list[str], stage_dir: Path, env: dict[str, str]) -> int:
        del command, env
        _write_stage_output(stage, stage_dir, contract.source_commit)
        return 0

    result = EventDrivenScheduler(
        contract,
        root,
        python_executable=sys.executable,
        config_path=tmp_path / "config.json",
        runner=runner,
    ).run()
    assert result.status == "complete"
    rows = [
        json.loads(line)
        for line in (root / "attempts.jsonl").read_text().splitlines()
    ]
    orphan_close = [
        row
        for row in rows
        if row["attempt_id"] == "orphan" and row["event"] == "closed"
    ]
    assert orphan_close[0]["status"] == "interrupted"


def test_conditional_authorization_is_frozen_in_config() -> None:
    config = load_resolved(Path("configs/pipeline/rcmf_appworld_repro_14b.yaml"))
    authorization = config["pipeline"]["conditional_runtime_authorization"]
    assert authorization["approved_hard_cap_wall_hours"] == 200
    assert authorization["automatic_three_demo_launch_after_preflight"]
    assert (
        authorization["automatic_one_demo_launch_only_after"]
        == "THREE_DEMO_REPRODUCTION_PASS"
    )
    assert authorization["monitor_is_scheduler"] is False


def test_technical_smoke_covers_preregistered_engineering_path() -> None:
    source = Path(
        "scripts/smoke_rcmf_reproducible_pipeline_14b.py"
    ).read_text(encoding="utf-8")
    required = (
        "full_demo",
        "full_demo_first_only",
        "_transition_readout",
        "train_field_selector",
        "optimizer_updates",
        "paired_causal_generation",
        "policy_teacher_target_score",
        "writer_reader_backward",
        "memory_count\": 401",
        "_run_task(",
        "_scheduler_smoke",
    )
    assert all(token in source for token in required)
