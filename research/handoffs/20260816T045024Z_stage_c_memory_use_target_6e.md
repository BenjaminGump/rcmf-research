# Handoff: EXP-021 Memory-Use Target Audit

## Status

- Completed under run UUID `memory_use_target_6e_20260816_001`.
- Branch: `research/v4-decision-transition-memory`.
- Source/audit commit: `3995b3cfdffdfc700846d2dec928cf5f7574e6fd`.
- Final record commit: the commit containing this handoff.
- Decision branch: `raw_nll_memory_use_target_not_deployably_predictable`.
- Revised target validated: no.
- Behavioral `p(s,m_transition)` remains blocked.
- V4 remains a candidate; no V4 tag was created.

## Immutable Inputs

- EXP-020 queries: 92 = 74 train/37 tasks plus 18 held-out/9 tasks.
- Transition panel/parents: 148/37 with the locked 29/8 parent split.
- Legal/scoreable/over-context: `13,320/13,128/192`.
- A/B/C/D scoreable rows: `8,205/2,051/2,296/576`.
- Historical comparator: frozen-Qwen raw-transition target-NLL utility.

## What Ran

- Reproduced and decomposed the locked raw utility using A-only main effects.
- Audited 192 pairs under locked, canonical-JSON, and compact-tagged teacher
  serializations.
- Extracted deterministic query/transition action signatures.
- Reused and calibrated the train-only state intent probe.
- Built T0-T7 absolute, relative, pairwise, intent, and matched-pair targets.
- Selected one primary target by five-fold task/parent-grouped A-only CV.
- Evaluated frozen architecture families on B/C/D exactly once with state,
  transition, and both-shuffle controls plus task bootstrap intervals.

## What Did Not Run

No behavioral program, additive injector, selector, production transition
field, Qwen behavioral backpropagation, Stage C2, end-to-end RCMF, AppWorld
generation/evaluation, new query state, new transition, demo change, or V4 tag.

## Main Evidence

- Serialization gate passed: `.833893` median Spearman, `.892081` sign
  agreement, `.966667` per-state top-4 overlap.
- Raw utility A variance `.094417`; additive main-effect R2 `.434646`;
  residual variance `.053379`; raw/residual rank `35.879/41.605`.
- Held-out query intent accuracy is `.944444/.833333/.888889` for app/API/
  action type, versus shuffled `.277778/.222222/.500000`.
- A-only CV selected T4 field epoch 120: NDCG@4 `.423183`, gap accuracy
  `.558759`, state/transition gaps `.025350/.009706`.
- T4 field D: NDCG@4 `.433983`, per-state Spearman `.117200`, residual
  Spearman `-.051060`, gap accuracy `.583228`, raw Huber `.306444`.
- Locked transition-only D NDCG@4 is `.480274`. T4 field shuffle drops are
  `.072329/.094174`; transition-shuffle CI includes zero; 4/9 tasks improve.
- T4 cross D NDCG@4 `.384751`; it also fails.
- Oracle/predicted intent D NDCG@4 `.337362/.344384`, both below the locked
  transition-only comparator.

## Corrected Gate Provenance

The first generated report incorrectly used a run-local transition-only mean,
read the wrong transition-bootstrap key, and compared tasks to one global
baseline. The old report/summary are preserved with a
`_pre_locked_transition_repair` suffix. Attempts 003/004 record the failed
schema adapter and successful record-only repair. No prediction, model,
checkpoint, or cache row changed. The old branch
`revised_target_learnable_but_field_factorization_insufficient` is superseded.

## Recovery Ledger

The append-only ledger has 17 rows: one bootstrap failure and eight start/end
pairs. Failures covered path/cell aliases, a post-scoring token field, a slow
scalar pair-loss loop, and the first record adapter. Every attempt has one run
UUID and `scientific_parameter_changed=false`; durable rows were reused and no
network disconnect created a duplicate run.

## Runtime And Validation

- Serialization: `.167312 H100 h` including resume.
- Successful serialization plus models: `2.147676 H100 h`.
- Including interrupted scalar-loss attempt: about `2.504051 H100 h`.
- Artifact size before final record sync: `7,054,264,484` bytes (`6.5698 GiB`).
- Independent validation: 20/20 checks, no errors.
- Focused Lambda regression tests before records: 17 passed.

## Decision

VERIFIED: the raw teacher is serialization-stable and action intent is
learnable. VERIFIED: no revised target produces deployable, field-compatible,
double-held-out state-transition matching under the registered gate.

INFERENCE: raw target-NLL utility should remain a scientific measurement, not
the immediate deployment teacher.

UNVERIFIED next candidates: structural procedural supervision, direct
next-action/API compatibility, and environment/outcome labels, potentially
with genuinely new source tasks. Review and preregister one before additional
scoring. Do not add more states from the same 37 tasks under unchanged raw NLL.

## Artifacts

Root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/memory_use_target_6e_20260816_001`.

Key files: `run_manifest.json`, `attempts.jsonl`,
`locked_raw_utility_decomposition.json`, `action_signatures.jsonl`,
`intent_probe_calibration.json`, `serialization_teacher_cache.jsonl`,
`serialization_robustness.json`, `candidate_target_rows.jsonl`,
`a_only_grouped_cv_selection.json`, `model_audit_summary.json`,
`scientific_gate_repair.json`, and `postrun_validation.json`.

At the pre-record audit no experiment process was active; H100 was
`0% / 0 MiB`. Completed idle tmux sessions remained only for final cleanup.
