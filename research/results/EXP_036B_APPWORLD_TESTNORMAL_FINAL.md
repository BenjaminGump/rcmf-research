# EXP-036B Deterministic AppWorld Test-Normal Evaluation

## Status

STOPPED_BEFORE_FORMAL

EXP-036B repaired the EXP-036A infrastructure determinism failure using only
process-start hash seeding. The final 15-trajectory complete-path smoke passed
exact fresh-process reproduction for all five frozen conditions. The formal
840-trajectory Test-Normal evaluation did not launch because the measured
conservative complete milestone estimate was 50.3493 hours, above the
explicitly approved 42-hour cap.

## Frozen Identities

- Starting commit: 69a218a212f05709ca4c278f1ae14a89b44031a4
- Working branch: research/v5-rcmf-appworld-testnormal-deterministic
- EXP-036A archive branch: archive/exp036a-determinism-stop-69a218a2
- EXP-036A annotated tag: exp036a-determinism-stop-verified-69a218a2
- Run UUID: rcmf_appworld_testnormal_final_13b_20260831_001
- Seed and PYTHONHASHSEED: 25101
- Ordered Test-Normal tasks: 168
- Ordered task-list SHA256:
  990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83
- Prompt: full_demo_first_only
- Initial-message SHA256:
  90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe
- Retained-demo SHA256:
  32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713
- BEST selector/writer-reader/field:
  c7ca61bb... / d11e9d8e... / 5fe48fc2...
- FULL1D selector/writer-reader/field:
  c6e4e2dd... / 357491a6... / f7fb2f87...
- Common shuffle:
  4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170

The full identities and exact Lambda paths are in
exp036b_appworld_testnormal_final/frozen_model_manifest.json.

## Root Cause And Repair

**VERIFIED:** The two archived EXP-036A divergences first appeared in raw
AppWorld observations after identical prompts, generated token IDs, model
responses, and executed code. Each difference consisted only of representation
order for equal Python set values; the surrounding non-set text and
world/evaluator state remained equal.

The Stage-1 repair launches every AppWorld Python process with
PYTHONHASHSEED=25101 before interpreter startup and verifies inheritance in the
execution and legacy AppWorld child interpreters. Two complete conditions on
task 3d9a636_1 were repeated in independent processes:

- B0: 50 steps in A and B, exact equality on every required field.
- FULL1D-S: 26 steps in A and B, exact equality on every required field.

The selected mode is hash_seed_only, logical SHA256
d43d46e46ce8beef7c2b5946c1657b84e94d4a0783b4180f45186beedfc9eb5c.
Set canonicalization is disabled. Raw observations are the exact model-visible
observations, and evaluator state is unchanged.

## Tests

- Pre-smoke local suite: 777 passed, 2 skipped.
- Pre-smoke Lambda suite: 779 passed.
- Final local suite after audit export: 778 passed, 2 skipped.
- Final Lambda suite after audit export: 780 passed.
- Earlier focused 13b suite: 13 passed, 1 skipped.
- Exporter-focused suite: 14 passed, 1 skipped.

The two initial local test invocations failed only because the Conda executable
was absent from PATH, then because the temporary parent directory and
pre-interpreter hash environment were not established. The same suite passed
after invoking the verified environment directly with PYTHONHASHSEED=25101;
no source or scientific setting changed in response.

## Final Smoke

The first two immutable task IDs were 3d9a636_1 and 3d9a636_2. All five
conditions ran on both tasks, and all five conditions on the first task ran
again in separate fresh Python processes: 15/15 trajectories, 575 steps.

Every repeat passed exact equality for:

- world initialization and state fingerprints;
- prompts and prompt token IDs;
- generated token IDs and raw responses;
- extracted, repaired, and executed code;
- raw/model-visible observations and semantic hashes;
- step count and authoritative success;
- query, field, model-package, and reader identities.

Smoke outcomes were non-scientific and did not affect configuration or the
runtime decision.

## Runtime Gate

| Quantity | Hours |
|---|---:|
| Expected formal 840 trajectories | 29.7593 |
| Conservative formal 840 trajectories | 45.0993 |
| Expected efficiency benchmark | 2.5000 |
| Conservative efficiency benchmark | 4.0000 |
| Expected reversibility benchmark | 0.1000 |
| Conservative reversibility benchmark | 0.2500 |
| Expected audit/finalization | 0.5000 |
| Conservative audit/finalization | 1.0000 |
| **Expected complete total** | **32.8593** |
| **Conservative complete total** | **50.3493** |
| Approved cap | 42.0000 |

Projected raw Lambda artifacts were 10,850,533,176 bytes and projected
Git-safe artifacts were 2,170,106,635 bytes. Lambda pricing was not available,
so no cost was fabricated.

The preregistered rule requires a stop when the conservative complete estimate
exceeds 42 hours. No task, condition, max-step setting, log field, or auxiliary
phase was reduced to evade the gate.

## Scientific Results

- Formal Test-Normal: 0/840, NOT_RUN.
- B0, BEST-C, BEST-S, FULL1D-C, FULL1D-S success counts: NOT_RUN.
- Paired gains/losses, bootstrap CIs, McNemar, LOO: NOT_RUN.
- Formal trajectory/deployment metrics: NOT_RUN.
- Efficiency/scaling/TTFT: NOT_RUN.
- Numerical reversibility: NOT_RUN.

Therefore EXP-036B makes no Test-Normal performance claim. The only completed
result is that process-level hash seeding restores exact complete-path
determinism on the preregistered probe and final-smoke cases.

## Attempts And Runtime

- Unique attempts: 22
- Failed attempts: 1
- Open attempts: 0
- Failed attempt: exp036b-smoke-finalize-001
- Failure reason: expected runtime cap enforcement after all smoke rows and
  deterministic comparisons were sealed.
- Probe plus smoke H100-active attempt time: 0.7817 hours.
- Run ledger span: 1.3898 hours.

## Audit And Artifacts

- Raw Lambda root:
  /lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13b_20260831_001
- Git-safe audit:
  research/audits/rcmf_appworld_testnormal_final_13b_20260831_001/index.json
- Audit index logical SHA256:
  a53c597eeb0d445e6bc901d74bb3c4f1fd0602202990b8b78276d33c53ef58f7
- Audit index file SHA256:
  d4d4fe7b793e8559a3b9d66c683123c889014e9ee411a8b72d4f94c17ffa8eb3
- Git-safe audit rows: 15 trajectory files and 575 step rows.
- Secret scan: passed; 0 unredacted JWT/credential findings.

## Interpretation

**VERIFIED:** Hash-seed-only observation rendering passed the required
cross-process and complete-path determinism gates without canonicalization,
model changes, or evaluator changes.

**VERIFIED:** The 42-hour compute authorization blocked formal generation
before any of the 840 rows were launched.

**INFERENCE:** The full frozen evaluation is technically executable and
expected to fit within about 33 hours, but its conservative estimate does not
fit the approved cap.

**UNVERIFIED:** All formal performance, efficiency, scaling, TTFT, serving
state, and numerical reversibility outcomes.

No follow-on experiment was started.
