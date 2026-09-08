from __future__ import annotations

import json
from pathlib import Path

from rcmf.pipeline.portable_v2.release_validation import (
    PROJECT_SOURCE_FILES,
    validate_chatgpt_bootstrap_documents,
    validate_portable_v2_release,
)


ROOT = Path(__file__).resolve().parents[1]


def test_chatgpt_bootstrap_documents_are_consistent() -> None:
    manifest_path = ROOT / "docs/PORTABLE_CANONICAL_V2_1.json"
    if not manifest_path.is_file():
        manifest_path = ROOT / "docs/PORTABLE_CANONICAL_V2.json"
    manifest = json.loads(manifest_path.read_text())
    result = validate_chatgpt_bootstrap_documents(
        ROOT, expected_source_sha=manifest["source_sha"]
    )
    assert result["passed"] is True
    assert result["project_source_count"] == 5
    assert result["premature_dataset_scientific_results"] == 0
    assert result["canonical_source"] == manifest["source_sha"]
    assert manifest["checkpoint_policy"] == "terminal_completed_epoch"
    assert manifest["new_dataset_scientific_status"] == {
        "alfworld": "NOT_EVALUATED",
        "webshop": "NOT_EVALUATED",
    }


def test_project_sources_are_exactly_the_requested_five() -> None:
    assert PROJECT_SOURCE_FILES == (
        "docs/CHATGPT_ENTRYPOINT.md",
        "docs/PIPELINE.md",
        "docs/SCIENTIFIC_STATUS.md",
        "tasks/alfworld/CHATGPT_CONTEXT.md",
        "tasks/webshop/CHATGPT_CONTEXT.md",
    )


def test_portable_release_quantitative_gates() -> None:
    result = validate_portable_v2_release(
        ROOT,
        test_results={
            "failed": 0,
            "evidence": ["tests/test_portable_v2_release_documents.py"],
        },
    )
    zero_gates = (
        "generic_core_direct_benchmark_imports",
        "benchmark_name_dispatch",
        "unresolved_reachable_defects",
        "unowned_runtime_paths",
        "historical_outcome_counts_in_generic_success_conditions",
        "version_specific_continuation_dispatch",
        "silent_adapter_fallback_paths",
        "prompt_assets_without_source_commit_hash_license",
        "required_test_files_missing",
        "failed_required_conformance_tests",
    )
    assert result["passed"] is True
    gates = result["gates"]
    assert {name: gates[name]["value"] for name in zero_gates} == {
        name: 0 for name in zero_gates
    }
    assert gates["executor_bindings"]["value"] == 1
    assert {gate["evidence_class"] for gate in gates.values()} <= {
        "MACHINE_COMPUTED",
        "TEST_BOUND",
        "MANUAL_EVIDENCE_RECORDED",
        "NOT_EVALUATED",
    }
    assert result["ownership"]["entry_count"] >= 30


def test_prompt_source_manifests_are_machine_readable() -> None:
    for name in ("react_alfworld.json", "react_webshop.json"):
        row = json.loads((ROOT / "assets/prompts/source_manifests" / name).read_text())
        assert len(row["upstream_commit"]) == 40
        assert len(row["local_sha256"]) == 64
        assert row["license"] == "MIT"


def test_frozen_tokenizer_validator_is_a_no_generation_entrypoint() -> None:
    text = (ROOT / "scripts/validate_portable_prompt_tokenizer.py").read_text()
    assert "load_model=False" in text
    assert '"generation_executed": False' in text
    assert ".generate(" not in text
