from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
from pathlib import Path
import re
from typing import Any, Mapping

from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.ownership import validate_ownership_inventory
from rcmf.pipeline.portable_v2.prompts import PromptAssetManifest


BOOTSTRAP_DOCS = (
    "docs/CHATGPT_ENTRYPOINT.md",
    "tasks/alfworld/CHATGPT_CONTEXT.md",
    "tasks/webshop/CHATGPT_CONTEXT.md",
    "docs/CHATGPT_PROJECT_SOURCES.md",
)
PROJECT_SOURCE_FILES = (
    "docs/CHATGPT_ENTRYPOINT.md",
    "docs/PIPELINE.md",
    "docs/SCIENTIFIC_STATUS.md",
    "tasks/alfworld/CHATGPT_CONTEXT.md",
    "tasks/webshop/CHATGPT_CONTEXT.md",
)
REQUIRED_BOOTSTRAP_FIELDS = (
    "Development base records SHA",
    "Canonical executable ancestor SHA",
    "Canonical archive ref",
    "Bootstrap generated at SHA",
    "Last verified UTC",
)
REQUIRED_TEST_FILES = (
    "tests/test_portable_v2_1_contract.py",
    "tests/test_portable_v2_checkpoint_policy.py",
    "tests/test_portable_v2_core_invariants.py",
    "tests/test_portable_v2_appworld_and_boundaries.py",
)


class EvidenceClass(str, Enum):
    MACHINE_COMPUTED = "MACHINE_COMPUTED"
    TEST_BOUND = "TEST_BOUND"
    MANUAL_EVIDENCE_RECORDED = "MANUAL_EVIDENCE_RECORDED"
    NOT_EVALUATED = "NOT_EVALUATED"


@dataclass(frozen=True)
class ReleaseGate:
    value: Any
    evidence_class: EvidenceClass
    evidence: tuple[str, ...]


def _field(text: str, name: str) -> str:
    match = re.search(rf"^- {re.escape(name)}: `([^`]+)`", text, re.MULTILINE)
    if not match:
        raise ValueError(f"bootstrap document is missing {name}")
    return match.group(1)


