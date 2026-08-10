# EXP-016B Direct-Oracle Convergence Extension

Status: **completed**

Run ID: `stage_c_oracle_convergence_5fb_20260809_001`

Source commit: `02f13ec2bba7600441b565cd97884fc23f9fdbc9`

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001`

## Scope

The run resumed the ratio-1.0 K=4 `last_user_k` direct embedding-delta oracle
from Stage 5F-A. It did not reinitialize DeltaE or Adam, change the objective,
learning rate, injection position, K, pair set, model, tokenizer, or cache.
Qwen3-8B remained frozen and only teacher-forced target scoring was used.

No pair-z/shared-injector training, memory compiler, signed selector, full-bank
model, Stage C2, AppWorld generation/evaluation, or end-to-end RCMF training
was run.

## Resume Integrity

Source checkpoint:

`/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_1.0/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u064.pt`

Verified before the first new update:

- ordered pair IDs: exact match for all 192 pairs;
- min/max/mean updates per pair: `64 / 64 / 64.0`;
- Adam state: present and nonempty for all 192 pair parameters;
- restored learning rate: `0.05`;
- ratio budget / K / position: `1.0 / 4 / last_user_k`;
- objective: `sequence_utility_plus_sparse_kl`, unchanged weights;
- source checkpoint SHA256:
  `26993056d9ac06d6fb43316fdd8ce4cc2557497d994a62500dbc6d16193ea840`;
- normalized source DeltaE SHA256:
  `897db72059a5cb5e8a38beb28b618bc3a7906ce6b973e8d601bd685ce8150424`;
- ordered pair-manifest SHA256:
  `b4868b7b384c099ed929dc1c8cb4d9db608843bdeab70717aaccfb57848f7c4d`;
- Qwen model-config commit:
  `b968826d9c46dd6066d109eabc6255188de91218`;
- tokenizer/model identity: `Qwen/Qwen3-8B`;
- re-evaluated u64 metrics reproduced every recorded Stage-5F-A check with
  maximum absolute difference `0.0`.

The immutable Stage-5F-A checkpoint predates embedded DeltaE/config/model/cache
hashes. It was not rewritten. EXP-016B uses a sidecar containing the source
file hash and the independently audited normalized DeltaE hash, then verifies
both before loading.

### Pre-update aborted attempt

The first launch at source commit `b0037568a3decb8661c58630f73ad14c1fd539c6`
stopped before model loading or any u65 update. The new code's first tensor-hash
function used a different canonical byte framing than the earlier source-audit
script, so two valid hashes of the same tensor were incorrectly compared.
Commit `02f13ec2bba7600441b565cd97884fc23f9fdbc9` made the runtime hash function
exactly reproduce the immutable audit algorithm and added a compatibility
test. The source checkpoint was never modified and the corrected launch again
passed all checks before u65.

## Command

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python \
  scripts/run_stage_c_oracle_convergence_5fb.py \
  --config configs/benchmark/stage_c_oracle_convergence_5fb.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --pair-cache-dir runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache \
  --stage5fa-dir runs/stage_c/oracle_convergence_5fa_20260808_001 \
  --stage5e-dir runs/stage_c/oracle_capacity_5e_20260808_001 \
  --source-checkpoint runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_1.0/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u064.pt \
  --output-dir runs/stage_c/oracle_convergence_5fb_20260809_001 \
  --training-seed 1001 --k 4 --batch-size 1 --direct-lr 0.05 \
  --minimum-updates 128 --hard-cap-updates 256 \
  --reproduction-tolerance 0.00005 --bootstrap-samples 5000
```

## Convergence Curve

All saved checkpoints have equal min/max/mean per-pair update counts.

| Updates | Spearman | Pearson | Sign | Seq Huber | Seq MAE | Seq MSE | Target-delta corr | Sparse KL | Target NLL | Ratio mean/max | Boundary |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 64 | 0.976238 | 0.975828 | 1.000000 | 0.054151 | 0.071027 | 0.030273 | 0.820359 | 0.166327 | 0.790698 | 0.973289 / 1.0000001 | 0.895833 |
| 80 | 0.957625 | 0.937084 | 0.992806 | 0.065346 | 0.081153 | 0.068897 | 0.849818 | 0.141278 | 0.782250 | 0.974054 / 1.0000001 | 0.901042 |
| 96 | 0.982333 | 0.983559 | 1.000000 | 0.034719 | 0.049334 | 0.019274 | 0.879554 | 0.115651 | 0.776367 | 0.974414 / 1.0000001 | 0.901042 |
| 112 | 0.984810 | 0.985459 | 0.992806 | **0.029525** | **0.043546** | **0.016742** | **0.892731** | **0.096622** | 0.753281 | 0.974815 / 1.0000001 | 0.901042 |
| 128 | 0.979465 | 0.982839 | 0.992806 | 0.034512 | 0.048244 | 0.019760 | 0.875870 | 0.096836 | **0.753194** | 0.975180 / 1.0000001 | 0.901042 |

The curve was nonmonotonic. u80 worsened sequence Huber by `20.6747%` versus
u64, u96 improved it by `46.8696%` versus u80, and u112 improved it another
`14.9590%`. u112 is the best sequence-utility checkpoint in the observed
curve. No checkpoint was selected retrospectively for the formal gate.

At u128, the pre-registered u112-to-u128 plateau calculation was:

- relative sequence-Huber improvement: `-0.1689106903`;
- absolute Spearman improvement: `-0.0053458074`;
- required: improvement `< 0.01` and Spearman improvement `< 0.01`;
- result: both conditions true and u128 is eligible to stop.

