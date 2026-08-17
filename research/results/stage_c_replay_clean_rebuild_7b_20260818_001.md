# EXP-025B Replay-Validated Clean Rebuild and Oracle Causal Audit

## Outcome

- Run UUID: `replay_validated_clean_rebuild_7b_20260818_001`
- Branch: `research/v4-identity-reconciled-corpus`
- Starting SHA: `d74b8e9aa1c15dce1516e5a43e53e20b2970cc38`
- Final implementation/analysis source SHA:
  `38be6cab6d5fae6aa60a9cbd7aeab65014b9f63b`
- Decision: `raw_transition_content_behaviorally_validated_on_clean_corpus`
- Raw transition content behaviorally validated in the oracle one-step audit:
  yes
- Deployable field/program validated: no
- Field/program training remains blocked pending separate review: yes

EXP-025B passed every conditional gate in order: prospective root-login-JWT
semantic replay, clean data-contract freeze, incremental cache rebuild,
condition-manifest/context preflight, full lifecycle smoke, and formal oracle
one-step audit. Qwen3-8B remained frozen and AppWorld execution used the
isolated 0.1.0 capsule. No model was trained.

## Scientific Scope

VERIFIED:

- The replay and live bridge exactly preserve the reconciled query state up to
  the preregistered JWT `exp` equivalence.
- Raw procedural-oracle transitions improve one-step frozen-Qwen behavior on
  the primary clean subset and beat structured metadata and negative controls.

INFERENCE:

- Raw episodic transition content contributes information beyond normalized
  procedural metadata in this oracle one-step setting.

UNVERIFIED:

- No deployable field, selector, program, injector, full trajectory, Stage C2,
  or end-to-end RCMF result follows from this experiment.
- Raw-NLL/outcome association is underpowered because the locked comparator
  covers only 16 selected conditions across 8 states.

## Attempt Ledger

The append-only ledger contains `22` attempts and `44` start/end events. Every
attempt has a terminal event, `scientific_parameter_changed=false`, and there
is no duplicate run UUID or open attempt.

Two failed implementation attempts remain preserved:

- `exp025b-representations-001` exposed panel-row versus source-transition
  count semantics; a regression-tested fix resumed with the same config.
- `exp025b-pair-5d-001` exposed a utility-field reuse bug and stopped before
  completion. Attempt 002 resumed from validated cache state, and attempt 003
  canonicalized 196 unnecessary recomputations back to exact legacy rows.

No laptop or Codex disconnect created a duplicate run.

## Root-Login JWT Contract And Replay

`appworld_observation_semantic_normalization_7b_v1` extends semantic v2 at
exactly one location: observation root `$` for an AST-verified action containing
exactly one `apis.<app>.login(...)` call. Both values must be valid AppWorld
0.1.0 JWT strings, headers and stable claims must match, `exp` must be the only
changed claim, later recorded authenticated calls must accept the actual token,
and no non-token response content may exist.

Adversarial tests reject changed subject, app, username, role/permissions,
algorithm, token type, non-token content, non-login actions, malformed JWTs,
JWT/non-JWT pairs, and arbitrary root timestamps.

| Replay gate, run twice | States | Prior observations | Complete | Exceptions | Non-temporal mismatches |
|---|---:|---:|---:|---:|---:|
| Corrected sentinel | 13/13 | 102/102 | 13/13 | 0 | 0 |
| Root-JWT extension sentinel | 3/3 | 20/20 | 3/3 | 0 | 0 |
| Full reconciled replay | 45/45 | 372/372 | 45/45 | 0 | 0 |

Locked semantic v2 remains `42/45` histories and `369/372` priors on each
full run. The new result does not rewrite any prior replay decision.

## Replay-Validated Lineage

- Structural lineage:
  `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`
