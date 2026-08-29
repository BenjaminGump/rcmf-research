# EXP-033A: Frozen EXP-031A One-Demo AppWorld Dev Evaluation

## Status

- Run UUID: `rcmf_exp031a_one_demo_dev_11a_20260829_001`
- Branch: `research/v5-rcmf-one-demo-dev-eval`
- Starting commit: `b04d49d2ce9af305a004bb8bee2a3fc469e53927`
- Frozen EXP-031A source: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- Global seed: `25101`
- Evaluation-only: yes
- Official AppWorld 0.1.0 dev tasks: `57/57`
- Formal task-condition rows: `171/171`
- Infrastructure valid: yes
- Training or optimizer steps: none

## Frozen Identities

- EXP-031A epoch-2 checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- Correct 499-memory deployment field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- Frozen selector ensemble SHA256: `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Key-payload shuffle manifest SHA256: `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`
- Memory count / field shape: `499`, `A=[960,8,256]`, `B=[8,256]`
- Reader layers: `[7,14,21,28]`
- Slots: `8`; key dimension: `960`; payload dimension: `256`
- Parent weighting: parent-normalized rho
- Runtime retrieval, top-k, per-memory scoring, or raw-memory prompt text: none

## Prompt And Leakage Audit

- Prompt profile: `full_demo_first_only`
- Original full prompt SHA256: `dd74c379c97031a062ba79b2b82d3992ec3b38870792f53d86821544f994c4c3`
- Original 74-message structured prompt SHA256: `f9a6937120b7da883c60e9b5e9187290bf71d3d68b0182640487b705f4cb3734`
- One-demo raw prompt SHA256: `a0a8d3b2e10f167dba5dcab5ad62fa8f6737629b813d2d0e27af4872bef9e27b`
- One-demo 20-message initial asset SHA256: `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`
- Retained complete demo #1 section SHA256: `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- Dev ordered task-list SHA256: `c6aad8dca959d9c54537555dd6c3a4ececdd55390029511ab7971550d796e463`
- Retained demo provenance: prompt-native demo whose exact instruction is absent from all `732` scanned legacy task specs.
- Retained demo / dev overlap: `0`
- 499-memory parent / dev overlap: `0`
- Ground-truth model-input leak count: `0`
- Existing `full_demo` behavior remained byte-for-byte unchanged.

## Engineering Gates

- Train-only smoke: passed all D0/D1/D2 checks on two fixed tasks.
- Fresh-world determinism: D0/D1/D2 prompts, token IDs, code, observations, step counts, and task results matched exactly.
- Full local suite: `713 passed, 1 skipped`.
- Full Lambda suite: `714 passed`.
- Runtime preflight: expected `6.4936` H100/wall hours; conservative `8.7664`, below the `18 h` review threshold.

## Primary Results

| Condition | Success | Rate | Steps | Mean steps | Median steps |
|---|---:|---:|---:|---:|---:|
| D0 one-demo bare | 12/57 | 0.210526 | 1,428 | 25.0526 | 17 |
| D1 one-demo correct field | 17/57 | 0.298246 | 1,499 | 26.2982 | 17 |
| D2 one-demo shuffled field | 12/57 | 0.210526 | 1,484 | 26.0351 | 15 |

Primary paired effects:

- `D1 - D0 = +5/57 = +0.0877193`
- `D1 - D2 = +5/57 = +0.0877193`
- Descriptive pattern: `absolute_improvement_and_matched_shuffle_specificity`

This experiment does not declare the framework good enough or failed. AppWorld dev is developer-exposed, one seed was used, and both confidence intervals include zero.

## Paired Task Changes

D0 to D1:

