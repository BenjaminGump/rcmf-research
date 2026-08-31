"""Run EXP-036B through the frozen hash-seed-only observation contract."""

from __future__ import annotations

import os
from pathlib import Path

if os.environ.get("PYTHONHASHSEED") != "25101":
    raise RuntimeError("Launch EXP-036B through the 13b hash-seed launcher")

import _bootstrap  # noqa: E402,F401

from rcmf.config import load_config  # noqa: E402
from rcmf.training.rcmf_appworld_testnormal_deterministic_13b import (  # noqa: E402
    TASK_RESULT_FORMAT,
    assert_hash_seed_process,
    augment_task_row,
    read_mode_manifest,
    validate_formal_manifest,
    write_process_identity,
)
from rcmf.training.state_conditioned_program_7d import canonical_sha256  # noqa: E402
from rcmf.utils.serialization import atomic_write_json  # noqa: E402
import scripts.run_rcmf_appworld_testnormal_final_13a as base  # noqa: E402


def _output_path(kwargs: dict[str, object]) -> Path:
    paths = kwargs["paths"]
    assert isinstance(paths, dict)
    root = Path(paths["root"])
    return (
        root
        / ("smoke_v2" if bool(kwargs["smoke"]) else "conditions")
        / str(kwargs["condition"])
        / "task_results"
        / f"{kwargs['task_id']}.json"
    )


def main() -> None:
    assert_hash_seed_process()
    args = base.parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_13a"]
    deterministic = settings["determinism"]
    mode = read_mode_manifest(args.artifact_dir)
    process = write_process_identity(
        artifact_dir=args.artifact_dir,
        attempt_id=args.attempt_id,
        launcher_path=Path(str(deterministic["launcher_path"])),
        entrypoint_path=Path(__file__),
        legacy_python=Path(str(cfg.raw["stage_c_9a"]["appworld"]["legacy_python"])),
        source_head=args.source_head,
    )
    original = base.run_one
    base.TASK_RESULT_FORMAT = TASK_RESULT_FORMAT

    if args.phase in {"run", "finalize"}:
        condition_manifest = base.read_json(
            args.artifact_dir / "manifests" / "condition_manifest.json"
        )
        validate_formal_manifest(
            artifact_dir=args.artifact_dir,
            condition_manifest=condition_manifest,
            mode=mode,
        )

    def run_one_13b(**kwargs: object):
        row, reused = original(**kwargs)
        if reused:
            content = {key: value for key, value in row.items() if key != "result_sha256"}
            checks = {
                "format": row.get("format") == TASK_RESULT_FORMAT,
                "mode": row.get("determinism", {}).get("mode") == "hash_seed_only",
                "mode_sha": row.get("determinism", {}).get("mode_manifest_sha256")
                == mode["manifest_sha256"],
                "result_sha": row.get("result_sha256") == canonical_sha256(content),
            }
            if not all(checks.values()):
                raise ValueError(f"EXP-036B completed-row determinism differs: {checks}")
            return row, True
        row = augment_task_row(
            row=row,
            backend=kwargs["backend"],
            process_identity=process,
            mode=mode,
            result_format=TASK_RESULT_FORMAT,
        )
        atomic_write_json(_output_path(kwargs), row)
        return row, False

    base.run_one = run_one_13b
    base.main()


if __name__ == "__main__":
    main()
