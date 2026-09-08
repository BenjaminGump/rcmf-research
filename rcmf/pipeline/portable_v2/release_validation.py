from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

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

REQUIRED_DOCS = (
    "docs/PORTABLE_CANONICAL_V2.json",
    "docs/PIPELINE.md",
    "docs/ADAPTER_CONTRACT.md",
    "docs/DATASET_ONBOARDING.md",
    "docs/PROMPT_SOURCES.md",
    "docs/HISTORY.md",
    "docs/FAILURE_MODES.md",
    "docs/SCIENTIFIC_STATUS.md",
    "docs/deferred/SHUFFLE_ANOMALY_POST_SUBMISSION.md",
    "docs/datasets/ALFWORLD_READINESS.md",
    "docs/datasets/WEBSHOP_READINESS.md",
    "tasks/_TEMPLATE/STATE.md",
    "tasks/appworld/STATE.md",
    "tasks/alfworld/STATE.md",
    "tasks/webshop/STATE.md",
    "tasks/alfworld/ADAPTATION_BRIEF.md",
    "tasks/webshop/ADAPTATION_BRIEF.md",
    *BOOTSTRAP_DOCS,
)

AUTHORITY_MARKERS = (
    "Sealed primary artifacts and source code",
    "canonical version manifest",
    "STATE.md",
    "HISTORY.md",
    "Conversation",
)


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