- Retained successes (6): `23cf851_1`, `23cf851_2`, `23cf851_3`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`
- Gained (11): `0d8a4ee_2`, `396c5a2_1`, `396c5a2_2`, `6171bbc_1`, `6c2c621_1`, `6c2c621_2`, `b119b1f_2`, `b119b1f_3`, `d4e9306_2`, `df61dc5_1`, `fac291d_2`
- Lost (6): `57c3486_2`, `6171bbc_2`, `6171bbc_3`, `b119b1f_1`, `fac291d_1`, `fac291d_3`
- Both failed: 34

D2 to D1:

- Both success (10): `23cf851_1`, `23cf851_2`, `23cf851_3`, `6bdbc26_1`, `6bdbc26_2`, `6bdbc26_3`, `6c2c621_1`, `6c2c621_2`, `b119b1f_2`, `d4e9306_2`
- D1 only (7): `0d8a4ee_2`, `396c5a2_1`, `396c5a2_2`, `6171bbc_1`, `b119b1f_3`, `df61dc5_1`, `fac291d_2`
- D2 only (2): `396c5a2_3`, `6c2c621_3`
- Both failed: 38

## Uncertainty And Sensitivity

| Contrast | Effect | Paired bootstrap 95% CI | McNemar discordant | Exact p |
|---|---:|---:|---:|---:|
| D1 - D0 | +0.087719 | [-0.052632, 0.228070] | 11 vs 6 | 0.332306 |
| D1 - D2 | +0.087719 | [-0.017544, 0.192982] | 7 vs 2 | 0.179688 |

- Bootstrap used 100,000 paired replicates with analysis seed `25101`.
- Leave-one-task-out ranges are `[0.071429, 0.107143]` for both contrasts.
- Removing any one task does not reverse either qualitative direction.
- D1 gains over D0 span 8 task families; the largest family supplies 18.18% of gains.
- D1 wins over D2 span 6 task families; the largest family supplies 28.57% of wins.
- No single family accounts for most of either positive contrast.

## Secondary Trajectory Metrics

| Metric | D0 | D1 | D2 |
|---|---:|---:|---:|
| Generated tokens | 165,636 | 151,808 | 143,041 |
| Prompt tokens | 11,434,255 | 13,638,869 | 13,846,710 |
| Execution exceptions | 0 | 0 | 0 |
| Context-limit terminations | 0 | 1 | 0 |
| Repeated identical actions / strict no-progress loops | 279 | 409 | 490 |
| Completion calls | 40 | 38 | 37 |
| Mean task wall seconds | 105.089 | 124.385 | 124.136 |
| Mean state-query seconds | 0 | 0.458686 | 0.469400 |
| Mean field-read seconds | 0 | 0.000213 | 0.000227 |
| Mean slot norm | 0 | 45.254832 | 45.254828 |
| Mean reader residual norm | 0 | 20.409840 | 21.553866 |
| Mean reader attention entropy | n/a | 1.682046 | 1.661166 |

## Runtime And Attempts

- Formal task wall sum: `5.5988 h`.
- Accounted GPU-phase attempt wall, including failed smoke/determinism attempts: `5.6783 h`.
- End-to-end project wall through final audit preparation: approximately `7.4 h`.
- Append-only ledger: `10` attempts, `2` failed, `0` open.
- `exp033a-smoke-001` failed before D1 generation because a frozen state extractor assumed the 74-message full-demo boundary. The profile-aware boundary fix preserved the `full_demo` default.
- `exp033a-determinism-001` failed because repeat A/B reused one AppWorld experiment name. Distinct deterministic world prefixes fixed isolation.
- Three earlier prepare invocations failed schema validation before ledger creation and produced no scientific row.
- First audit export failed closed on one order-dependent sensitive-observation leak. The published export uses a tested complete registration pass before serialization.

## Audit And Artifacts

- Git-safe audit index: `research/audits/rcmf_exp031a_one_demo_dev_11a_20260829_001/index.json`
- Audit index SHA256: `1616378ab22874c21d4f9bff84db52078ed4d9533b4d86cac79014adb7a09b72`
- Git-safe step rows: `4,411`
- Task-condition files: `171`
- Comparison reports: `57`
- Registered sensitive observations: `163`; leaks: `0`; raw JWT matches: `0`
- Raw Lambda artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_exp031a_one_demo_dev_11a_20260829_001`
- Final validated export: `git_safe_export_003`
- Raw unredacted task rows and tensor artifacts remain Lambda-only; Git-safe rows retain typed redactions plus raw hashes and exact Lambda references.

## Interpretation

**VERIFIED:** Under the frozen one-demo deployment prompt, D1 succeeds on 17/57 dev tasks versus 12/57 for both bare D0 and matched-shuffle D2. The paired point estimates are positive, memory-specific under the matched shuffle, stable in direction to deleting one task, and not concentrated in one task family.

**INFERENCE:** The frozen EXP-031A whole-bank field appears behaviorally useful under this one-demo prompt, and the D1-D2 gap is consistent with memory-specific content rather than generic reader activation.

**UNVERIFIED:** Statistical generalization beyond this exposed dev split, robustness across seeds, and whether one demo is better than three demos. EXP-033A was not a 1-demo versus 3-demo causal comparison.

The task stops here. No retraining, new prompt variant, first37/test run, calibration, gate, addressing change, or follow-on experiment was launched.
