# EXP-034A: One-Demo-Consistent Retraining of EXP-031A

## Status

- Run UUID: `rcmf_exp031a_one_demo_retrain_11b_20260829_001`
- Branch: `research/v5-rcmf-one-demo-retrain`
- Starting commit: `a2c41652bfea380cfef89df0bee0e4d919458982`
- Immutable EXP-033A archive: `archive/exp033a-one-demo-dev-eval-a2c41652` at the exact starting commit
- Immutable EXP-031A source: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- Global seed: `25101`
- Scientific change: training-side prompt profile `full_demo` to the existing `full_demo_first_only`, and nothing else
- Selected checkpoint: epoch 2
- Formal dev evaluation: complete, `57/57` tasks for N1 and N2
- Final descriptive result: N1 exceeds D0 in point estimate, equals N2, and is one task below old D1
- Final record commit: `08e5ae7fee0e78842f8bd519dd59b4ccbcd1b7d0`

## Frozen Identities

- Original EXP-031A checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- Original EXP-031A deployment-field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- Frozen selector ensemble SHA256: `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Retained first-demo SHA256: `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- One-demo initial asset SHA256: `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`
- Dev task-list SHA256: `c6aad8dca959d9c54537555dd6c3a4ececdd55390029511ab7971550d796e463`
- EXP-033A audit-index SHA256: `1616378ab22874c21d4f9bff84db52078ed4d9533b4d86cac79014adb7a09b72`
- Key-payload shuffle SHA256: `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`
- Replay-validated corpus manifest SHA256: `6e6d3b54b1a1c5d69508e5a69f433c9c8a749c64aab95137d0c872cec3cce0a1`

The architecture remained exactly EXP-031A: 8 slots, key dimension 960, payload dimension 256, four section writers, reader layers `[7,14,21,28]`, parent-normalized rho, `A=[960,8,256]`, `B=[8,256]`, no hard gate, no retrieval, no top-k, no runtime per-memory scoring, and no raw-memory deployment prompt.

## Data And Prompt Reconstruction

- Task split: the immutable 29 model-training tasks and 8 heldout-train tasks.
- Heldout task IDs: `76f2c72_1`, `76f2c72_2`, `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`, `e7a10f8_3`.
- Fixed state universe: 366 model-train and 98 heldout states.
- Fixed-state identity SHA256: `4523c5f98d45badf5d523cfe22c5f53337beef6b87c9dd344481edf6607ce484`.
- Dependency manifest SHA256: `9d8fb576d39e704c0f35cf85ee7a58c7bb35c3d79bc43b0da1cec7db11d597e4`.
- One-demo state cache SHA256: `200e8037ee953b327d43f2d9d43f9d8725cf7f2ed701759dfa1e913eb1a91483`.
- State cache: 464 fresh frozen-Qwen forwards, 0 resumed, 309.002 s, no target action, future observation, truncation, or dev access.
- Frozen-selector selections changed for 93/464 states relative to the three-demo state geometry.

Prompt-independent artifacts reused after hash validation:

| Artifact | SHA256 |
|---|---|
| Raw memory ledger | `e565cc83dd22fca9a9a813e4655f23c784009b0a83b08e64712120ffafa15acb` |
| Clean transitions | `f25f7ed937776cd8f0f373a16cf7869f766bac2a269083aed2998779de31c623` |
| Transition multiview cache | `00549c7de1ee75e39ab5a2919a1f3c8d713fbec6709255955d0b2805b276cc3b` |
| Task split | `b2e1efc1ea687dc0e2767f281e31c7405fc63e6c8935a9e853b6d0e40b97ee14` |
| EXP-031A memory provenance | `96143da66dc2d0b88ce3e888308099315b80a3f3befa7bdb0a5ff7153d538b6b` |

Prompt-dependent artifacts rebuilt: state renderings and multiview tensors, q(s), selector choices, paired bare/raw outcomes, causal labels, policy teachers, zero-policy cache, training units, heldout teacher-forced rows, heldout live rows, and dev N1/N2 rows.

## Rebuilt Supervision

All 464 paired states completed under `full_demo_first_only`; all 928 raw/bare policy rows were rebuilt. Teacher-cache SHA256 is `e8251e0d6699e79a23a8b0d0d30d9e1ab4dbc282d98e1cd6f6b08ec2fcbd71a9`.

| Split | Prompt | Harmful | Neutral | Positive |
|---|---|---:|---:|---:|
| Model train | one demo | 37 | 218 | 111 |
| Heldout train | one demo | 8 | 61 | 29 |
| Model train | old three demo | 26 | 235 | 105 |
| Heldout train | old three demo | 9 | 65 | 24 |