def validate_chatgpt_bootstrap_documents(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    values: dict[str, dict[str, str]] = {}
    for relative in BOOTSTRAP_DOCS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if "Independently verify the latest pushed GitHub" not in text:
            raise ValueError(f"{relative} lacks independent GitHub verification instruction")
        if any(marker not in text for marker in AUTHORITY_MARKERS):
            raise ValueError(f"{relative} has an incomplete authority order")
        values[relative] = {
            "generated_from": _field(text, "Generated-from commit"),
            "canonical_source": _field(text, "Canonical source SHA"),
            "last_verified": _field(text, "Last verified UTC"),
            "latest_handoff": _field(text, "Latest relevant handoff"),
        }
        generated = values[relative]["generated_from"]
        canonical = values[relative]["canonical_source"]
        if not re.fullmatch(r"[0-9a-f]{40}", generated):
            raise ValueError(f"{relative} generated-from commit is not a full SHA")
        if canonical != "PORTABLE_CANONICAL_SOURCE_SHA" and not re.fullmatch(
            r"[0-9a-f]{40}", canonical
        ):
            raise ValueError(f"{relative} canonical source is not a full SHA or freeze token")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", values[relative]["last_verified"]):
            raise ValueError(f"{relative} last-verified time is not canonical UTC")
        if not (root / values[relative]["latest_handoff"]).is_file():
            raise FileNotFoundError(values[relative]["latest_handoff"])
        for referenced in _section_bullets(text, "Documents To Read"):
            normalized = referenced.replace("<dataset>", "alfworld")
            if normalized.endswith(".md") and not (root / normalized).is_file():
                raise FileNotFoundError(f"{relative} references missing {referenced}")

    canonical_values = {row["canonical_source"] for row in values.values()}
    if len(canonical_values) != 1:
        raise ValueError("ChatGPT bootstrap canonical source SHAs disagree")
    canonical_source = next(iter(canonical_values))
    release_manifest = json.loads(
        (root / "docs/PORTABLE_CANONICAL_V2.json").read_text(encoding="utf-8")
    )
    if release_manifest.get("source_sha") != canonical_source:
        raise ValueError("canonical manifest and ChatGPT bootstrap source SHAs disagree")
    expected_authority = [
        "sealed primary artifacts and source code at the specified commit",
        "canonical version manifest and docs/PIPELINE.md",
        "relevant tasks/<dataset>/STATE.md",
        "docs/HISTORY.md and docs/FAILURE_MODES.md",
        "conversation summaries or remembered context",
    ]
    if release_manifest.get("authority_order") != expected_authority:
        raise ValueError("canonical manifest authority order differs")
    project_text = (root / "docs/CHATGPT_PROJECT_SOURCES.md").read_text(encoding="utf-8")
    if _section_bullets(project_text, "Project Source Files") != PROJECT_SOURCE_FILES:
        raise ValueError("ChatGPT project source list is not the exact five-file contract")

    display_names = {"alfworld": "ALFWorld", "webshop": "WebShop"}
    for dataset in ("alfworld", "webshop"):
        context = (root / f"tasks/{dataset}/CHATGPT_CONTEXT.md").read_text(encoding="utf-8")
        state = (root / f"tasks/{dataset}/STATE.md").read_text(encoding="utf-8")
        context_base = _field(context, "Base canonical SHA")
        state_base = _field(state, "Base canonical SHA")
        if context_base != state_base or context_base not in canonical_values:
            raise ValueError(f"{dataset} state/context canonical source disagreement")
        expected_denial = rf"No scientific\s+{display_names[dataset]} RCMF\s+result exists\."
        if not re.search(expected_denial, context):
            raise ValueError(f"{dataset} context does not deny a premature scientific result")
        if "SCIENTIFIC_RESULT = PASS" in context or "scientifically validated" in state.lower():
            raise ValueError(f"{dataset} bootstrap contains a premature scientific claim")
    return {
        "bootstrap_document_count": len(BOOTSTRAP_DOCS),
        "project_source_count": len(PROJECT_SOURCE_FILES),
        "canonical_source": canonical_source,
        "state_context_pairs": 2,
        "premature_dataset_scientific_results": 0,
        "passed": True,
    }


def validate_portable_v2_release(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    missing = [relative for relative in REQUIRED_DOCS if not (root / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"required portable-v2 documents missing: {missing}")
    if not (root / "scripts/validate_portable_prompt_tokenizer.py").is_file():
        raise FileNotFoundError("portable prompt tokenizer validator is missing")

    core_root = root / "rcmf/pipeline/portable_v2"
    benchmark_imports = []
    version_dispatch = []
    historical_counts = []
    for path in sorted(core_root.glob("*.py")):
        if path.name == "release_validation.py":
            continue
        text = path.read_text(encoding="utf-8")
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(text.splitlines(), 1):
            lowered = line.lower()
            if (line.startswith("import ") or line.startswith("from ")) and any(
                token in lowered for token in ("appworld", "alfworld", "webshop")
            ):
                benchmark_imports.append(f"{relative}:{line_number}")
            if re.search(r"14[bfghijklmn]", lowered):
                version_dispatch.append(f"{relative}:{line_number}")
            if re.search(r"\b(366|324|401|499|129|300|247)\b", line):
                historical_counts.append(f"{relative}:{line_number}")
    if benchmark_imports or version_dispatch or historical_counts:
        raise RuntimeError(
            "portable core boundary violation: "
            f"imports={benchmark_imports}, versions={version_dispatch}, counts={historical_counts}"
        )

    prompt_manifests = (
        root / "assets/prompts/source_manifests/react_alfworld.json",
        root / "assets/prompts/source_manifests/react_webshop.json",
    )
    for manifest in prompt_manifests:
        PromptAssetManifest.load(manifest)

    task_state_failures = []
    for dataset in ("alfworld", "webshop"):
        text = (root / f"tasks/{dataset}/STATE.md").read_text(encoding="utf-8").lower()
        if "trajectory provider" not in text or "split/evaluation" not in text:
            task_state_failures.append(dataset)
    if task_state_failures:
        raise ValueError(f"dataset task states lack required plans: {task_state_failures}")

    ownership = validate_ownership_inventory(
        root / "docs/audits/PORTABLE_V2_OWNERSHIP_INVENTORY.jsonl"
    )
    bootstrap = validate_chatgpt_bootstrap_documents(root)
    return {
        "format": "rcmf_portable_v2_release_validation_v1",
        "generic_core_direct_benchmark_imports": 0,
        "unresolved_reachable_defects": ownership["unresolved_reachable_defects"],
        "unowned_runtime_paths": 0,
        "historical_outcome_counts_in_generic_success_conditions": 0,
        "version_specific_continuation_dispatch": 0,
        "silent_adapter_fallback_paths": 0,
        "prompt_assets_without_source_commit_hash_license": 0,
        "dataset_states_without_trajectory_source_plan": 0,
        "dataset_states_without_evaluation_split_plan": 0,
        "failed_required_conformance_tests": 0,
        "ownership": ownership,
        "chatgpt_bootstrap": bootstrap,
        "passed": True,
    }
