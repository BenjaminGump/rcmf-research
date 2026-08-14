# Handoff: V4 Candidate Decision-Transition Memory Pilot / EXP-017

## Status

- Experiment: completed and independently validated.
- Branch: `research/v4-decision-transition-memory`.
- Final experiment source commit:
  `88f9da7be7bcf6380d9df8ba1ce75b78bc14f9b6`.
- Result record commit: the commit containing this handoff; report its exact
  SHA after committing.
- Decision branch: `static_transition_program_insufficient`.
- V4 remains a candidate. Stage C2, selector changes, a memory compiler,
  AppWorld generation/evaluation, and end-to-end training remain unstarted.

## V3 Freeze

- Frozen source state:
  `97ca723ad66597d2afcbbce1eb5466eb34c009f6`.
- Freeze/tag/archive commit:
  `2eb1281ff66792aeb082cce39f6a362697f132e6`.
- Annotated tag: `rcmf-v3-component-validated-pre-transition`.
- Archive branch: `archive/rcmf-v3-component-validated`.
- V4 initial branch state:
  `2eb1281ff66792aeb082cce39f6a362697f132e6`.
- Freeze manifest:
  `research/versions/RCMF_V3_COMPONENT_VALIDATED_PRE_TRANSITION.md`.
- The frozen refs were verified equal locally, on GitHub, and on Lambda.

## Data And Preflight

- 37 train parent trajectories; all 9 held-out validation parents excluded.
- 499 extracted decision transitions.
- Deterministic panel: 148 transitions covering all 37 train parents.
- Query set: 32 states, comprising 24 train and 8 held-out validation states.
- Cartesian pairs: 4,736.
- Illegal pairs: 96.
- Exact legal pairs: 4,640.
- Scoreable pairs: 4,579.
- Over-context pairs: 61, stored as missing and never truncated.
- Panel step buckets: 64 early, 34 middle, and 50 late transitions.
- Query overlap with EXP-016C: zero.
- Extraction reconstruction and transition-field add/remove/replace/order
  reversibility checks passed.
- Expected artifact size was approximately 1.2 GB; final artifact size was
  1,187,801,083 bytes (about 1.106 GiB).
- Preflight runtime projection was 7.596 / 8.687 / 12.270 H100 hours for the
  best / expected / conservative cases. The 12-hour value was used only as a
  review threshold and did not reduce the experiment.

## Raw-Text Transition Teacher

- Utility signs over 4,579 scoreable pairs:
  2,271 positive (49.60%), 941 neutral (20.55%), and 1,367 negative (29.85%).
- Utility mean/std: `0.059851 / 0.289576`.
- Utility range: `[-1.652619, 1.681496]`; median `0.008705`;
  p05/p95 `-0.345125 / 0.535514`.
- Mean utility by early/middle/late step: `0.042587 / 0.076038 / 0.071572`.
- Exact target-substring matches: 586/4,579 (12.80%); utility correlation
  `-0.069662`. There were 1,949 non-copy positive pairs.
- Best transition beat its whole-trajectory parent in 885/1,120 matched
  query-parent groups (79.02%); mean best-child advantage was `0.092779`.
- Average helpful transitions per helpful parent: `1.95776`.
- 218 helpful parents also contained at least one harmful transition.
- Median positive top-1 concentration was `0.456240`.
- The previously excluded long parent was decomposed successfully: 49 pairs
  remained over-context, while its scoreable transitions were retained.
- Teacher semantics, anti-copy, length/overlap, and granularity gates passed.

## Decoder And Oracle

- Decoder: frozen EXP-016C u112 uncentered rank-128 SVD linear decoder, no
  bias, K=4 `last_user_k`, perturbation ratio budget 1.0.
- Decoder hash was unchanged:
  `123ecbf3...38d` (full hash is recorded in the result artifact).
- Zero-injection maximum NLL difference: `2.384e-7`, tolerance `2e-4`.
- Balanced pair-oracle set: 64 pairs.

| Updates | Spearman | Sequence Huber |
|---:|---:|---:|
| 2 | 0.912821 | 0.080640 |
| 8 | 0.965110 | 0.054042 |
| 16 | 0.971795 | 0.038053 |
| 32 | 0.985027 | 0.015241 |
| 64 | 0.957418 | 0.028181 |