- Changed labels: `138/464`.
- Paired-outcome SHA256: `24f2db33d33780a0eb757b24e7751fe718d11bdb2425c629f5190646cf986164`.
- Training-unit manifest SHA256: `c0771707cbe215f9ec2e1d4fe105d98b98ed41e48b3a749f06adefe0050739d7`.
- Zero-policy mean NLL: `0.1970756`.

## Training

Exactly 26,810,368 parameters were trainable: 8,949,760 writer parameters and 17,860,608 reader parameters. Qwen and the selector remained frozen and gradient-free. The exact two-epoch EXP-031A recipe ran 1,176 backwards over 588 units per epoch with complete task-legal fields.

| Epoch | Checkpoint SHA256 | Final recent loss | Checkpoint recent mean | Elapsed at boundary |
|---|---|---:|---:|---:|
| 1 | `3fe8e2a0b8e8b04574568258fcf34cdaefda2e50447624966b92e55ff93cfb13` | 0.178266 | 0.200592 | 1,687.702 s |
| 2 | `078dcd0e3b729877a5b9994ed515cb0867601ef72381b7d8b7705dfb66cd56ef` | 0.104328 | 0.112964 | 3,377.700 s |

Total training-attempt time was 3,380.755 s. Epoch 2 writer SHA256 is `6786216e54e179d72d976685dbdef277031c65157927c808740725aa9d460581`; reader SHA256 is `0cf8ca38007047bbcdfdfbb6e55ae4f62880607bbd106b4ab20e367203c687b9`.

## Heldout Selection

Dev outcomes were not inspected or used. Both epochs were evaluated on the exact 98 heldout states with zero, correct, key-payload-shuffle, and state-query-shuffle controls.

Teacher-forced target NLL:

| Epoch | Zero | Correct | Key-payload shuffle | Correct minus zero |
|---|---:|---:|---:|---:|
| 1 | 0.194272 | 0.121799 | 0.121679 | -0.072473 |
| 2 | 0.194272 | 0.114577 | 0.117249 | -0.079695 |

Live one-step metrics:

| Epoch / condition | Signature | Successor | Execution | Exact API |
|---|---:|---:|---:|---:|
| Epoch 1 zero | 0.336735 | 0.367347 | 0.887755 | 0.520408 |
| Epoch 1 correct | 0.357143 | 0.377551 | 0.948980 | 0.571429 |
| Epoch 1 key shuffle | 0.336735 | 0.377551 | 0.938776 | 0.540816 |
| Epoch 1 state shuffle | 0.357143 | 0.377551 | 0.959184 | 0.581633 |
| Epoch 2 zero | 0.336735 | 0.367347 | 0.887755 | 0.520408 |
| Epoch 2 correct | 0.438776 | 0.438776 | 0.959184 | 0.653061 |
| Epoch 2 key shuffle | 0.387755 | 0.448980 | 0.948980 | 0.591837 |
| Epoch 2 state shuffle | 0.336735 | 0.346939 | 0.908163 | 0.530612 |

Epoch 1 was eligible but `PARTIAL` with selection score `0.0102041`. Epoch 2 was eligible and `STRONG` with score `0.0765306`; the unchanged EXP-031A heldout-only algorithm selected epoch 2. Positive correct-minus-zero behavior occurred on 6/8 heldout tasks. Decision branch: `rcmf_full_field_checkpoint_selected`.

## Deployment Field

The selected writer compiled and instant-added all 98 heldout-task memories to the 401-memory train field without an optimizer step.

- New 499-memory field SHA256: `f24b16e4af6f1c59b0f59984b551eb590d80f09e6f380f30c370c8d5593d2fc7`.
- Shape remained `A=[960,8,256]`, `B=[8,256]`; float32 field bytes: `7,872,512`.
- Mean compile per memory: `0.00100918 s`; maximum: `0.00236820 s`.
- Mean add per memory: `0.0000692265 s`; maximum: `0.000131247 s`.
- Total 98-memory compile plus add: `0.111178 s`.
- Audit rebuild max error: `5.24521e-6`; remove/restore max error: `1.43051e-6`.
- Production add scanned no existing records; read shape and complexity stayed independent of memory count.

## D0 Reuse

The immutable EXP-033A D0 `12/57` was reused. All ten identity checks passed: Qwen/tokenizer, prompt profile and asset, task manifest, renderer/generation contract, evaluator, fresh worlds, authoritative success, and audit index. Reuse manifest SHA256 is `edfcb765e5bec86b4b52d55aa28bec33faee20693ebcf1fd7643a7ead5b74e7b`; rerun was not required.

