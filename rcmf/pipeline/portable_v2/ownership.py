from __future__ import annotations

from collections import Counter
from enum import Enum
import json
from pathlib import Path
from typing import Any


class OwnershipClass(str, Enum):
    CORE_MATHEMATICAL_INVARIANT = "CORE_MATHEMATICAL_INVARIANT"
    CONFIGURED_RUN_POLICY = "CONFIGURED_RUN_POLICY"
    DATASET_ADAPTER_FACT = "DATASET_ADAPTER_FACT"
    UPSTREAM_DERIVED_ARTIFACT_VALUE = "UPSTREAM_DERIVED_ARTIFACT_VALUE"
    REPRODUCTION_PROFILE_ONLY = "REPRODUCTION_PROFILE_ONLY"
    EXTERNAL_ENVIRONMENT_DEPENDENCY = "EXTERNAL_ENVIRONMENT_DEPENDENCY"
    LEGACY_UNREACHABLE_HISTORY = "LEGACY_UNREACHABLE_HISTORY"
    DEFECT = "DEFECT"


REQUIRED_FIELDS = (
    "source_file",
    "symbol_or_config_path",
    "current_value_or_pattern",
    "current_consumer",
    "classification",
    "intended_owner",
    "reachable",
    "repaired",
    "validation_evidence",
    "notes",
)


def validate_ownership_inventory(
    path: str | Path, *, repo_root: str | Path | None = None
) -> dict[str, Any]:
    inventory_path = Path(path).resolve()
    root = Path(repo_root).resolve() if repo_root is not None else inventory_path.parents[2]
    rows = []
    missing_evidence: list[str] = []
    with inventory_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [field for field in REQUIRED_FIELDS if field not in row]
            if missing:
                raise ValueError(f"ownership row {line_number} is missing {missing}")
            row["classification"] = OwnershipClass(row["classification"]).value
            source = root / str(row["source_file"])
            if not source.exists():
                missing_evidence.append(str(row["source_file"]))
            evidence = str(row["validation_evidence"])
            for referenced in set(
                __import__("re").findall(
                    r"(?:tests|rcmf|scripts|configs|docs)/[A-Za-z0-9_./-]+\.(?:py|yaml|json|jsonl|md)",
                    evidence,
                )
            ):
                    if not any(root.glob(referenced)):
                        missing_evidence.append(referenced)
            rows.append(row)
    if not rows:
        raise ValueError("ownership inventory is empty")
    unresolved = [
        row
        for row in rows
        if row["classification"] == OwnershipClass.DEFECT.value
        and bool(row["reachable"])
        and not bool(row["repaired"])
    ]
    if unresolved:
        raise RuntimeError(f"ownership inventory has {len(unresolved)} unresolved defects")
    if missing_evidence:
        raise FileNotFoundError(
            f"ownership inventory evidence paths are missing: {sorted(set(missing_evidence))}"
        )
    return {
        "entry_count": len(rows),
        "counts_by_class": dict(sorted(Counter(row["classification"] for row in rows).items())),
        "unresolved_reachable_defects": len(unresolved),
        "missing_evidence_paths": 0,
        "passed": True,
    }
