from __future__ import annotations

from scripts.run_raw_text_teacher_audit3b import compute_expanded_audit_metrics


def _row(
    state: str,
    memory: str,
    utility: float | None,
    *,
    proposed: bool = False,
    sources: list[str] | None = None,
    over_context: bool = False,
) -> dict:
    category = None
    if utility is not None:
        if utility > 0.01:
            category = "positive"
        elif utility < -0.01:
            category = "negative"
        else:
            category = "neutral"
    return {
        "state_example_id": state,
        "task_id": state,
        "episode_id": state,
        "step_id": 1,
        "candidate_memory_id": memory,
        "candidate_memory_index": 0,
        "L0": 1.0,
        "text_utility": utility,
        "utility_category": category,
        "over_context": over_context,
        "is_proposed_candidate": proposed,
        "proposal_sources": sources or [],
    }


def test_expanded_audit_metrics_compute_recall_regret_and_mass() -> None:
    rows = [
        _row("s1", "m_best", 0.50),
        _row("s1", "m_prop1", 0.20, proposed=True, sources=["cosine_top2"]),
        _row("s1", "m_prop2", -0.10, proposed=True, sources=["random_low_similarity"]),
        _row("s1", "m_masked", None, over_context=True),
        _row("s2", "m_hit", 0.30, proposed=True, sources=["same_app"]),
        _row("s2", "m_other", 0.05),
    ]
    proposal_order = {
        "s1": ["m_prop1", "m_prop2"],
        "s2": ["m_hit"],
    }

    metrics = compute_expanded_audit_metrics(rows, proposal_order)
    overall = metrics["overall"]

    assert overall["state_count"] == 2
    assert overall["recall@1"] == 0.5
    assert overall["recall@2"] == 0.5
    assert overall["mean_regret"] == 0.15
    assert metrics["thresholds"][">=0.25"]["state_count"] == 2
    assert metrics["per_state"][0]["over_context_pair_count"] == 1
    assert metrics["source_ablations"]["same_app"]["hit_best_legal_rate"] == 0.5
    assert 0.0 < overall["positive_utility_mass_coverage"] < 1.0
