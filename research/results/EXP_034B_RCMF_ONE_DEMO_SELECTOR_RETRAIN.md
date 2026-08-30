# EXP-034B: Fresh One-Demo Selector Retraining

## Status

- Run UUID: `rcmf_one_demo_selector_retrain_11c_20260830_001`
- Branch: `research/v5-rcmf-one-demo-selector-retrain`
- Starting commit: `0d8da7a5073a2c33b5447e1eabf95c2abf56619a`
- Immutable EXP-034A archive: `archive/exp034a-one-demo-retrain-0d8da7a5`
- Global seed: `25101`
- Single scientific change: replace the historical selector parameters with a fresh three-member selector trained from the locked historical recipe on one-demo state representations
- Dev use in training, checkpoint selection, or calibration: none
- Formal dev completion: `57/57` N1 and `57/57` N2 tasks
- Scientific decision: `STOP`
- Audit publication commit: `da1e507470b39c9e0c2c1690bd53197fd561b69f`

## Frozen Contract

The one-demo prompt, Qwen3-8B, transition cache, memory ledger, 29/8 split,
writer/reader architecture and objective, AppWorld 0.1.0 environment, evaluator,
generation settings, parent-normalized field algebra, key-payload shuffle, and
seed remained fixed. The student prompt contained no raw transition. Runtime
retrieval, top-k, per-memory scoring, and a hard memory gate remained absent.

Key identities:

| Identity | SHA256 |
|---|---|
| Config after identity-only correction | `8a4ae2d98a73c8c2924a0e22a72d9eebb3012ad279f1e4231906dc808713fe11` |
| Historical selector, provenance only | `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f` |
| Transition multiview cache | `00549c7de1ee75e39ab5a2919a1f3c8d713fbec6709255955d0b2805b276cc3b` |
| One-demo initial messages | `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe` |
| Retained first demo | `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713` |
| Official ordered dev tasks | `c6aad8dca959d9c54537555dd6c3a4ececdd55390029511ab7971550d796e463` |
| Immutable EXP-033A audit index | `1616378ab22874c21d4f9bff84db52078ed4d9533b4d86cac79014adb7a09b72` |

## Fresh Selector

The selector state universe contains 638 rows: 499 train and 139 validation.
Exactly 464 hash-identical one-demo rows were reused from EXP-034A and 174 were
freshly encoded; no three-demo state row was used. The fixed transition bank
contains 499 transitions. Legal exclusions yielded 310,433 supervision pairs.

The locked recipe was `hard_lr3e4_e120_t075`: 120 epochs, learning rate
`3e-4`, temperature `0.75`, rank 32, projection dimension 64, and the same
listwise, pairwise, hard-negative, exact-API, and stage losses as EXP-025C.
No architecture or hyperparameter search was repeated. Three structurally
required members were derived deterministically from global seed 25101.

| Selector artifact | SHA256 |
|---|---|
| Member 0 | `b129135bbe88c0bbabbe1e2bfb22f8be94d42197d86a858c75c92b6e96c079a0` |
| Member 1 | `c1e02a9877c46c4267aa48e5012d1e1c5a90b7b90af155cdd9c710a993d47651` |
| Member 2 | `3d19352b2d301d221e16ec32bb734ed32e08802a9f729ae608dbcac2a47899c8` |
| Ensemble | `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300` |
| State query q(s) | `1ff7f69c12cf11407387679f795510586cb91601637d8c5b4123584a096ce4c2` |
| Memory keys k(i) | `dad9590a0a62adef6db620bbf2c1d2faa35f30100b5cc495294c4a9d50cbfa15` |

The exact field-compatible decomposition is `score(s,i) = q(s) @ k(i) +
0.5639004892288608`. Maximum direct-versus-factorized error was
`2.86102e-6`.

Selector diagnostics were heldout-only and not used for tuning:

| Cell/control | NDCG@4 | Tier-3/4 recall@4 | Exact-API recall@4 | Same-intent pairwise |
|---|---:|---:|---:|---:|
| B correct | 0.783678 | 0.776978 | 0.820144 | 0.906302 |
| B state shuffle | 0.151786 | 0.158273 | 0.165468 | 0.567205 |
| B transition shuffle | 0.081675 | 0.158273 | 0.194245 | 0.472864 |
| E correct | 0.785296 | 0.776978 | 0.820144 | 0.903279 |
| E state shuffle | 0.151998 | 0.158273 | 0.165468 | 0.568335 |
| E transition shuffle | 0.083958 | 0.158273 | 0.194245 | 0.472444 |

The fresh selector changed 111/464 downstream selected classes and transitions
relative to EXP-034A.

