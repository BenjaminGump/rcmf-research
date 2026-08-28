# Structured Handoff: EXP-032A On-Policy Trajectory Union Distillation

Timestamp: 2026-08-28T19:56:49Z
Repository: `BenjaminGump/rcmf-research`
Active branch: `research/v5-rcmf-onpolicy-trajectory-distillation`
Starting SHA: `a6506b55b07d5708e839be4d04a8cd19e2decb91`
Run UUID: `rcmf_onpolicy_trajectory_distillation_10a_20260828_001`
Seed: `25101`
Decision: `trajectory_union_distillation_failed_on_heldout`

## Status

EXP-032A is scientifically complete. The heldout full-trajectory stop gate
fired after both reader-only epochs and the preauthorized writer-last-layer
stage failed eligibility. No model was selected and first37 was not run.
No process should be resumed.

## Immutable Inputs

- EXP-031A checkpoint:
  `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`.
- EXP-031A 499-memory field:
  `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`.
- EXP-031B calibration:
  `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`.
- Archive branch: `archive/exp031c-q90-full-trajectory-a6506b55`.
- Annotated tag:
  `exp031c-q90-full-trajectory-verified-a6506b55`.
- Deployment contract remained retrieval-free, gate-free, online-training-free,
  and memory-count-independent.

## Commits

Source commits:

`81af7e9edef2e39c36be198317eee54552472b36`,
`87b47d5b8a72f4a25ca7477f0725d633d8552880`,
`0e771c4eca58ee99465c43216cd22678be41c20c`,
`d355aa7a793a468e0becdd7bd5508b8ed65eb073`,
`cd612a2e4d05df168ee22d7880e6e2b40f6dfb22`,
`794a1a92269be1326167fe6815364203ba760b0d`,
`a4812aa7f5fba9b90e5fd99ba514b3e126f1d4e5`,
`51e7483af92f6bfb2578b3d81241d209e5c56880`,
`b16a637ba326f1221d03f076cb1bca0f8d791b09`,
`adb62ef39ef711f6680de58f1a9fd483147a1c02`.

GPU execution commits:

- Formal T0/T1/T2: `a4812aa7f5fba9b90e5fd99ba514b3e126f1d4e5`.
- Teacher cache, training, and heldout evaluation:
  `adb62ef39ef711f6680de58f1a9fd483147a1c02`.

The final record commit is the reviewed branch head created after this handoff
and is reported in the final response.

## Tests

- Focused EXP-032A: `19 passed`.
- Full repository: `700 passed, 1 skipped`.
- Python compilation passed.
- Complete-task determinism passed twice.
- Same-task exclusion, explicit field sum, field subtraction, shuffle,
  trainable parameter sets, frozen Qwen, and production complexity checks
  passed.

## Rollouts and Dataset

T0/T1/T2 successes: `15/29`, `14/29`, `18/29`.

Task classes:

- Bare-only: 7.
- RCMF-only: 6.
- Both: 8.
- Neither: 8.

Union:

- 483 training rows; 494 total units.
- 372 on-policy rows.
- 109 preservation states.
- 101 memory-benefit states.
- 111 clean-replay auxiliary states.
- 3 first-divergence preferences.
- 8 strict loop negatives.
- 124/494 exactly augmented units.
- Union SHA256:
  `87833cd3f3c16b93c4894119e5e5e088a1ac5e9fb7732220ee99ab5dd3f10803`.

The exact T0/T1/T2 success IDs and all 87 per-task records are in the report,
rollout manifest, and audit.

## Training and Heldout Results

| Candidate | Mean loss | Correct | Shuffle | Eligible |
|---|---:|---:|---:|---|
| Reader epoch 1 | 0.153050 | 0/8 | 0/8 | No |
| Reader epoch 2 | 0.113576 | 1/8 | 0/8 | No |
| Writer+reader epoch 1 | 0.101688 | 0/8 | 2/8 | No |

Immutable H0/H1/H2 references were `3/8`, `5/8`, and `3/8`.

Trainable counts:

- Reader-only: 8,388,608.
- Writer+reader: 8,388,608 reader plus 525,312 writer.

Last evaluated hashes:

- Checkpoint:
  `d1568e43d2541f1761c1a89fa783f66d48a702ffd30951c8e77bbbe0437a1505`.
- Reader:
  `f94f1ade5c895a74712b27aa7f311e451b883a3f5e2b0ea85985a4437b58ccf9`.
