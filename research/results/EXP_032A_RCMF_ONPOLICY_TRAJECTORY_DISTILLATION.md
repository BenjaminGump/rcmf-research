# EXP-032A: On-Policy Trajectory Union Distillation for Full-Bank RCMF

Date: 2026-08-28 UTC
Run UUID: `rcmf_onpolicy_trajectory_distillation_10a_20260828_001`
Branch: `research/v5-rcmf-onpolicy-trajectory-distillation`
Starting commit: `a6506b55b07d5708e839be4d04a8cd19e2decb91`
Decision: `trajectory_union_distillation_failed_on_heldout`

## Scientific Question

Does offline distillation from complete on-policy bare, correct-field, and
shuffled-field trajectories improve the immutable EXP-031A full-bank RCMF
system while preserving its memory-count-independent deployment contract?

## Frozen Contract

- Global seed: `25101`.
- Frozen Qwen3-8B, tokenizer, selector/query encoder, slot count 8, key
  dimension 960, payload dimension 256, layers `[7,14,21,28]`, field
  algebra, parent-normalized rho, prompt, renderer, AppWorld 0.1.0 harness,
  and 29/8 split.
- EXP-031A checkpoint:
  `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`.
- EXP-031A 499-memory deployment field:
  `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`.
- EXP-031B/C calibration identity:
  `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`.
- No retrieval, top-k, runtime per-memory scoring, hard/learned gate, raw memory
  query text, test-time online training, Q90, L1, or first37 model selection.

## Source Provenance

Archive reference:
`archive/exp031c-q90-full-trajectory-a6506b55`.

Annotated experiment tag:
`exp031c-q90-full-trajectory-verified-a6506b55`.

Source commits, in execution order:

- `81af7e9edef2e39c36be198317eee54552472b36`: EXP-032A charter.
- `87b47d5b8a72f4a25ca7477f0725d633d8552880`: rollout infrastructure.
- `0e771c4eca58ee99465c43216cd22678be41c20c`: union training pipeline.
- `d355aa7a793a468e0becdd7bd5508b8ed65eb073`: cache and training identity.
- `cd612a2e4d05df168ee22d7880e6e2b40f6dfb22`: exact 25% augmentation.
- `794a1a92269be1326167fe6815364203ba760b0d`: audit exporter.
- `a4812aa7f5fba9b90e5fd99ba514b3e126f1d4e5`: formal rollout launcher.
- `51e7483af92f6bfb2578b3d81241d209e5c56880`: frozen rollout/union record.
- `b16a637ba326f1221d03f076cb1bca0f8d791b09`: runtime authorization.
- `adb62ef39ef711f6680de58f1a9fd483147a1c02`: hook-lifetime correction and
  formal training/evaluation source.

GPU rollout conditions used `a4812aa7...`; training and heldout conditions
used `adb62ef3...`.

## Validation

- Preflight verified NVIDIA H100 80GB HBM3, persistent NFS, immutable hashes,
  exact same-task field subtraction, fixed shapes, and no runtime scan.
- Exact legal-field explicit-sum maximum error:
  `1.9073486328125e-06`.
- Complete-task determinism passed twice on `07b42fd_1`: identical prompts,
  tokens, code, observations, step count, and success.
- Focused EXP-032A tests: `19 passed`.
- Complete repository suite: `700 passed, 1 skipped`.
- Python compilation passed.
- Qwen and all prohibited parameters remained frozen at every accepted
  checkpoint.

## Phase A: 87 Complete Train Trajectories

| Condition | Success | Steps | Exceptions | Strict loops | Wall seconds |
|---|---:|---:|---:|---:|---:|
| T0 bare | 15/29 | 504 | 0 | 1 | 2395.998 |
| T1 correct full field | 14/29 | 625 | 0 | 3 | 3276.339 |
| T2 shuffled full field | 18/29 | 634 | 0 | 4 | 3749.708 |

T0 success IDs:

`07b42fd_2, 229360a_2, 27e1026_2, 287e338_1, 287e338_2,
287e338_3, 771d8fc_1, 7d7fbf6_3, 82e2fac_1, aa8502b_1,
aa8502b_2, b7a9ee9_1, cf6abd2_1, cf6abd2_2, cf6abd2_3`.

T1 success IDs:

