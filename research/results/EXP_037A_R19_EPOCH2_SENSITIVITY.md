# EXP-037A-R19 Epoch-2 Forced-Checkpoint Sensitivity Diagnostic

Date (UTC): 2026-09-08

Decision: `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`

This is a post-hoc checkpoint-sensitivity diagnostic. It does not replace the
formal 14n result, whose selected checkpoint remains epoch 1.

## Identity and scope

- Formal source: `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`
- Formal run: `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`
- Formal root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`
- Diagnostic source: `2a7f371f378eab42e42629a4fd5275e18b2814ce`
- Diagnostic root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r19_epoch2_forced_dev_20260908_001`
- Global seed: `25101`
- Hardware: NVIDIA H100 80GB HBM3
- Diagnostic hard cap: 10 wall-clock hours
- Approximate end-to-end diagnostic wall span: 4.2 hours
- Measured field/evaluation stage-wall sum: 3.79 hours
- Backward passes: `0`
- Optimizer steps: `0`

The diagnostic used the strict-valid epoch-2 checkpoint without retraining,
reconstructed fresh diagnostic fields, and ran two fresh official-dev
conditions. It did not modify formal checkpoint selection, formal fields, or
formal task outputs.

## Verified formal state

- All `18/18` formal 14n stages pass the real strict completion validator.
- Epoch-1 checkpoint SHA256:
  `c3bc33f38dd0ea58e368c38f582775c355902bb9a01671f21e4e485add9daf2c`.
- Epoch-2 checkpoint SHA256:
  `1db373ba6399aac1adcd6fd721a05e79f3a9acfc39d8a03f2e0a5d8c49ea1457`.
- Formal epoch-1 deployment-field SHA256:
  `3bda71b91f7e1f5147a8399c5fdcb41207869ffbd5f0236bfbdecaa88d4c4110`.
