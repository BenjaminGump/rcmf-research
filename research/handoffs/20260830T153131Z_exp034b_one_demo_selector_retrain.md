# EXP-034B Structured Handoff

## Identity

- Run: `rcmf_one_demo_selector_retrain_11c_20260830_001`
- Branch: `research/v5-rcmf-one-demo-selector-retrain`
- Starting SHA: `0d8da7a5073a2c33b5447e1eabf95c2abf56619a`
- Audit publication SHA: `da1e507470b39c9e0c2c1690bd53197fd561b69f`
- Global seed: `25101`
- Config SHA256: `8a4ae2d98a73c8c2924a0e22a72d9eebb3012ad279f1e4231906dc808713fe11`
- Lambda artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_one_demo_selector_retrain_11c_20260830_001`

## Question And Frozen Scope

EXP-034B tested whether fresh same-recipe selector parameters trained on the
one-demo state geometry rescue the one-demo complete-trajectory memory effect.
The selector architecture and historical chosen recipe were fixed. The
downstream writer/reader architecture, objective, field algebra, prompt,
transition cache, Qwen, evaluator, generation contract, and seed were
unchanged. Dev was excluded from training and selection.

## Verified Selector Result

- State universe: 638, with 499 train and 139 validation states.
- State cache: 464 one-demo rows reused from EXP-034A plus 174 fresh rows.
- Transition bank: 499; legal supervision pairs: 310,433.
- Recipe: `hard_lr3e4_e120_t075`, three deterministic members, no search.
- Member SHA256 values: `b129135b...79a0`, `c1e02a98...7651`, `3d19352b...9c8`.
- Ensemble SHA256: `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300`.
- q/k SHA256: `1ff7f69c...e4c2` / `dad9590a...fa15`.
- Exact decomposition max error: `2.86102e-6`.
- B/E NDCG@4: `0.783678` / `0.785296`.
- B/E transition-shuffle NDCG@4: `0.081675` / `0.083958`.
- Downstream selected classes/transitions changed: 111/464.

## Verified Supervision And Training

- Paired states/conditions: 464 / 928.
- Labels: 134 POSITIVE, 281 NEUTRAL, 49 HARMFUL.
- Changed labels versus EXP-034A: 24.
- Policy teachers: 464 bare plus 464 selected raw; cache SHA256 `b4ae734a...77e0`.
- Writer/reader trainable parameters: 8,949,760 / 17,860,608.
- Backwards: 1,164 across two unchanged epochs.
- Epoch 1 checkpoint: `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764`.
- Epoch 2 checkpoint: `d8c82a331e66cb8d7a4fe9504dc34dd75cb3534c518e63dc3e08f1fc3a3fa4f1`.
- Heldout-only selector chose epoch 1. Both epochs were STRONG; epoch 1 score was `0.091837` and epoch 2 score was `0.073980`.
- Selected deployment field: `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169`.
- Field shape: `A=[960,8,256]`, `B=[8,256]`, 499 memories.
- Audit rebuild/remove-restore max errors: `3.09944e-6` / `7.15256e-7`.
- Production add scanned no existing memory and required no optimizer step.

## Verified Dev Result

All 57 official AppWorld 0.1.0 dev tasks completed under both new conditions.
The immutable EXP-033A D0 was reused only after ten identity checks passed.

| Condition | Success | Rate |
|---|---:|---:|
| D0 bare | 12/57 | 0.210526 |
| N1 fresh-selector correct | 10/57 | 0.175439 |
| N2 fresh-selector matched shuffle | 15/57 | 0.263158 |
| Old EXP-033A D1 reference | 17/57 | 0.298246 |

- N1-D0: `-2/57`, CI `[-0.140351, 0.070175]`, McNemar `p=0.753906`, LOO `[-0.053571,-0.017857]`.
- N1-N2: `-5/57`, CI `[-0.192982, 0.017544]`, McNemar `p=0.226562`, LOO `[-0.107143,-0.071429]`.
- N1-old D1: `-7/57`, CI `[-0.228070,-0.017544]`, McNemar `p=0.065430`.
- New N1-EXP-034A N1: `-6/57`, CI `[-0.210526,0]`.
- N1 retained 3/11 old EXP-033A gains and recovered 1/6 old losses.

Decision: `STOP`. Correct N1 is below both bare and matched shuffle. The
one-demo selector retrain therefore does not rescue complete-trajectory
memory-specific behavior under the frozen pipeline.

## Runtime And Attempts

- Successful single-H100 attempt wall: `10.7594 h`.
- Ledger wall span: `13.3338 h`.
- Formal N1/N2 task wall: `2.1309 h` / `2.0972 h`.
- Ledger: 28 unique attempt IDs, 27 launched attempts, 55 rows, 2 failed, 0 open.
- Final local complete suite: `738 passed, 1 skipped`.
- Failed `exp034b-dependency-001` stopped before science.
- Failed `exp034b-dev-n1-001` stopped before any task row on a stale historical-selector identity check.

## Audit And Provenance

- Git-safe audit index: `research/audits/rcmf_one_demo_selector_retrain_11c_20260830_001/index.json`.
- Audit-index SHA256: `c4bea6d3fb4c7ab2fef2f489bb626b8b5a0fee75f1dca06d24ff59a744daa802`.
- Coverage: 114 task conditions, 3,132 step rows, 57 comparisons.
- Secret verification: 0 raw JWTs and 0/161 sensitive-observation leaks.
- Raw Git-safe export archive SHA256: `f5ecefb49035239b337965fdb42c4ad0f6b97d712f133e2a2e91168f8b0ded68`.
- The archive was copied from Lambda to local and verified before extraction.

Non-scientific deviations are fully recorded in the report and DECISIONS:
historical selector SHA transcription correction; two attempt-command suffix
corrections; preservation of a diagnostic-created single-space Lambda file;
restoration of exact immutable teacher sources; and correction of the inherited
historical-selector runtime identity check. No completed scientific row was
rewritten.

## Files And GitHub

- Final report: `research/results/EXP_034B_RCMF_ONE_DEMO_SELECTOR_RETRAIN.md`
- Machine summary: `research/results/exp034b_rcmf_one_demo_selector_retrain/summary.json`
- Paired analysis: `research/results/exp034b_rcmf_one_demo_selector_retrain/paired_analysis.json`
- Audit index: `research/audits/rcmf_one_demo_selector_retrain_11c_20260830_001/index.json`
- This handoff: `research/handoffs/20260830T153131Z_exp034b_one_demo_selector_retrain.md`

GitHub URLs after publication:

- `https://github.com/BenjaminGump/rcmf-research/blob/research/v5-rcmf-one-demo-selector-retrain/research/results/EXP_034B_RCMF_ONE_DEMO_SELECTOR_RETRAIN.md`
- `https://github.com/BenjaminGump/rcmf-research/blob/research/v5-rcmf-one-demo-selector-retrain/research/handoffs/20260830T153131Z_exp034b_one_demo_selector_retrain.md`
- `https://github.com/BenjaminGump/rcmf-research/blob/research/v5-rcmf-one-demo-selector-retrain/research/audits/rcmf_one_demo_selector_retrain_11c_20260830_001/index.json`

## Next Action

Stop this route for review. Do not automatically launch another selector,
writer/reader retrain, calibration, retrieval mechanism, first37/test run,
architecture study, or V5 tag. The immediate work should be paper-facing:
compare the EXP-033A positive frozen-selector result with the EXP-034A/034B
negative matched-shuffle results and make the proxy-to-trajectory mismatch a
first-class limitation.

Final local/GitHub/Lambda HEAD equality, test counts, process/GPU state, and
safe-to-terminate status are reported in the task's final response after the
publication commit is synchronized.
