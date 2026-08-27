from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401
import torch

from rcmf.config import load_config
from rcmf.training.rcmf_benefit_preserving_calibration_9b import (
    INSERTION_LAYERS,
    derive_layer_caps,
    tau_for_median_confidence,
)
from rcmf.utils.serialization import atomic_write_json, sha256_file


RUNNER_VERSION = "rcmf_benefit_preserving_runner_9b_v1"


def _rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def derive_unlabeled_calibration(
    rows: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]
) -> dict[str, Any]:
    if not rows:
        raise ValueError("Unlabeled heldout profile is empty")
    if any(bool(row.get("outcome_used", False)) for row in rows):
        raise ValueError("Calibration profile may not use labels or outcomes")
    ratio_rows = {
        layer: torch.tensor(
            [
                float(row["layers"][str(layer)]["ratio"])
                for row in rows
            ],
            dtype=torch.float32,
        )
        for layer in INSERTION_LAYERS
    }
    raw_rms = torch.tensor(
        [float(row["raw_field_rms"]) for row in rows],
        dtype=torch.float32,
    )
    if bool((raw_rms <= 0).any()) or not bool(torch.isfinite(raw_rms).all()):
        raise ValueError("Raw-field RMS profile is nonpositive or nonfinite")
    median = float(torch.median(raw_rms))
    caps = {
        name: {
            str(layer): value
            for layer, value in derive_layer_caps(
                ratio_rows, float(quantile)
            ).items()
        }
        for name, quantile in settings["candidates"]["cap_quantiles"].items()
    }
    taus = {
        name: tau_for_median_confidence(median, float(target))
        for name, target in settings["candidates"][
            "median_confidence_targets"
        ].items()
    }
    return {
        "format": RUNNER_VERSION,
        "state_count": len(rows),
        "outcomes_used": False,
        "raw_field_rms": {
            "minimum": float(raw_rms.min()),
            "median": median,
            "maximum": float(raw_rms.max()),
            "standard_deviation": float(raw_rms.std(unbiased=False)),
        },
        "caps": caps,
        "taus": taus,
        "locked_before_candidate_outcomes": True,
    }


def critical_benefit_gate(results: Mapping[str, bool]) -> dict[str, Any]:
    gain_families = {
        "cross_app_import": ("0d01c76_3",),
        "spotify_state_machine": ("325d6ec_2", "325d6ec_3"),
        "exact_set_migration": (
            "634f342_1",
            "634f342_2",
            "634f342_3",
        ),
    }
    retained = ("8749218_2", "8749218_3")
    all_gains = tuple(
        value for rows in gain_families.values() for value in rows
    )
    missing = [
        task for task in (*all_gains, *retained) if task not in results
    ]
    if missing:
        raise ValueError(f"Critical replay outcomes are incomplete: {missing}")
    gain_count = sum(bool(results[task]) for task in all_gains)
    represented = {
        family: any(bool(results[task]) for task in tasks)
        for family, tasks in gain_families.items()
    }
    exact_set_count = sum(
        bool(results[task])
        for task in gain_families["exact_set_migration"]
    )
    passed = (
        gain_count >= 5
        and all(represented.values())
        and exact_set_count >= 2
        and all(bool(results[task]) for task in retained)
    )
    return {
        "passed": passed,
        "gain_count": gain_count,
        "gain_family_represented": represented,
        "exact_set_gain_count": exact_set_count,
        "retained_successes_preserved": all(
            bool(results[task]) for task in retained
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            "configs/benchmark/"
            "stage_c_rcmf_benefit_preserving_calibration_9b.yaml"
        ),
    )
    parser.add_argument(
        "--phase",
        choices=("derive-calibration", "critical-gate"),
        required=True,
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    cfg = load_config(args.config)
    settings = cfg.raw["stage_c_9b"]
    if args.phase == "derive-calibration":
        payload = derive_unlabeled_calibration(_rows(args.input), settings)
    else:
        source = json.loads(args.input.read_text(encoding="utf-8"))
        payload = {
            "format": RUNNER_VERSION,
            "critical_benefit_gate": critical_benefit_gate(
                source["results"]
            ),
        }
    payload["config_sha256"] = sha256_file(args.config)
    atomic_write_json(args.output, payload)
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