The negative Huber "improvement" means u128 was `16.8911%` worse than u112.
The user-specified rule treats deterioration as less than 1% improvement, so
the formal plateau passes. This is a verified caveat: the stop is compliant,
but it is not evidence of a monotonic asymptote. A future convergence rule
should separately bound deterioration if that behavior is undesired.

## Final Utility Metrics

At u128:

- `u_text/u_student` Spearman / Pearson: `0.979465 / 0.982839`;
- positive/negative sign agreement: `0.992806`;
- sequence utility MAE / MSE / Huber:
  `0.048244 / 0.019760 / 0.034512`;
- positive-state mean student utility: `+0.667495`;
- negative-state mean student utility: `-0.753849`;
- neutral-state mean absolute student utility: `0.000135`;
- target-token delta correlation / Huber: `0.875870 / 0.257348`;
- sparse teacher KL: `0.096836`;
- target NLL: `0.753194`;
- perturbation ratio mean / max: `0.975180 / 1.0000001`.

Utility-defined categories contained 77 positive, 53 neutral, and 62 negative
pairs. Selection-category results were:

| Selection category | Count | Utility Spearman | Sign | Seq Huber | Mean teacher utility | Mean student utility |
|---|---:|---:|---:|---:|---:|---:|
| positive | 48 | 0.792553 | 0.979167 | 0.064386 | +0.993182 | +0.917383 |
| neutral | 48 | 0.675315 | n/a | 0.000002 | -0.000002 | -0.000056 |
| negative | 48 | 0.911528 | 1.000000 | 0.069891 | -0.991025 | -0.916043 |
| random | 48 | 0.995983 | 1.000000 | 0.003770 | +0.096142 | +0.095757 |

## Final Controls

All controls used the same 192 pair IDs in the same order and the same scoring
code.

| Control | Updates/pair | Spearman | Pearson | Sign | Seq Huber | Target-delta corr | Sparse KL | Target NLL |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| zero DeltaE | 0 | 0.151501 | 0.190621 | 0.575540 | 0.515256 | 0.033224 | 0.282498 | 0.777454 |
| matched-norm random | 128 | 0.062822 | -0.012630 | 0.546763 | 0.515793 | -0.018484 | 0.280358 | 0.774642 |
| Stage-5E underoptimized | 2 | 0.641904 | 0.491805 | 0.776978 | 0.473661 | 0.369083 | 0.261206 | 0.748773 |
| Stage-5F-A u64 | 64 | 0.976238 | 0.975828 | 1.000000 | 0.054151 | 0.820359 | 0.166327 | 0.790698 |
| EXP-016B u128 | 128 | 0.979465 | 0.982839 | 0.992806 | 0.034512 | 0.875870 | 0.096836 | 0.753194 |

Paired bootstrap 95% confidence intervals, all reported as final minus control:

- sequence Huber vs zero: `-0.480744`, CI
  `[-0.546267, -0.414451]`;
- sequence Huber vs matched random: `-0.481281`, CI
  `[-0.551062, -0.414347]`;
- sequence Huber vs u64: `-0.019638`, CI
  `[-0.046206, +0.005487]`;
- sign agreement vs zero: `+0.417266`, CI
  `[+0.335664, +0.500000]`;
- utility Spearman vs matched random: `+0.916642`, CI
  `[+0.749631, +1.084757]`.

The final-vs-u64 Huber CI includes zero. Therefore u128 is numerically better
than u64 but is not established as significantly better by this paired
bootstrap. The direct-capacity conclusion is instead supported decisively
against zero and matched-random controls.

## Gate And Decision

All eight formal checks passed:

- Spearman `>= 0.80`;
- sign agreement `>= 0.85`;
- sequence Huber at least 50% below zero: actual reduction `93.3019%`;
- positive mean utility above zero;
- negative mean utility below zero;
- neutral mean absolute utility `<= 0.05`;
- ratio `<= 1.0` within numerical tolerance;
- documented u128 plateau condition.

Decision branch:
`input_embedding_channel_capacity_passed_after_convergence`.

Interpretation:

- K=4 `last_user_k` input-embedding injection has sufficient oracle
  sequence-utility capacity under ratio 1.0;
- Stage-5E's direct-channel failure remains superseded as an underoptimized
  two-update result;
- Stage-5E's sparse-objective mismatch remains valid;
- an injection-site redesign is not justified by current evidence;
- the next separately reviewed milestone should test a properly optimized
  128D pair-latent/shared-injector decoder;
- no pair-z experiment was run in EXP-016B.

## Validation And Runtime

- Local Stage-C tests: `36 passed`.
- Lambda Stage-C tests: `36 passed`.
- Independent post-run validation: passed with `0` errors.
- Final checkpoint pair counters: unique value `[128]`.
- Final Adam state count: `192`; learning rate `[0.05]`.
- Final checkpoint SHA256:
  `a31d53f426aeea9f01ea9def68c66004f49143d68e37f215efe0e9d2564e27f4`.
- Final normalized DeltaE SHA256:
  `bf501c69bc7f800ed7f03debf65bf9a84f8e043c7acd76426797d2486d31b2b8`.
- Formal runtime: `23,495.135 s = 6.5264 H100 hours`.
- Final checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u128.pt`.
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001/summary.json`.
- Post-run validation:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001/postrun_validation.json`.
- Log:
  `/lambda/nfs/rcmf-persist/runs/logs/exp016b_direct_oracle_20260809_001.log`.
- Active tmux/process: none.
- GPU after completion: `0 MiB / 0%`.
- Safe to terminate Lambda: **yes**.