`07b42fd_2, 07b42fd_3, 229360a_3, 287e338_3, 6104387_3,
771d8fc_2, 7d7fbf6_3, 82e2fac_1, aa8502b_2, b7a9ee9_3,
cf6abd2_1, cf6abd2_2, cf6abd2_3, e7a10f8_2`.

T2 success IDs:

`07b42fd_1, 07b42fd_3, 27e1026_2, 287e338_1, 287e338_2,
287e338_3, 34d9492_1, 76f2c72_3, 771d8fc_2, 7d7fbf6_3,
82e2fac_1, aa8502b_1, aa8502b_2, b7a9ee9_1, b7a9ee9_3,
cf6abd2_1, cf6abd2_2, cf6abd2_3`.

Task classes:

- Bare-only success: 7.
- RCMF-only success: 6.
- Both success: 8.
- Neither success: 8.

## Frozen Trajectory Union

- 483 imitation/training rows.
- 372 on-policy rows.
- 109 bare-preservation states.
- 101 memory-benefit states.
- 111 low-weight clean-replay auxiliary states.
- 3 common-history first-divergence preference pairs.
- 8 strict no-progress loop negatives.
- 494 total training units.
- Bank augmentation: exactly `124/494 = 25%`; each removes 10% of unrelated
  parents under the frozen seed rule.
- Prompt tokens: mean `10746.230`, median `10414`, maximum `28523`.
- Each normalized training group has total weight approximately 1.0.
- Union semantic SHA256:
  `87833cd3f3c16b93c4894119e5e5e088a1ac5e9fb7732220ee99ab5dd3f10803`.
- Training rows SHA256:
  `5082cb19003a6044d2b1577677d376a4c9d99dcf37e8fc94b746b8bace36264c`.
- Teacher cache: 494/494 complete, top-64 sparse policy rows; 483 imitation,
  3 preference, and 8 loop units.

## Training

| Stage | Epoch | Backwards | Mean loss | Trainable reader | Trainable writer | Seconds |
|---|---:|---:|---:|---:|---:|---:|
| Reader-only | 1 | 494 | 0.153050 | 8,388,608 | 0 | 1013.769 |
| Reader-only | 2 | 494 | 0.113576 | 8,388,608 | 0 | 1010.020 |
| Writer+reader | 1 | 494 | 0.101688 | 8,388,608 | 525,312 | 1012.685 |

Reader epoch-1 checkpoint:
`8b22141487f523bc7a3938da3597ce810e3897f01ee595e8015256e749d9e444`.

Reader epoch-2 checkpoint:
`2ed85dbad008d4229fa10729eee70f836fd0311820d6eec98d1fad55b6d0a971`.

Writer+reader checkpoint:
`d1568e43d2541f1761c1a89fa783f66d48a702ffd30951c8e77bbbe0437a1505`.

No accepted checkpoint came from a failed or incomplete attempt.

## Heldout Full-Trajectory Selection

Immutable references on the eight heldout train tasks:

- H0 bare: 3/8, IDs `7d7fbf6_1,b7a9ee9_2,c901732_1`.
- H1 original EXP-031A correct: 5/8, IDs
  `b7a9ee9_2,c901732_1,c901732_3,e7a10f8_1,e7a10f8_3`.
- H2 original EXP-031A shuffle: 3/8, same success IDs as H0.

| Candidate | Correct | Shuffle | Margin | H1 retained | Correct loops | Eligible |
|---|---:|---:|---:|---:|---:|---|
| Reader epoch 1 | 0/8 | 0/8 | 0 | 0/5 | 2 | No |
| Reader epoch 2 | 1/8 | 0/8 | +1 | 1/5 | 3 | No |
| Writer+reader epoch 1 | 0/8 | 2/8 | -2 | 0/5 | 0 | No |

Reader epoch 2 succeeded only on `c901732_1`. The writer+reader shuffled
condition succeeded on `c901732_1,c901732_3`; its correct condition succeeded
on none.

Stage C ran because neither reader checkpoint was eligible. Its compiled
401-memory field SHA256 is
`c9b172b701b8c2d5c65a50f04939f6a1a80c791a75b2170c1fa95c691c5d4b0e`.
The last evaluated reader SHA256 is
`f94f1ade5c895a74712b27aa7f311e451b883a3f5e2b0ea85985a4437b58ccf9`;
the last evaluated writer SHA256 is
`acbe95119a6371f2e416294e3be20bc776b518d4da13f15561fae08c99ff0a1a`.