def _section_bullets(text: str, heading: str) -> tuple[str, ...]:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(?P<body>.*?)(?=^## |\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        raise ValueError(f"missing section: {heading}")
    return tuple(re.findall(r"^- `([^`]+)`\s*$", match.group("body"), re.MULTILINE))


def validate_chatgpt_bootstrap_documents(
    repo_root: str | Path,
    *,
    expected_source_sha: str | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    values: dict[str, dict[str, str]] = {}
    for relative in BOOTSTRAP_DOCS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if "Independently verify the latest pushed GitHub" not in text:
            raise ValueError(f"{relative} lacks independent GitHub verification instruction")
        for marker in ("Sealed primary artifacts and source code", "STATE.md", "HISTORY.md", "Conversation"):
            if marker not in text:
                raise ValueError(f"{relative} has an incomplete authority order")
        values[relative] = {field: _field(text, field) for field in REQUIRED_BOOTSTRAP_FIELDS}
        values[relative]["latest_handoff"] = _field(text, "Latest relevant handoff")
        for sha_field in (
            "Development base records SHA",
            "Canonical executable ancestor SHA",
            "Bootstrap generated at SHA",
        ):
            if not re.fullmatch(r"[0-9a-f]{40}", values[relative][sha_field]):
                raise ValueError(f"{relative} {sha_field} is not a full SHA")
        if not (root / values[relative]["latest_handoff"]).is_file():
            raise FileNotFoundError(values[relative]["latest_handoff"])
        for referenced in _section_bullets(text, "Documents To Read"):
            normalized = referenced.replace("<dataset>", "alfworld")
            if normalized.endswith(".md") and not (root / normalized).is_file():
                raise FileNotFoundError(f"{relative} references missing {referenced}")
    source_values = {row["Canonical executable ancestor SHA"] for row in values.values()}
    if len(source_values) != 1:
        raise ValueError("ChatGPT bootstrap canonical executable ancestor SHAs disagree")
    if expected_source_sha is not None and source_values != {expected_source_sha}:
        raise ValueError("ChatGPT bootstrap differs from expected canonical source SHA")
    project_text = (root / "docs/CHATGPT_PROJECT_SOURCES.md").read_text(encoding="utf-8")
    if _section_bullets(project_text, "Project Source Files") != PROJECT_SOURCE_FILES:
        raise ValueError("ChatGPT project source list is not the exact five-file contract")
    for dataset, display in (("alfworld", "ALFWorld"), ("webshop", "WebShop")):
        context = (root / f"tasks/{dataset}/CHATGPT_CONTEXT.md").read_text(encoding="utf-8")
        state = (root / f"tasks/{dataset}/STATE.md").read_text(encoding="utf-8")
        if _field(context, "Canonical executable ancestor SHA") != _field(
            state, "Canonical executable ancestor SHA"
        ):
            raise ValueError(f"{dataset} state/context canonical source disagreement")
        if not re.search(rf"No scientific\s+{display} RCMF\s+result exists\.", context):
            raise ValueError(f"{dataset} context does not deny a premature scientific result")
    return {
        "bootstrap_document_count": len(BOOTSTRAP_DOCS),
        "project_source_count": len(PROJECT_SOURCE_FILES),
        "canonical_source": next(iter(source_values)),
        "premature_dataset_scientific_results": 0,
        "passed": True,
    }


def _scan_generic_core(root: Path) -> dict[str, list[str]]:
    result = {
        "benchmark_imports": [],
        "benchmark_dispatch": [],
        "version_dispatch": [],
        "historical_counts": [],
        "adapter_fallbacks": [],
    }
    for path in sorted((root / "rcmf/pipeline/portable_v2").glob("*.py")):
        if path.name == "release_validation.py":
            continue
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            location = f"{relative}:{line_number}"
            if (line.startswith("import ") or line.startswith("from ")) and any(
                token in lowered for token in ("appworld", "alfworld", "webshop")
            ):
                result["benchmark_imports"].append(location)
            if re.search(r"benchmark(?:_name)?\s*==\s*['\"](?:appworld|alfworld|webshop)", lowered):
                result["benchmark_dispatch"].append(location)
            if re.search(r"14[bfghijklmn]", lowered):
                result["version_dispatch"].append(location)
            if re.search(r"\b(366|324|401|499|129|300|247)\b", line):
                result["historical_counts"].append(location)
            if "fallback" in lowered and not line.strip().startswith(('"""', "'''")) and not any(
                marker in lowered
                for marker in (
                    "prohibit",
                    "forbid",
                    "no_fallback",
                    "no_checkpoint_fallback",
                    '"fallback_permitted": false',
                )
            ):
                result["adapter_fallbacks"].append(location)
    return result


def validate_portable_v2_release(
    repo_root: str | Path,
    *,
    test_results: Mapping[str, Any] | None = None,
    validate_bootstrap: bool = True,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    scan = _scan_generic_core(root)
    ownership = validate_ownership_inventory(
        root / "docs/audits/PORTABLE_V2_OWNERSHIP_INVENTORY.jsonl",
        repo_root=root,
    )
    config = PortablePipelineConfig.load(root / "configs/pipeline/rcmf_portable_canonical_v2_1.yaml")
    bindings = config.validate_bindings()
    prompt_failures = []
    for manifest in (
        root / "assets/prompts/source_manifests/react_alfworld.json",
        root / "assets/prompts/source_manifests/react_webshop.json",
    ):
        try:
            PromptAssetManifest.load(manifest)
        except Exception:
            prompt_failures.append(str(manifest.relative_to(root)))
    missing_tests = [path for path in REQUIRED_TEST_FILES if not (root / path).is_file()]
    failed_tests = None if test_results is None else int(test_results.get("failed", -1))
    gates = {
        "generic_core_direct_benchmark_imports": ReleaseGate(len(scan["benchmark_imports"]), EvidenceClass.MACHINE_COMPUTED, tuple(scan["benchmark_imports"])),
        "benchmark_name_dispatch": ReleaseGate(len(scan["benchmark_dispatch"]), EvidenceClass.MACHINE_COMPUTED, tuple(scan["benchmark_dispatch"])),
        "version_specific_continuation_dispatch": ReleaseGate(len(scan["version_dispatch"]), EvidenceClass.MACHINE_COMPUTED, tuple(scan["version_dispatch"])),
        "historical_outcome_counts_in_generic_success_conditions": ReleaseGate(len(scan["historical_counts"]), EvidenceClass.MACHINE_COMPUTED, tuple(scan["historical_counts"])),
        "silent_adapter_fallback_paths": ReleaseGate(len(scan["adapter_fallbacks"]), EvidenceClass.MACHINE_COMPUTED, tuple(scan["adapter_fallbacks"])),
        "unresolved_reachable_defects": ReleaseGate(ownership["unresolved_reachable_defects"], EvidenceClass.MACHINE_COMPUTED, ("docs/audits/PORTABLE_V2_OWNERSHIP_INVENTORY.jsonl",)),
        "unowned_runtime_paths": ReleaseGate(ownership["missing_evidence_paths"], EvidenceClass.MACHINE_COMPUTED, ("docs/audits/PORTABLE_V2_OWNERSHIP_INVENTORY.jsonl",)),
        "executor_bindings": ReleaseGate(1 if bindings["passed"] else 0, EvidenceClass.MACHINE_COMPUTED, (str(config.path.relative_to(root)), config.phase_executor_factory)),
        "prompt_assets_without_source_commit_hash_license": ReleaseGate(len(prompt_failures), EvidenceClass.MACHINE_COMPUTED, tuple(prompt_failures)),
        "required_test_files_missing": ReleaseGate(len(missing_tests), EvidenceClass.MACHINE_COMPUTED, tuple(missing_tests)),
        "failed_required_conformance_tests": ReleaseGate(failed_tests, EvidenceClass.TEST_BOUND if failed_tests is not None else EvidenceClass.NOT_EVALUATED, tuple(test_results.get("evidence", ())) if test_results else ()),
    }
    bootstrap = validate_chatgpt_bootstrap_documents(root) if validate_bootstrap else {"passed": False, "status": "NOT_EVALUATED"}
    zero_names = set(gates) - {"executor_bindings", "failed_required_conformance_tests"}
    passed = (
        all(gates[name].value == 0 for name in zero_names)
        and gates["executor_bindings"].value == 1
        and failed_tests == 0
        and bootstrap.get("passed") is True
    )
    return {
        "format": "rcmf_portable_v2_1_release_validation_v1",
        "gates": {
            name: {**asdict(gate), "evidence_class": gate.evidence_class.value}
            for name, gate in sorted(gates.items())
        },
        "ownership": ownership,
        "config_binding": bindings,
        "chatgpt_bootstrap": bootstrap,
        "passed": passed,
    }
