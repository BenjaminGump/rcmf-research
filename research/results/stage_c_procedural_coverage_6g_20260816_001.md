# EXP-023 Full-Transition Procedural Coverage And Split-Semantics Preflight

## Status

- Milestone: 6G / EXP-023.
- Run UUID: `procedural_coverage_6g_20260816_001`.
- Attempt: `exp023-preflight-001`.
- Branch: `research/v4-decision-transition-memory`.
- Experiment source commit:
  `59e1f15b733a3259727b0631265207f0c9354344`.
- Post-run validator fix:
  `3c5ed9171cd9ba3e9882673752846edc09b02fb4`.
- Final record commit: the commit containing this report.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_coverage_6g_20260816_001`.
- Decision branch: `nominal_procedural_coverage_lacks_diversity`.
- The procedural model and one-step audit remain blocked.
- V4 remains a candidate; no V4 tag was created or moved.

## Scope And Immutable Contract

EXP-017, EXP-020, EXP-021, and EXP-022 remained read-only. The audit used all
92 existing query states (74 train / 18 held out) and all 499 transitions from
the 37 training trajectories. The preserved parent split contains 29
train-side parents with 413 transitions and 8 held-out parents with 86
transitions.

The complete Cartesian product has 45,908 pairs. Existing
task/episode/replay/lineage exclusions remove 998 pairs, leaving 44,910 legal
pairs. Token-only preflight marks 43,415 scoreable and 1,495 over context.
Nothing was truncated.

| Cell | Definition | Legal | Scoreable | Over context |
|---|---|---:|---:|---:|
| A | train query / train-parent transition | 29,736 | 28,582 | 1,154 |
| B | held-out query / train-parent transition | 7,434 | 7,112 | 322 |
| C | train query / held-out-parent transition | 6,192 | 6,173 | 19 |
| D | held-out query / held-out-parent transition | 1,548 | 1,548 | 0 |
| E | held-out query / all 37 source parents | 8,982 | 8,660 | 322 |

This milestone trained no model, ran no Qwen forward or generation, created no
AppWorld instance, executed no action, and changed no procedural tier or
threshold.

## Existing 148-Transition Panel

The old panel's scoreable held-out-query spaces reproduce as follows:

| Space | Rows | Tier 0/1/2/3/4 | Tier-3/4 states | Exact-API states |
|---|---:|---|---:|---:|
| B | 2,051 | 1,560 / 284 / 97 / 27 / 83 | 12/18 (66.67%) | 14/18 (77.78%) |
| D | 576 | 422 / 89 / 39 / 6 / 20 | 9/18 (50.00%) | 11/18 (61.11%) |
| E = B union D | 2,627 | 1,982 / 373 / 136 / 33 / 103 | 12/18 (66.67%) | 14/18 (77.78%) |

None of the six original B gaps gains Tier-3/4 coverage from D. Therefore the
old-panel conclusion is `existing_panel_globally_insufficient`, not a failure
caused only by the strict parent split.

| Original gap | Old E max tier | Full B max | Full D max | Full E max |
|---|---:|---:|---:|---:|
| `229360a_1` step 12 | 1 | 4 | 1 | 4 |
| `2a163ab_1` step 13 | 1 | 4 | 2 | 4 |
| `2a163ab_1` step 14 | 2 | 3 | 2 | 3 |
| `771d8fc_3` step 9 | 2 | 4 | 4 | 4 |
| `b0a8eae_1` step 9 | 2 | 4 | 4 | 4 |
| `b0a8eae_1` step 16 | 2 | 2 | 2 | 2 |

The full bank repairs five of the six original gaps. The remaining state asks
for `spotify.show_playlist`; exact API candidates exist, but their ordered
API sequence and argument/control-flow schema do not reach Tier 3.

## Full 499-Transition Coverage

All 499 actions parsed through Python AST with zero fallback and zero
credential leakage. All 148 overlapping old-panel signatures match exactly.

The scoreable tier distribution and state coverage are:

| Space | Tier 0 | Tier 1 | Tier 2 | Tier 3 | Tier 4 | Tier-3/4 states | Exact-API states |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 21,080 | 2,658 | 2,535 | 737 | 1,572 | 70/74 (94.59%) | 73/74 (98.65%) |
| B | 5,424 | 669 | 501 | 85 | 433 | 17/18 (94.44%) | 18/18 (100%) |
| C | 4,530 | 651 | 584 | 99 | 309 | 55/74 (74.32%) | 63/74 (85.14%) |
| D | 1,177 | 151 | 114 | 12 | 94 | 12/18 (66.67%) | 15/18 (83.33%) |
| E | 6,601 | 820 | 615 | 97 | 527 | 17/18 (94.44%) | 18/18 (100%) |

B task coverage is 2/2 for eight held-out tasks and 1/2 for `b0a8eae_1`.
D coverage by the same nine tasks is respectively `1/2, 0/2, 2/2, 2/2,
0/2, 2/2, 1/2, 2/2, 2/2`; E is identical to B at the state-coverage level.

Nominal B coverage clears the unchanged 70% continuity threshold. It is not,
however, a diverse 17-state result under the fixed pre-run operational rule
that a covered state have at least two unique Tier-3/4 signatures and two
source parents:

- B/E diverse Tier-3/4 coverage is only 10/18 (55.56%).
- Seven B states have only one unique high-tier signature.
- Two scoreable B states have high-tier candidates from only one parent/source
  task.
- Six B states obtain high-tier coverage only from API-documentation actions.

The full bank has 150 unique canonical signatures. There are 349 transitions
in 54 duplicate groups; 210/499 (42.08%) are API-documentation transitions.
The largest groups contain 139 `api_docs.show_api_doc`, 51
`api_docs.show_api_descriptions`, 26 `supervisor.complete_task`, 19
`supervisor.show_account_passwords`, and 14 additional
`api_docs.show_api_doc` transitions. The first two span 36 and 33 parents, so
parent diversity alone does not remove signature duplication.

## Context Preflight

Full-bank preflight is exact and tokenizer-only:

- legal / scoreable / over-context: 44,910 / 43,415 / 1,495;
- over-context rate: 3.3289%; truncated pairs: 0;
- no state's only Tier-3/4 candidates are over context;
- combined prompt tokens: min 15,074, median 21,936, p95 38,893.65,
  p99 45,890, max 58,085;
- total tokens including target: median 21,989, p95 38,929.20,
  p99 45,933.82, max 58,112.

One parent, `076f5673-6565-5f20-aada-6f16a0f8d4b0`, accounts for 1,192 of
1,495 over-context pairs (79.73%). Eight transitions are over context for all
90 legal queries. The most affected query state has 175/463 over-context
pairs; none of these missing rows removes its only high-tier candidate.

The prepared 45-state one-step audit has 22,455 legal pairs: 21,624 scoreable
and 831 over context, with no truncation. The planned six base conditions give
270 target slots; 269 are available and one high-tier condition is missing.
Thirty-three optional unseen-parent conditions are available, producing 302
potential Qwen generations, AppWorld reconstructions, and executions. None was
run in EXP-023.

## Runtime, Storage, And Resume Plan

EXP-023 itself used 0 H100 hours. Its CPU/tokenizer wall time was 4,663.52
seconds (1 h 17 min 43.5 s), and the final Lambda artifact occupies
402,387,856 bytes (383.75 MiB).

Measured/prior-run projections for a separately approved EXP-024 are:

| Required phase | Best H100 h | Expected H100 h | Conservative H100 h |
|---|---:|---:|---:|
| 351 new multiview transition representations | 0.085 | 0.099 | 0.122 |
| Field-compatible procedural model | 4.772 | 9.544 | 19.087 |
| 302-condition deterministic one-step audit | 1.846 | 4.194 | 8.389 |
| Required total | 6.702 | 13.837 | 27.599 |

On one H100, projected wall time is approximately the H100-hour total plus
orchestration overhead. The expected required run exceeds the 12-hour review
threshold and therefore requires explicit approval before launch. An optional
expanded prompt cross-encoder would add 13.415 expected H100 hours, for 27.252
expected total.

Projected required artifacts are 2,977,956,875 bytes (2.77 GiB); including the
optional cross-encoder gives 4,253,039,575 bytes (3.96 GiB). The plan uses one
immutable run UUID, append-only attempts, persistent heartbeat, atomic
per-transition representations and pair rows, optimizer-bearing model
checkpoints, and per-state/per-condition AppWorld checkpoints. Reconnects must
inspect tmux/process/heartbeat and validate hashes before any resume.

## Validation And Recovery

The scientific run has exactly one start/end attempt pair, exit code 0,
`normal_completion`, and `scientific_parameter_changed=false`. No disconnect
created a duplicate run. Independent post-run validation passes 24/24 checks,
including source hashes, exact partitions, unique IDs, label/preflight
identity, no truncation, old-panel reproduction, decision reproduction, and
absence of prohibited model/execution artifacts.

The first post-run validator invocation incorrectly treated logical manifest
hash names as filesystem paths. Commit
`3c5ed9171cd9ba3e9882673752846edc09b02fb4` fixed that validator and added a
regression test; the scientific artifact was not rewritten. A shell-formatting
typo left the auxiliary `exit_code.txt` as `0n`; the authoritative append-only
ledger records integer exit code 0 and the heartbeat records `completed`.

The final local suite passed `268 passed, 1 skipped`; the Lambda full suite
passed `268`. Post-validator focused tests passed 14 locally and on Lambda.

## Decision

VERIFIED: the full 499 bank passes the original nominal B coverage threshold
(94.44% versus 70%), and full deployment-space E has the same nominal state
coverage. It does not pass the separately recorded diversity condition:
high-tier coverage is materially concentrated in duplicated signatures and
API-documentation actions.

INFERENCE: running a one-step behavioral claim directly from this bank could
confound reusable procedural content with repeated documentation/action-intent
metadata. The correct branch is therefore
`nominal_procedural_coverage_lacks_diversity`, not
`full_transition_bank_procedural_coverage_passed`.

UNVERIFIED: signature-balanced weighting, equivalence-class treatment, or
additional source trajectories may provide enough genuinely distinct
procedures. These choices require a new preregistered review and must not be
selected from held-out labels.

The next milestone should first decide how duplicate procedural signatures and
API-documentation transitions count in training and behavioral claims. Only
after that review, and explicit approval of the expected 13.837 H100-hour
required run, should an EXP-024 field-prediction/one-step audit be launched.
Behavioral `p(s,m_transition)` remains blocked.

## Artifacts

Key Lambda files are `run_manifest.json`, `attempts.jsonl`, `heartbeat.json`,
`full_transition_signature_manifest.jsonl`,
`full_transition_signature_validation.json`,
`signature_equivalence_groups.json`, `full_pair_preflight.jsonl`,
`full_illegal_pairs.jsonl`, `full_procedural_label_rows.jsonl`,
`one_step_pair_preflight.jsonl`, `one_step_condition_preflight.json`,
`final_exp023_summary.json`, and `postrun_validation.json`.

At final source audit there was no tmux server or experiment Python process;
the H100 was at `0% / 0 MiB` and the persistent NFS mount was healthy.