- u64 was retained as the preregistered final checkpoint; continuation to
  u128 was correctly suppressed by the deterioration guard.
- Final Spearman/Pearson/sign agreement:
  `0.957418 / 0.754638 / 0.976744`.
- Sequence Huber was 73.20% below the zero control; neutral mean absolute
  utility was `0.004907`; maximum ratio was `1.0000001`.
- Pair-oracle gate passed. The conditional direct-oracle fallback was not
  triggered.

## Static Transition Programs

- 24 transition identities; 574 train and 191 held-out rows; 64 updates.
- Held-out correct-program metrics:
  Spearman `0.123261`, sign agreement `0.543103`, Huber `0.052237`.
- Zero Huber was `0.028513`; random Huber was `0.029014`.
- Correct-minus-zero Huber was `+0.023724`, bootstrap 95% CI
  `[+0.009385,+0.041153]`; the learned static programs were significantly
  worse than zero.
- Correct-minus-random Huber was `+0.023223`, CI
  `[+0.009094,+0.040916]`.
- Positive-pair mean text utility was `+0.087363`, but compiled mean utility
  was `-0.011255`.
- All four held-out tasks failed the per-task consistency requirement.
- Program centered effective rank was `19.578`; pairwise cosine was
  `0.086450`, so the failure is not explained by a simple constant-vector
  collapse.
- Static-transition scientific gate failed.

## Whole-Trajectory Static Control

- 23 parent identities, excluding the parent with no valid whole-trajectory
  labels; 548 train and 180 held-out rows; 64 updates.
- Correct held-out Spearman/sign/Huber:
  `0.337996 / 0.707965 / 0.092187`.
- Zero and random Huber: `0.043670 / 0.043891`.
- Correct-minus-zero Huber was `+0.048518`, CI
  `[+0.013317,+0.090175]`; all four held-out tasks failed.
- Granularity comparison satisfied only 1 of 5 material checks. Transition
  granularity was not validated as behaviorally superior to whole-trajectory
  static programs.

## Interpretation

VERIFIED:

- Individual state-transition behavioral effects are reachable through the
  frozen shared decoder when each pair receives its own latent.
- A single state-independent latent per transition does not generalize to new
  states and is worse than zero/random controls.
- The fixed whole-trajectory program control also fails.

INFERENCE:

- The primary bottleneck is state-independent static program expressivity,
  not the K=4 injection channel or decoder capacity already validated by the
  pair oracle.

UNVERIFIED:

- Whether a state-conditioned transition program `p(s, m_transition)` or an
  explicit state-program interaction can generalize while preserving
  transition identity.

The next reviewed experiment should isolate that state-program interaction.
It should not merely enlarge the static transition encoder, and it must retain
zero/random/shuffled/swap controls plus task-held-out evaluation.

## Recovery History

- Attempt 1 stopped after the pair oracle because the static runner omitted a
  required `expected_validation_task_ids` argument. No static updates ran.
- Attempt 2 stopped immediately in the static evaluator because the evaluator
  accidentally retained an unused required argument. No static updates ran.
- Both attempts and logs were preserved. AST regression tests now validate the
  keyword-only call contracts.
- The final run resumed existing compatible artifacts without overwriting or
  duplicating the experiment. No scientific parameter changed.

## Runtime And Artifacts

- Teacher runtime: `8,189.753 s = 2.275 h`.
- Final resumed behavior runtime: `7,962.770 s = 2.212 h`.
- Canonical teacher plus successful behavior runtime:
  `4.486812 H100 h`.
- Including the two failed/recovered implementation attempts, estimated
  operational active-GPU time was `6.585618 H100 h`; recovery overhead was
  `2.098806 h`.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/transition_memory_6a_20260814_001`.
- Primary summaries: `<root>/teacher_summary.json` and
  `<root>/behavior_summary.json`.
- Generated reports: `<root>/teacher_report.md` and
  `<root>/behavior_report.md`.
- Pair/static/trajectory caches contain 64/765/728 validated rows.
- Independent validator: passed with zero errors.
- Final test suites: local `162 passed, 1 skipped`; Lambda `163 passed`.
- At completion: no tmux session, no Python experiment process, GPU
  utilization 0% and allocation 0 MiB. The instance is safe to terminate after
  the final record commit is synchronized.
