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


def validate_ownership_inventory(path: str | Path) -> dict[str, Any]:
    rows = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            missing = [field for field in REQUIRED_FIELDS if field not in row]
            if missing:
                raise ValueError(f"ownership row {line_number} is missing {missing}")
            row["classification"] = OwnershipClass(row["classification"]).value
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
    return {
        "entry_count": len(rows),
        "counts_by_class": dict(sorted(Counter(row["classification"] for row in rows).items())),
        "unresolved_reachable_defects": len(unresolved),
        "passed": True,
    }