No candidate satisfied the locked success, correct-over-shuffle,
benefit-retention, and loop gates. Therefore no final candidate was selected,
no 499-memory migration was authorized, and instant recompile/add latency is
not applicable.

## First37

N1/N2 were not run. The preregistered heldout stop fired before first37:

`trajectory_union_distillation_failed_on_heldout`.

The immutable first37 outcomes were not used for model selection or model
modification.

## Attempt Ledger and Runtime

- 27 unique append-only attempts, all closed.
- 25 completed normally.
- Attempt `exp032a-teacher-cache-008` was interrupted after an operator-side
  full-SHA command expansion error; 22 already-written rows were hash-verified
  and reused by `exp032a-teacher-cache-resume-009`.
- Attempt `exp032a-reader-epoch1-010` failed before a checkpoint because the
  reader hook context ended before activation-checkpoint backward
  recomputation. The hook lifetime was corrected without changing scientific
  parameters; `exp032a-reader-epoch1-retry-011` restarted epoch 1 cleanly.
- Attempt span: `2026-08-28T12:04:07Z` to
  `2026-08-28T19:35:07Z`.
- Accounted H100-active time: `5.6175 h`.
- Sum of all attempt durations: `5.6249 h`.
- Scientific wall span: `7.5167 h`.
- Lambda artifact size: `3,051,945,601 bytes`.

All attempt IDs, exact commands, source commits, parents, exits, and stop
reasons are in `attempts.jsonl`.

## Detailed Audit

- Git-safe audit index:
  `research/audits/rcmf_onpolicy_trajectory_distillation_10a_20260828_001/index.json`.
- Raw Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_onpolicy_trajectory_distillation_10a_20260828_001`.
- Git-safe audit: 3,853 step rows across 135 JSONL files and 53 comparison
  reports.
- The model-derived 46,632,089-byte tensor bundle remains Lambda-only under
  repository artifact policy. SHA256:
  `b5dae044b252df1682b9a9cebca28787d443bdd65e8311a8a332d9c8a88077cb`.
- Exact exported tensor path:
  `/lambda/nfs/rcmf-persist/exports/exp032a_v3/audits/rcmf_onpolicy_trajectory_distillation_10a_20260828_001/field_tensors/query_and_slots.pt`.
- Secret verification: 189 text files checked, zero raw JWT matches, 98
  registered sensitive observations, zero leaks.
- The audit result is independently verified because all attempts are closed.

## Deviations

- One union command was initially invoked from the wrong working directory
  before any attempt-ledger row or scientific output; it was rerun from the
  repository root.
- Teacher-cache attempt 008 used a manually expanded incorrect full commit SHA
  and was interrupted. The append-only failure is preserved; no scientific
  parameter changed and 22 atomic rows were hash-verified before reuse.
- Reader attempt 010 exposed a hook-lifetime implementation bug. The correction
  keeps the already-validated EXP-031A reader hooks active through backward
  recomputation and passed the full suite.
- The Windows `apply_patch` helper failed with `helper_unknown_error`.
  Guarded exact UTF-8 PowerShell replacements were used after the failed helper
  call and were verified by tests and diffs.
- No tasks, controls, epochs, logs, memory bank, or heldout condition were
  reduced.

## Interpretation

**VERIFIED:** Complete on-policy trajectory collection and frozen union
construction succeeded. Training loss decreased through both reader epochs and
the one writer+reader epoch, but every candidate failed the preregistered
heldout full-trajectory selection contract.

**INFERENCE:** The tested bounded trajectory-union objective does not overcome
the complete-trajectory deployment failure of this fixed full-bank
architecture. Lower scalar training loss did not translate into retained
heldout task success or reliable correct-over-shuffle behavior.

**UNVERIFIED:** Whether a different architecture or substantially different
training regime could succeed. EXP-032A does not authorize or test that claim.

## Decision

Freeze EXP-032A at
`trajectory_union_distillation_failed_on_heldout`. Do not run first37, start
another architecture, add a gate or retrieval path, introduce post-hoc
calibration, or reduce the paper scope within this task. A separate reviewed
contract is required for any subsequent scientific work.
