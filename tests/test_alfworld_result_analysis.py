from __future__ import annotations

from scripts.analyze_alfworld_paired_results import _mcnemar_exact, _nearest_rank


def test_nearest_rank_percentile_is_declared_and_deterministic() -> None:
    assert _nearest_rank([4, 1, 3, 2], 0.50) == 2.0
    assert _nearest_rank([4, 1, 3, 2], 0.95) == 4.0


def test_exact_mcnemar_handles_ties_and_asymmetric_pairs() -> None:
    assert _mcnemar_exact(0, 0)["p_value"] == 1.0
    result = _mcnemar_exact(5, 0)
    assert result["discordant"] == 5
    assert result["p_value"] == 0.0625