## Rebuilt Causal Supervision

All 464 downstream states completed paired bare/raw generation: 928 conditions,
with no dev access. The resulting labels were 134 POSITIVE, 281 NEUTRAL, and 49
HARMFUL; 24 labels changed relative to EXP-034A. The paired-outcome elapsed time
was `7,655.299 s`.

The policy teacher contains 464 bare and 464 selected-raw rows. POSITIVE rows
target raw policy; the 330 NEUTRAL/HARMFUL rows target bare policy. Teacher
cache SHA256 is `b4ae734ab4457f65edefe0f6b83836d9d02fcbbc705300984e93f913488a77e0`.

## Writer/Reader Training And Heldout Selection

Exactly 26,810,368 parameters were trainable: 8,949,760 writer parameters and
17,860,608 reader parameters. Qwen remained frozen and gradient-free. The
unchanged two-epoch full-bank recipe ran 1,164 backwards.

| Epoch | Checkpoint SHA256 | Heldout class | Selection score | Positive tasks |
|---|---|---|---:|---:|
| 1 | `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764` | STRONG | 0.091837 | 6/8 |
| 2 | `d8c82a331e66cb8d7a4fe9504dc34dd75cb3534c518e63dc3e08f1fc3a3fa4f1` | STRONG | 0.073980 | 6/8 |

The unchanged heldout-only selection implementation selected epoch 1.

| Epoch/condition | Signature | Successor | Execution | Exact API |
|---|---:|---:|---:|---:|
| Epoch 1 zero | 0.336735 | 0.367347 | 0.887755 | 0.520408 |
| Epoch 1 correct | 0.418367 | 0.428571 | 0.928571 | 0.642857 |
| Epoch 1 key shuffle | 0.377551 | 0.408163 | 0.938776 | 0.581633 |
| Epoch 1 state shuffle | 0.346939 | 0.377551 | 0.928571 | 0.581633 |
| Epoch 2 correct | 0.428571 | 0.438776 | 0.948980 | 0.653061 |
| Epoch 2 key shuffle | 0.397959 | 0.438776 | 0.959184 | 0.602041 |
| Epoch 2 state shuffle | 0.377551 | 0.418367 | 0.918367 | 0.591837 |

## Deployment Field

The selected writer compiled and instant-added all 98 heldout-parent memories
to the 401-memory train field without an optimizer step.

- Deployment field SHA256: `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169`.
- Shape: `A=[960,8,256]`, `B=[8,256]`; memory count 499.
- Mean compile/add per memory: `0.00103088 s` / `0.000074733 s`.
- Complete 98-memory compile plus add: `0.114087 s`.
- Audit rebuild max error: `3.09944e-6`; remove/restore max error: `7.15256e-7`.
- Production add scanned no existing records; read shape and complexity remained independent of memory count.

## Complete Dev Results

The immutable EXP-033A D0 `12/57` was reused after all ten identity checks
passed. N1 and N2 each ran every dev task once in sequential fresh worlds.

| Condition | Success | Rate | Steps | Mean steps | Median steps |
|---|---:|---:|---:|---:|---:|
| D0 immutable bare | 12/57 | 0.210526 | 1,428 | 25.0526 | 17 |
| N1 fresh-selector correct | 10/57 | 0.175439 | 1,707 | 29.9474 | 22 |
| N2 fresh-selector matched shuffle | 15/57 | 0.263158 | 1,425 | 25.0000 | 15 |
| Old EXP-033A D1 reference | 17/57 | 0.298246 | 1,499 | 26.2982 | 17 |

N1 success IDs: `23cf851_1`, `23cf851_2`, `23cf851_3`, `396c5a2_1`,
`530b157_1`, `6171bbc_2`, `6bdbc26_1`, `6bdbc26_2`, `6c2c621_2`,
`fac291d_2`.

