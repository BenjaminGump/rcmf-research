# Handoff: EXP-023 Full-Transition Procedural Coverage Preflight

## Status

- Run UUID: `procedural_coverage_6g_20260816_001`.
- Attempt: `exp023-preflight-001` (one start/end pair, normal completion).
- Experiment source: `59e1f15b733a3259727b0631265207f0c9354344`.
- Validator fix: `3c5ed9171cd9ba3e9882673752846edc09b02fb4`.
- Final record commit: the commit containing this handoff.
- Branch: `research/v4-decision-transition-memory`.
- Decision: `nominal_procedural_coverage_lacks_diversity`.
- Behavioral `p(s,m_transition)` and the procedural one-step audit remain
  blocked.

## Verified Result

The immutable 92-query manifest and all 499 transitions produce 45,908
Cartesian pairs, 998 illegal pairs, 44,910 legal pairs, 43,415 scoreable pairs,
and 1,495 masked over-context pairs. Scoreable A/B/C/D are
`28,582/7,112/6,173/1,548`; E has 8,660 rows. No pair was truncated.

Old 148-panel B/D/E Tier-3/4 coverage is `12/18, 9/18, 12/18`; none of the six
old B gaps is repaired by D alone. The full bank repairs five of six and gives
B/E nominal coverage `17/18 = 94.44%`, above the historical 70% gate.

The full bank has 150 canonical signatures, 349 transitions in 54 duplicate
groups, and 210 API-documentation transitions. B has only 10/18 states with at
least two high-tier signatures and two high-tier parents; seven have one
high-tier signature, two have one high-tier parent, and six are covered only by
API-documentation actions. That preregistered diversity diagnosis prevents the
nominal coverage result from unlocking EXP-024.

Exact-API coverage is A/B/C/D/E `73/74, 18/18, 63/74, 15/18, 18/18`.
Tier-3/4 coverage is `70/74, 17/18, 55/74, 12/18, 17/18`.

## Context And Cost

The full over-context rate is 3.3289%; no state's only high-tier candidates
are over context. One parent causes 1,192/1,495 missing pairs. The 45-state
future audit has 21,624/22,455 scoreable pairs and 302 available conditions
including optional unseen-parent controls.

EXP-023 used zero H100 time, ran for 4,663.52 seconds on CPU/tokenizer work,
and produced 402,387,856 bytes. A possible EXP-024 is projected at
`6.702/13.837/27.599` best/expected/conservative H100 hours for required work;
an optional cross-encoder adds 13.415 expected H100 hours. Expected storage is
2.77 GiB required or 3.96 GiB with the optional cross-encoder. The required
expected run exceeds the 12-hour review threshold and needs explicit approval.

## Recovery And Validation

The scientific attempt ledger is append-only and contains one successful
attempt with no parameter change. The initial independent validator had a
logical-key/path interpretation bug; it was fixed and regression tested without
changing artifacts. Final validation passes 24/24 checks. The auxiliary shell
`exit_code.txt` contains cosmetic `0n`, while the authoritative ledger records
integer 0 and the heartbeat records `completed`.

## Recommended Review

Before EXP-024, preregister how canonical-signature duplication and the 42.08%
API-documentation fraction will be represented or weighted without using
held-out labels. Review whether equivalence-class weighting is scientifically
acceptable or whether additional source trajectories are needed. Do not launch
field training, Qwen generation, AppWorld replay, or behavioral program work
until that decision and the >12-hour compute approval are explicit.

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_coverage_6g_20260816_001`.
