from __future__ import annotations

import json

import _bootstrap  # noqa: F401

import scripts.run_cross_attention_field_8b_after_smoke_v6 as base


_RUN = base._run


def _v7_run(**kwargs):
    if str(kwargs["script"]) == "scripts/run_cross_attention_reader_8b_v6.py":
        kwargs["script"] = "scripts/run_cross_attention_reader_8b_v7.py"
    return _RUN(**kwargs)


def _reuse_measured_runtime_gate(args):
    path = args.artifact_dir / "reader/measured_runtime_gate.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    if not bool(report["automatic_launch_allowed"]):
        raise RuntimeError("Existing measured runtime gate did not pass")
    return "exp030a-reader-a4-04-measured-runtime-gate", report


def main() -> None:
    base._run = _v7_run
    base._measured_runtime_gate = _reuse_measured_runtime_gate
    base.main()


if __name__ == "__main__":
    main()