N2 success IDs: `23cf851_1`, `23cf851_2`, `23cf851_3`, `4ec8de5_2`,
`6171bbc_2`, `6171bbc_3`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`,
`6c2c621_1`, `6c2c621_2`, `b119b1f_2`, `b119b1f_3`, `fac291d_1`,
`fac291d_3`.

Paired changes:

- N1 vs D0: 6 both succeed, 4 N1-only, 6 D0-only, 41 both fail.
- N1 vs N2: 7 both succeed, 3 N1-only, 8 N2-only, 39 both fail.
- N1 retained 3/11 original EXP-033A D1 gains and recovered 1/6 original D1 losses.
- New N1 is 6 tasks below EXP-034A N1; new N2 is 1 task below EXP-034A N2.

## Uncertainty And Stability

| Contrast | Effect | Paired bootstrap 95% CI | Discordant wins/losses | Exact McNemar p | LOO range |
|---|---:|---:|---:|---:|---:|
| N1 - D0 | -0.035088 | [-0.140351, 0.070175] | 4 / 6 | 0.753906 | [-0.053571, -0.017857] |
| N1 - N2 | -0.087719 | [-0.192982, 0.017544] | 3 / 8 | 0.226562 | [-0.107143, -0.071429] |
| N1 - old D1 | -0.122807 | [-0.228070, -0.017544] | 2 / 9 | 0.065430 | [-0.142857, -0.107143] |
| N1 - EXP-034A N1 | -0.105263 | [-0.210526, 0] | 2 / 8 | 0.109375 | [-0.125000, -0.089286] |

Bootstrap used 100,000 paired replicates with analysis seed 25101. Removing
one task never changes the negative direction of N1-D0 or N1-N2. N1-only wins
versus D0 span four families; D0-only losses span five. N1-only wins versus N2
span three families; N2-only wins span six. Dev remains developer-exposed, so
these are bounded development results rather than final generalization claims.

## Trajectory Diagnostics

| Metric | N1 | N2 |
|---|---:|---:|
| Prompt tokens | 18,301,733 | 11,905,602 |
| Generated tokens | 144,944 | 166,847 |
| Execution exceptions | 0 | 0 |
| Context terminations | 7 | 2 |
| Repeated actions | 681 | 414 |
| Completion calls | 29 | 39 |
| Mean task wall seconds | 134.584 | 132.452 |
| Mean query seconds | 0.564814 | 0.408141 |
| Mean field-read seconds | 0.000222 | 0.000216 |
| Mean slot norm | 45.254817 | 45.254824 |
| Mean reader residual norm | 32.403446 | 25.154214 |
| Mean attention entropy | 1.650140 | 1.635940 |

## Runtime, Attempts, Tests, And Audit

- Successful attempt wall on the single H100: `38,733.904 s = 10.7594 h`.
- End-to-end ledger span: `48,001.585 s = 13.3338 h`.
- N1/N2 formal task wall: `7,671.273 s` and `7,549.761 s`.
- Append-only ledger: 28 unique IDs, 27 launched attempts, 55 rows, 2 failed, 0 open.
- Failed attempts: dependency validation before science and the first N1 identity check before any N1 task row.
- Final focused suite before export: `25 passed`.
- Final local complete suite: `738 passed, 1 skipped`.
- Git-safe audit: 114 task-condition traces, 3,132 generated steps, and 57 comparisons.
- Audit index SHA256: `c4bea6d3fb4c7ab2fef2f489bb626b8b5a0fee75f1dca06d24ff59a744daa802`.
- Secret scan: 172 text files, 0 raw JWT matches, 161 registered sensitive observations, 0 leaks.
- Raw Lambda artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_one_demo_selector_retrain_11c_20260830_001`.

Implementation deviations, none scientific:

1. The user-supplied historical selector SHA contained a transcription error; the immutable source-backed SHA above was used and documented.
2. Two provenance-only attempt commands referenced a nonexistent source suffix; corrected rows preserved the scientific identities and outputs.
3. A failed diagnostic command created an untracked single-space Lambda file. It was preserved in quarantine with SHA256 `1398c18b...`; the checkout was restored clean.
4. The first policy-teacher attempt stopped before generation because two immutable source copies were absent; exact hash-validated copies were restored before the successful attempt.
5. The first N1 attempt stopped before any task row because an inherited runtime check expected the historical selector SHA. The check was corrected to the fresh ensemble identity; the manifest and all other frozen preflight artifacts remained byte-identical.

## Scientific Interpretation

VERIFIED:

- The fresh same-recipe selector is strongly state- and transition-sensitive on its locked selector diagnostics.
- The downstream epoch-1 whole-bank model passes the locked heldout one-step selection contract.
- On complete official dev trajectories, correct N1 is below both bare D0 and matched-shuffle N2.
- N1-D0 and N1-N2 remain negative under every leave-one-task-out deletion.

INFERENCE:

- Retraining the selector on one-demo state geometry does not rescue the complete-trajectory memory-specific effect under this frozen pipeline.
- The selector proxy and heldout one-step gates again fail to predict the direction of complete agent trajectories.

UNVERIFIED:

- Whether a different architecture, objective, prompt regime, or broader evaluation could reverse the result.
- The causal source of N1's extra repeated actions and context terminations.

Decision: `STOP`. The correct field does not exceed bare and does not exceed the
matched key-payload shuffle. No follow-on experiment, architecture search,
calibration, retrieval path, first37/test run, or V5 tag was started.