- Writer:
  `acbe95119a6371f2e416294e3be20bc776b518d4da13f15561fae08c99ff0a1a`.
- 401-memory candidate field:
  `c9b172b701b8c2d5c65a50f04939f6a1a80c791a75b2170c1fa95c691c5d4b0e`.

Final selected candidate: none.
Instant 499-memory migration: not applicable.
N1/N2 first37: not run.

## Attempt Ledger

All 27 unique attempts are closed:

`exp032a-preflight-001`,
`exp032a-determinism-002`,
`exp032a-rollout-t0-003`,
`exp032a-rollout-t1-004`,
`exp032a-rollout-t2-005`,
`exp032a-rollout-finalize-006`,
`exp032a-union-007`,
`exp032a-teacher-cache-008`,
`exp032a-teacher-cache-resume-009`,
`exp032a-reader-epoch1-010`,
`exp032a-reader-epoch1-retry-011`,
`exp032a-heldout-reader-e1-prepare-012`,
`exp032a-heldout-reader-e1-ra-013`,
`exp032a-heldout-reader-e1-rs-014`,
`exp032a-heldout-reader-e1-finalize-015`,
`exp032a-reader-epoch2-016`,
`exp032a-heldout-reader-e2-prepare-017`,
`exp032a-heldout-reader-e2-ra-018`,
`exp032a-heldout-reader-e2-rs-019`,
`exp032a-heldout-reader-e2-finalize-020`,
`exp032a-heldout-reader-select-021`,
`exp032a-writer-reader-epoch1-022`,
`exp032a-heldout-writer-e1-prepare-023`,
`exp032a-heldout-writer-e1-wa-024`,
`exp032a-heldout-writer-e1-ws-025`,
`exp032a-heldout-writer-e1-finalize-026`,
`exp032a-heldout-final-select-027`.

Failures preserved:

- `008`: interrupted after an incorrect operator-expanded full SHA; 22 atomic
  rows were verified and reused.
- `010`: activation-checkpoint hook-lifetime mismatch; no checkpoint
  accepted. The implementation was corrected and epoch 1 restarted cleanly
  under `011`.

## Runtime

- Accounted H100-active: `5.6175 h`.
- Sum of attempt durations: `5.6249 h`.
- Wall span: `7.5167 h`.
- Raw Lambda artifact size: `3,051,945,601 bytes`.

## Artifacts

Git-safe:

- `research/results/EXP_032A_RCMF_ONPOLICY_TRAJECTORY_DISTILLATION.md`.
- `research/results/exp032a_rcmf_onpolicy_trajectory_distillation/`.
- `research/audits/rcmf_onpolicy_trajectory_distillation_10a_20260828_001/`.
- This handoff.
- `research/CURRENT_STATE.md`, `research/DECISIONS.md`,
  `research/NEXT_EXPERIMENTS.md`, and `research/experiments.jsonl`.

Lambda:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_onpolicy_trajectory_distillation_10a_20260828_001`.

The audit contains 3,853 Git-safe step rows, 53 comparisons, and a
46,632,089-byte tensor bundle with SHA256
`b5dae044b252df1682b9a9cebca28787d443bdd65e8311a8a332d9c8a88077cb`.
Secret checks found no JWT or sensitive-observation leaks.

## Deviations

- One pre-ledger union invocation used the wrong working directory.
- Teacher attempt 008 used an incorrectly expanded commit SHA.
- Reader attempt 010 exposed and then corrected the hook lifetime bug.
- The Windows `apply_patch` helper failed; guarded exact UTF-8 edits were
  used after failure and verified by diff/tests.
- Scientific coverage and parameters were not reduced or altered.

## Interpretation

**VERIFIED:** Training completed exactly within the bounded reader and
writer-last-layer stages, but no candidate passed heldout full trajectories.

**INFERENCE:** On-policy trajectory-union distillation, as specified here, is
insufficient to recover robust full-bank behavior on the heldout tasks.

**UNVERIFIED:** Whether another architecture or training objective could
succeed.

## Next Action

Freeze the run and return for scientific review. Do not launch first37, a new
architecture, gate, retrieval path, post-hoc calibration, or paper-scope
change under this task. Lambda artifacts must remain preserved; the instance
may be terminated only after final Git/Lambda synchronization and process/GPU
checks.