- The 922-file parent closure remains exact at
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`.
- Formal-root immutability snapshots before and after the diagnostic are
  identical; each snapshot JSON has SHA256
  `ba1b6310e00dcbffede964fd0293122139141ae8fe2dbf433a486f36221a85c5d`.

## Formal checkpoint selection

Official dev was not used for checkpoint selection. Selection used the frozen
83-state heldout-training evidence and prioritized classification before score,
policy KL, or epoch. Epoch 1 was `STRONG`; epoch 2 was `PARTIAL`.

| Metric | Epoch 1 | Epoch 2 |
| --- | ---: | ---: |
| Classification | STRONG | PARTIAL |
| Eligible | true | true |
| Selection score | 0.0632530120 | 0.0391566265 |
| Correct policy KL | 0.0987784935 | 0.0865620102 |
| Positive task count | 4/8 | 5/8 |
| Stable generation | true | true |

Heldout one-step action-signature / semantic-successor / execution metrics:

| Condition | Epoch 1 | Epoch 2 |
| --- | --- | --- |
| Zero | 0.325301 / 0.385542 / 0.891566 | 0.325301 / 0.385542 / 0.891566 |
| Correct | 0.385542 / 0.433735 / 0.975904 | 0.409639 / 0.445783 / 0.927711 |
| Key-payload shuffle | 0.349398 / 0.421687 / 0.951807 | 0.409639 / 0.457831 / 0.951807 |
| State-query shuffle | 0.361446 / 0.409639 / 0.975904 | 0.397590 / 0.421687 / 0.891566 |

Complete heldout trajectories were already complete for both epochs and did
not modify selection. Successes over eight tasks were epoch 1 `3/1/4/3` and
epoch 2 `3/4/3/3` for zero/correct/key-payload-shuffle/state-query-shuffle.

## Formal epoch-1 control integrity

The control audit passed every preregistered check:

- Correct and shuffle both use the selected epoch-1 writer/reader checkpoint.
- Both use all 499 memory IDs, the same keys, rho values, and payload set.
- Permutation SHA256 is
  `96f1f3784c73e45bc9036b40aa83f43eca8e03b39995cd6f9fc29cd7790c7db2`.
- The permutation is a bijection over 499 rows with exactly zero fixed points,
  no omissions, and no duplicates.
- The deployment memory-ID/key SHA256 is
  `6cbaf15f3e65ba46cdd1a68116b1641b1a1e9f004427a325f5174793d7f81659`;
  the payload-set SHA256 is
  `709b026f6be873e6ab6b86c0e9dccef8841d90dc6a7db9051c90a9291d0e60ff`.
- Correct and shuffle conditions use Qwen3-8B, `full_demo_first_only`, seed
  25101, deterministic generation, identical ordered 57-task lists, and no
  runtime retrieval.
- Condition labels and directories are not swapped. No task output is reused
  or hard-linked between conditions.
- Per-task rows independently recompute to formal correct `8/57` and shuffle
  `18/57`.

This excludes `H4 - implementation/control integrity defect` for the audited
formal result.

## Field reconstruction and epoch-2 fields

The diagnostic epoch-1 reconstruction matched the formal epoch-1 scientific
A/B tensor hashes for the 401-memory correct and shuffled fields and the
499-memory correct and shuffled deployment fields. Serialized wrapper file
hashes differ because diagnostic-only provenance metadata and paths differ;
the validated tensors, memory IDs, and checkpoint binding are exact.

Epoch-2 field evidence:

| Artifact | SHA256 / tensor SHA256 | Shape | Norm |
| --- | --- | --- | ---: |
| 401 correct A | `3b4e6545efcf2b14b2520a3b0a0e6ac5966e337900bebd60284e90c715a53fcc` | 960x8x256 | 328.376129 |
| 401 shuffle A | `59ccb7a09bfe72517bed0ff022c3476dc54819ff90eebc88cb90b00fee0c45ef` | 960x8x256 | 324.682678 |
| 499 correct file | `c88a26d73892344695b21ea14d160317b971405ec35e5e845e8a13b06b616ddc` | n/a | n/a |
| 499 correct A | `12dfd904116199e63d93674f3a9b5c5d0df8b3b86bb4a7e94956b28ce2548d68` | 960x8x256 | 421.154053 |
| 499 shuffle A | `48646d450d14d21cfe313ea4d0e9e3850ffd31e91dcca840274173edb3e4af5d` | 960x8x256 | 417.149323 |
| B (both) | `9f1dcbc35c350d6027f98be0f5c8b43b42ca52b7604459c0c42be3aa88913d47` | 8x256 | 0.0 |

For epoch 2, correct versus shuffle A has L2 norm `29.0242119` and
maximum absolute difference `0.6939529`; B is exactly equal. All tensors are
finite. The same 499-memory zero-fixed-point permutation was used.

## Epoch-2 official-dev diagnostic

| Metric | Epoch-2 correct | Epoch-2 matched shuffle |
| --- | ---: | ---: |
| Successes | 16/57 | 19/57 |
| Total steps | 1,339 | 1,441 |
| Prompt tokens | 13,335,717 | 13,400,740 |
| Completion tokens | 130,078 | 150,480 |
| Context overflows | 8 | 8 |
| Repeated actions | 357 | 589 |
| Completion actions | 37 | 33 |
| Execution exceptions | 0 | 0 |
| Mean prompt tokens/step | 9,959.4600 | 9,299.6114 |
| Task wall time | 6,381.45 s | 7,232.86 s |

Paired outcomes:

- Correct-only: 4 tasks: `6171bbc_3`, `d4e9306_1`, `df61dc5_1`, `fac291d_2`.
- Shuffle-only: 7 tasks: `23cf851_2`, `396c5a2_2`, `57c3486_3`,
  `6171bbc_2`, `b119b1f_3`, `df61dc5_2`, `fac291d_3`.
- Both: 12 tasks.
- Neither: 34 tasks.
- Exact two-sided McNemar p-value: `0.548828125`.

Descriptive differences:

- Epoch-2 correct minus shared bare: `+4/57`.
- Epoch-2 correct minus epoch-2 shuffle: `-3/57`.
- Epoch-2 correct minus formal epoch-1 correct: `+8/57`.
- Epoch-2 shuffle minus formal epoch-1 shuffle: `+1/57`.
- Epoch-2 correct minus three-demo correct: `-1/57`.

The complete per-task paired table is in `final_result.json`.

## Interpretation

The result is `EPOCH2_DIAGNOSTIC_MIXED_INCONCLUSIVE`. Epoch 2 improves the
correct condition over bare and over the formal epoch-1 correct condition, but
it does not restore matched-shuffle specificity: matched shuffle remains three
successes higher. This is consistent with neither the strict rescue rule nor
the strict no-rescue rule.

The anomalously strong matched-shuffle condition is not isolated to epoch 1:
epoch-2 shuffle is `19/57`, one success above epoch-1 shuffle. A future
multi-permutation sensitivity study could test whether this behavior depends
on the single frozen permutation, but no such study is authorized or launched.

The formal 14n result remains unchanged. Epoch 1 remains formally selected;
epoch 2 is post-hoc and cannot become the paper result based on dev outcomes.

## Evidence

- Git-safe result directory:
  `research/results/exp037a_r19_epoch2_sensitivity/`
- Raw Lambda diagnostic root:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r19_epoch2_forced_dev_20260908_001`
- Raw Lambda artifact-index SHA256:
  `fb8a6d6e712c2d612e467a9f3d050bb54fca46a52abdc4c949883d6692cb2189`
- Final-result SHA256:
  `e9cf96f454299601a1b2f76bc6d706e191fc67b266dadb4ce95ee9f412eb3c99`
- Field-construction SHA256:
  `a2013f84ed3e0d99e7e136b3368c3dce1f85c93ec03b316d6264ffaafd1de7a0`
- Formal-control-integrity SHA256:
  `a0e13479ac9414a05036496ade6a893e3793ceacb2e82480b8533c1a99126c23`

Raw task trajectories, model outputs, and observations remain Lambda-only.