## Dev Results

| Condition | Success | Rate | Steps | Mean steps | Median steps |
|---|---:|---:|---:|---:|---:|
| D0 immutable bare | 12/57 | 0.210526 | 1,428 | 25.0526 | 17 |
| Old EXP-033A D1 | 17/57 | 0.298246 | 1,499 | 26.2982 | 17 |
| N1 retrained correct | 16/57 | 0.280702 | 1,428 | 25.0526 | 17 |
| N2 retrained shuffle | 16/57 | 0.280702 | 1,373 | 24.0877 | 17 |

Point effects:

- `N1 - D0 = +4/57 = +0.0701754`.
- `N1 - N2 = 0/57 = 0`.
- `N1 - old D1 = -1/57 = -0.0175439`.

Success sets:

- D0: `fac291d_1`, `fac291d_3`, `b119b1f_1`, `23cf851_1`, `23cf851_2`, `23cf851_3`, `57c3486_2`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`, `6171bbc_2`, `6171bbc_3`.
- Old D1: `fac291d_2`, `b119b1f_2`, `b119b1f_3`, `d4e9306_2`, `0d8a4ee_2`, `df61dc5_1`, `23cf851_1`, `23cf851_2`, `23cf851_3`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`, `6171bbc_1`, `6c2c621_1`, `6c2c621_2`, `396c5a2_1`, `396c5a2_2`.
- N1: `fac291d_1`, `fac291d_2`, `d4e9306_2`, `d4e9306_3`, `23cf851_1`, `23cf851_2`, `23cf851_3`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`, `6171bbc_1`, `6171bbc_2`, `6171bbc_3`, `6c2c621_2`, `396c5a2_2`, `396c5a2_3`.
- N2: `fac291d_2`, `d4e9306_2`, `d4e9306_3`, `df61dc5_1`, `23cf851_1`, `23cf851_2`, `23cf851_3`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`, `6171bbc_2`, `6171bbc_3`, `6c2c621_2`, `396c5a2_1`, `396c5a2_2`, `396c5a2_3`.

Paired changes:

- N1 vs D0: 9 retained, 7 gained (`fac291d_2`, `d4e9306_2`, `d4e9306_3`, `6171bbc_1`, `6c2c621_2`, `396c5a2_2`, `396c5a2_3`), 3 lost (`fac291d_3`, `b119b1f_1`, `57c3486_2`), 38 both failed.
- N1 vs N2: 14 both succeeded, 2 N1-only (`fac291d_1`, `6171bbc_1`), 2 N2-only (`df61dc5_1`, `396c5a2_1`), 39 both failed.
- N1 vs old D1: 11 retained, 5 N1-only (`fac291d_1`, `d4e9306_3`, `6171bbc_2`, `6171bbc_3`, `396c5a2_3`), 6 old-D1-only (`b119b1f_2`, `b119b1f_3`, `0d8a4ee_2`, `df61dc5_1`, `6c2c621_1`, `396c5a2_1`), 35 both failed.

Of the 11 old EXP-033A gains, N1 retained 5 (`fac291d_2`, `d4e9306_2`, `6171bbc_1`, `6c2c621_2`, `396c5a2_2`) and lost 6. Of the 6 old EXP-033A losses, N1 recovered 3 (`fac291d_1`, `6171bbc_2`, `6171bbc_3`) and still failed 3.

## Uncertainty And Stability

| Contrast | Effect | Paired bootstrap 95% CI | Discordant wins/losses | Exact McNemar p |
|---|---:|---:|---:|---:|
| N1 - D0 | +0.070175 | [-0.035088, 0.175439] | 7 / 3 | 0.34375 |
| N1 - N2 | 0 | [-0.070175, 0.070175] | 2 / 2 | 1.0 |
| N1 - old D1 | -0.017544 | [-0.122807, 0.087719] | 5 / 6 | 1.0 |

- Bootstrap used 100,000 paired replicates with analysis seed `25101`.
- N1-D0 leave-one-task-out range is `[0.053571, 0.089286]`; no one-task deletion reverses its positive direction.
- N1-N2 range is `[-0.017857, 0.017857]`; one task can change its qualitative direction.
- N1-old-D1 range is `[-0.035714, 0]`; one task can change negative to tied.
- Largest family share: 28.57% of N1-D0 gains, 50% of either N1/N2 exclusive set, and 40% of N1-only wins versus old D1.
- Every confidence interval includes zero. No statistical generalization claim is made on developer-exposed dev.