- Replay-validated lineage:
  `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Corpus: `46` tasks (`37/9` train/validation), `638` decisions, `638`
  structural transitions; the source bank contains the `499` train
  transitions only.

## Incremental Clean Rebuild

The direct preflight predicted `3,658` invalid Qwen-scoring rows. The final
validated cascade recomputed `3,963`: the extra `305` rows are downstream
teacher conditions whose dependencies changed. This was required propagation,
not an expanded scientific sample.

| Artifact | Total | Reused | Final recomputed | Runtime |
|---|---:|---:|---:|---:|
| State representations | 638 | 603 | 35 | included in 28.147 s representation phase |
| Memory representations | 46 | 44 | 2 | included in 28.147 s representation phase |
| Source transition representations | 638 source / 148 panel output | 144 panel | 17 source / 4 panel | included in 28.147 s representation phase |
| Raw-text teacher | 28,710 | 25,929 | 2,781 | 4,287.140 s |
| Stage-C1 response cache | 638 | 476 | 162 | 296.576 s |
| Pair-5D response cache | 1,728 | 1,404 | 324 | 645.785 s scoring; 23.755 s canonicalization |
| Transition teacher | 4,640 | 3,944 | 696 | 940.422 s |

Productive representation/scoring wall-equivalent time was about `1.721686`
H100 hours. The cache outputs contain `27,054` scoreable and `1,656`
over-context raw-teacher rows, `4,579` scoreable and `61` over-context
transition-teacher rows, `16,786` valid train labels, `4,930` valid validation
labels, and `36` effective train memories.

Validation passed with zero duplicate keys, no truncation, correct leakage
exclusions, reconciled lineage on every recomputed row, byte/tensor identity
for unaffected rows, and no superseded `b0a8eae_3` transition ID.

## Clean Condition Manifest

- Audit states/tasks: `45/9`
- Train-bank transitions/signature classes: `499/150`
- Duplicate transitions/classes: `349/54`
- API-documentation transitions: `210`
- Legal/scoreable/over-context query-transition pairs:
  `22,455 / 21,624 / 831`
- Audit strata A/B/C/D: `9/23/12/1`
- Primary non-documentation high-tier states/tasks: `32/9`
- Conditions: `323`

| Condition | Count |
|---|---:|
| C0 bare | 45 |
| C1 raw procedural oracle | 45 |
| C2 signature-only card | 45 |
| C3 same-intent hard negative | 45 |
| C4 signature popularity | 45 |
| C5 unrelated transition | 45 |
| C6 alternate same-signature exemplar | 37 |
| C7 strict-B oracle | 15 |
| C8 exact-API Tier-2 diagnostic | 1 |

The old/new 323-condition comparison is `312` semantically unchanged and `11`
ID-only changes. Prompt preflight found zero truncation; maximum and mean prompt
lengths were `40,162` and `20,201.52` tokens. Projected generation H100 hours
were `1.7944/4.0375/8.0750` best/expected/conservative, below the 12-hour review
threshold.

## Lifecycle Smoke And Formal Run

The non-scientific smoke completed `4/4` conditions with same-world replay and
execution, same Python namespace, zero exceptions, and a verified replay
variable (`passwords`) surviving into generated code. A simulated interruption
left no partial atomic output; resume, finalizer, validator, and report paths
all passed. Smoke wall/Qwen time was `31.545/11.405` seconds.

The formal run completed `323/323` unique conditions with `323/323` history
replays, same-world executions, and namespace continuities; execution-
infrastructure exceptions and resumed duplicates were zero. Wall time was
`2,534.315` seconds, Qwen generation time was `672.948` seconds
(`0.186930` H100 hours), and the final artifact size was `1,609,057,399`
bytes.

## All-State Condition Results

| Condition | N | Exact API | Action signature | Execution | Normalized observation | Semantic successor |
|---|---:|---:|---:|---:|---:|---:|
| C0 bare | 45 | 0.7333 | 0.3556 | 0.9333 | 0.4551 | 0.4222 |
| C1 raw oracle | 45 | 0.8222 | 0.6222 | 1.0000 | 0.7179 | 0.7111 |
| C2 signature-only | 45 | 0.6889 | 0.4000 | 0.9556 | 0.4249 | 0.3778 |
| C3 hard negative | 45 | 0.7111 | 0.4222 | 0.9556 | 0.4584 | 0.4222 |
| C4 popularity | 45 | 0.7556 | 0.5111 | 0.9778 | 0.6029 | 0.6000 |
| C5 unrelated | 45 | 0.8000 | 0.4667 | 1.0000 | 0.5910 | 0.5556 |
| C6 alternate | 37 | 0.8108 | 0.6486 | 0.9730 | 0.6403 | 0.6216 |

## Primary Non-Documentation Results

These are the preregistered `32` Tier-3/4 non-documentation states.

| Condition | Exact API | Action signature | Execution | Normalized observation | Semantic successor |
|---|---:|---:|---:|---:|---:|
| C0 bare | 0.7813 | 0.3125 | 0.9375 | 0.4417 | 0.4375 |
| C1 raw oracle | 0.9063 | 0.6563 | 1.0000 | 0.8305 | 0.8438 |
| C2 signature-only | 0.6875 | 0.3438 | 0.9375 | 0.4373 | 0.4063 |
| C3 hard negative | 0.7188 | 0.3750 | 0.9688 | 0.4851 | 0.4688 |
| C4 popularity | 0.8125 | 0.5000 | 0.9688 | 0.6662 | 0.6875 |
| C5 unrelated | 0.8125 | 0.3750 | 1.0000 | 0.5911 | 0.5625 |
| C6 alternate (N=25) | 0.9200 | 0.6800 | 0.9600 | 0.7842 | 0.8000 |

## Causal Contrasts

Task-grouped bootstrap 95% confidence intervals:

| Contrast | Exact API delta [CI] | Signature delta [CI] | Execution delta [CI] | Semantic successor delta [CI] |
|---|---|---|---|---|
| C1-C0 | +0.1250 [0.0000, 0.2500] | +0.3438 [0.1563, 0.5278] | +0.0625 [0.0000, 0.1667] | +0.4063 [0.2222, 0.5714] |
| C1-C2 | +0.2188 [0.0882, 0.3529] | +0.3125 [0.1333, 0.4857] | +0.0625 [0.0000, 0.1515] | +0.4375 [0.2758, 0.6000] |
| C1-C3 | +0.1875 [0.0571, 0.3235] | +0.2813 [-0.0286, 0.5455] | +0.0313 [0.0000, 0.1034] | +0.3750 [0.0357, 0.6333] |
| C1-C4 | +0.0938 [0.0000, 0.1852] | +0.1563 [0.0345, 0.2990] | +0.0313 [0.0000, 0.1000] | +0.1563 [0.0000, 0.3030] |
| C1-C5 | +0.0938 [0.0286, 0.1852] | +0.2813 [0.0968, 0.4688] | +0.0000 [0.0000, 0.0000] | +0.2813 [0.1333, 0.4286] |
| C2-C0 | -0.0938 [-0.2286, 0.0000] | +0.0313 [0.0000, 0.1000] | +0.0000 [-0.0882, 0.0968] | -0.0313 [-0.1379, 0.0690] |

C1 has positive relative behavior on `7/9` tasks. The two non-positive tasks
are `2a163ab_1` and `7d7fbf6_2`.

## Documentation And Metadata Controls

The `12` API-documentation-only states do not drive the result. C1-C0 is
`0.0000` exact API, `+0.0833` action signature with CI `[0.0000,0.2500]`,
`0.0000` execution, `-0.0080` normalized observation, and `0.0000` semantic
successor.

On the primary subset, C2 retains only `9.09%` of C1's action-signature gain
over bare and does not retain its API or successor gain. Thus structured
procedural metadata is not sufficient under the preregistered gate.

## Alternate Exemplar Consistency

C1/C6 provides `37` same-signature pairs across all `9` tasks:

- same effect direction versus bare: `0.8649`;
- exact API agreement: `0.9730`;
- execution agreement: `0.9730`;
- effect Pearson/Spearman: `0.8419/0.7911`;
- within-class variance: `0.04254`;
- between-class variance: `0.21325`.

The preregistered 70% direction gate passes. Cross-parent inconsistencies are
retained in the pair-level artifact and are not hidden by the aggregate.

## Clean Raw-NLL Versus Outcome

Only `16` selected conditions across `8` states intersect the locked
148-transition raw-NLL comparator. Within that limited subset:

- raw utility versus semantic-successor effect Pearson/Spearman:
  `0.3164/0.3695`;
- raw utility versus action-signature effect Pearson/Spearman:
  `-0.0145/0.1402`;
- signature-class size versus semantic effect Spearman: `-0.0838`;
- transition token length versus semantic effect Spearman: `0.0620`.

Exact-API and execution effects are constant in this subset, so their
correlations are undefined. These results are descriptive and cannot support a
strong raw-NLL claim.

## Gate And Decision

- Procedural-oracle behavioral gate: passed
- Content-beyond-metadata gate: passed
- Same-signature consistency gate: passed
- API-documentation dominance: false
- Reached branch:
  `raw_transition_content_behaviorally_validated_on_clean_corpus`

The result validates raw transition content as a clean oracle one-step causal
signal. It does not validate a deployable field. The next separately reviewed
milestone should be EXP-025C: signature-class-balanced field prediction with
inverse-frequency weights, API-documentation stratification, separate strict-B
and deployment-E evaluation, and a deployable top-transition one-step audit.

## Reproducibility

- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/replay_clean_rebuild_7b_20260818_001`
- Attempts: `attempts.jsonl`
- Replay: `replay/sentinel_summary.json`, `replay/root_jwt_summary.json`,
  `replay/full_summary.json`
- Frozen contract: `replay_validated_corpus_manifest.json`
- Clean caches: `clean_cache_rebuild/`
- Conditions: `clean_procedural_audit/clean_condition_manifest.json`
- Smoke: `lifecycle_smoke/smoke_summary.json`
- Formal rows: `condition_outputs/`
- Metrics: `one_step_causal_metrics.json`, `causal_comparisons.json`
- Final summary/report: `final_exp025b_summary.json`,
  `final_exp025b_report.md`
- Focused local verification: `46 passed`
- GPU after completion: idle, `0 MiB` allocated and `0%` utilization
- Tmux after completion: `exp025b` alive at an idle shell; no experiment Python
  process remains.