## Trajectory Diagnostics

| Metric | D0 | Old D1 | N1 | N2 |
|---|---:|---:|---:|---:|
| Generated tokens | 165,636 | 151,808 | 149,869 | 133,287 |
| Prompt tokens | 11,434,255 | 13,638,869 | 13,790,418 | 14,022,370 |
| Execution exceptions | 0 | 0 | 0 | 0 |
| Context-limit terminations | 0 | 1 | 8 | 9 |
| Repeated actions / no-progress loops | 279 | 409 | 505 | 322 |
| Completion calls | 40 | 38 | 33 | 36 |
| Mean task wall seconds | 105.089 | 124.385 | 130.256 | 118.550 |
| Mean query seconds | 0 | 0.458686 | 0.505031 | 0.534934 |
| Mean field-read seconds | 0 | 0.000213 | 0.000229 | 0.000230 |
| Mean slot norm | 0 | 45.254832 | 45.254831 | 45.254830 |
| Mean residual norm | 0 | 20.409840 | 23.170397 | 25.331176 |
| Mean attention entropy | n/a | 1.682046 | 1.689698 | 1.661628 |

The absolute N1 point estimate is above bare, but the matched shuffled field has the same aggregate success. N1 also has more repeated-action loops and more context terminations than old D1. These observations are descriptive and were not used to alter the model.

## Runtime, Attempts, And Tests

- Runtime preflight: best `6.5797`, expected `8.2246`, conservative `10.4008` H100 hours; automatic launch authorized below 18 hours.
- Accounted H100-active attempt time: `9.1179 h`.
- Ledger wall span: `10.8150 h`; total project wall through final audit preparation was approximately `11.2 h`.
- Attempts: 27 total, 4 failed, 0 open.
- Failed pre-science attempts `paired-001`, `paired-002`, and `teacher-001` produced zero scientific rows and exposed compatibility contracts.
- `dev-n1-001` generated all 57 immutable N1 rows, then failed only while labeling the shared summary. `dev-n1-resume-001` hash-validated and reused all 57 rows (`new_task_count=0`).
- Final local full suite: `726 passed, 1 skipped`.
- Final Lambda full suite: `727 passed`.

## Audit And Deviations

- Audit index: `research/audits/rcmf_exp031a_one_demo_retrain_11b_20260829_001/index.json`.
- Audit-index SHA256: `5c9ce71b618a189a502e90cd76a1e856d5bbb7f22d4a6af482f939614b4fbf2b`.
- New audit coverage: 114 task conditions, 2,801 step rows, 57 comparisons.
- Secret verification: 172 text files checked, 0 raw JWT matches, 199 sensitive observations registered, 0 registered leaks.
- Raw Lambda artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_exp031a_one_demo_retrain_11b_20260829_001`.

Implementation deviations, none scientific:

1. Shared upstream runners required three compatibility aliases/audit keys; failed preflights stopped before rows and were fixed without changing scientific semantics.
2. N1's shared D1/D2 summarizer rejected the outer N1 label after all rows completed. The fix validates D1 semantics while preserving N1 as the result label; no generation reran.
3. The audit exporter initially omitted three already-recorded provenance fields. The first uncommitted export was moved to `/lambda/nfs/rcmf-persist/quarantine/exp034a_audit_export_v1_20260830T000000Z`; the final export restored the fields and revalidated both trees.
4. The Windows `apply_patch` helper repeatedly failed with `helper_unknown_error`; guarded exact UTF-8 replacements were used after each failed patch attempt. The full suites passed.
5. The first local full-suite invocation hit only a Windows default pytest temp-directory permission error; rerunning the unchanged suite with an isolated `--basetemp` passed.

## Descriptive Interpretation

VERIFIED:

- New N1 exceeds bare D0 by four tasks in point estimate.
- New N1 does not exceed matched-shuffle N2; both are 16/57, with two exclusive successes each.
- New N1 does not improve old D1; it is one task lower and retains only 5/11 old gains while recovering 3/6 old losses.
- N1-D0 is leave-one-task-out directionally stable, but all uncertainty intervals include zero.

INFERENCE:

- Matching the training prompt to one-demo deployment may improve absolute performance relative to bare, but this experiment does not validate a memory-specific gain because the matched shuffle performs identically in aggregate.

UNVERIFIED:

- The mechanism behind the changed task set, increased N1 loops, or matched N1/N2 aggregate success.

No new architecture decision is made. No further retraining, calibration, first37, test split, or follow-on experiment is authorized or started.