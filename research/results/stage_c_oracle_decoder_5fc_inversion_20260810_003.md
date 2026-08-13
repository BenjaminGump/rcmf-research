# EXP-016C Shared-Decoder Held-Out Inversion

Status: **completed**

Run ID: `stage_c_oracle_decoder_5fc_20260810_003`

Final source commit:
`95be149e26598546327c33e8207c1c4f833130aa`

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_decoder_5fc_20260810_003`

Log:
`/lambda/nfs/rcmf-persist/runs/logs/oracle_decoder_5fc_20260810_003.log`

## Scope

EXP-016C tests whether the converged direct embedding-delta oracle can be
represented by a shared 128D decoder. Qwen3-8B remained frozen, evaluation was
teacher forced, and the injection site remained K=4 `last_user_k` input
embeddings. The run did not use or train a memory compiler, selector, gate,
empirical mu, full-bank field, Stage C2 model, AppWorld agent, or end-to-end
RCMF model.

The final command was:

```bash
/home/ubuntu/venvs/rcmf-py311/bin/python \
  scripts/run_stage_c_oracle_decoder_5fc.py \
  --config configs/benchmark/stage_c_oracle_decoder_5fc.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --pair-cache-dir runs/stage_c/pair_grounding_5d_20260807_001/pair_response_cache \
  --stage5fb-dir runs/stage_c/oracle_convergence_5fb_20260809_001 \
  --output-dir runs/stage_c/oracle_decoder_5fc_20260810_003
```

The process resumed the same `_003` artifact atomically. It did not create a
duplicate run or overwrite validated checkpoints.

## Prospective Plateau Correction

Stage-5F-B artifacts remain unchanged. For EXP-016C and future runs, a valid
plateau requires all three conditions:

- absolute relative sequence-Huber change over the checkpoint window < 1%;
- absolute Spearman change < 0.01;
- current sequence Huber <= 1.02 times best-so-far Huber.

Tests cover small improvement, small deterioration, large deterioration,
oscillation, and a genuinely flat plateau. u112 remains the best observed
direct checkpoint; u128 remains the formal Stage-5F-B stopping checkpoint but
does not establish a monotonic asymptote.

## Source Validation

Both direct checkpoints exactly reproduced their saved metrics before decoder
work:

| Target | Shape | Updates/pair | Source checkpoint SHA256 | DeltaE SHA256 | Reproduction max delta |
|---|---|---:|---|---|---:|
| u112 | `[192,4,4096]` | 112 | `4d4e971fde7b4ab57fa629735f0304fd9eb3859f62414f4d4f8c33506a883210` | `35046ae87aaf2a51dc2ec07b37a234a05fff7cad85cfe613d4e458ee969be0ab` | 0 |
| u128 | `[192,4,4096]` | 128 | `a31d53f426aeea9f01ea9def68c66004f49143d68e37f215efe0e9d2564e27f4` | `bf501c69bc7f800ed7f03debf65bf9a84f8e043c7acd76426797d2486d31b2b8` | 0 |

The pair manifest contains 192 pairs, 57 state groups, and all 36 effective
memories. Each fold has 128 decoder-train and 64 held-out pairs. There is no
state overlap within a fold, every pair is held out once, and all train folds
cover all memories.

## Low-Rank Result

Global uncentered rank-128 reconstruction passed the low-rank capacity gate on
both targets. Rank 192 exactly reproduced both direct tensors and frozen-Qwen
behavior. Details are in
`stage_c_oracle_decoder_5fc_low_rank_20260810_003.md`.

## Inversion Schedule

For each fold and decoder, held-out z was evaluated at 2/8/16/32/64 updates per
pair. A path continued to 128 only if u32-to-u64 Huber or Spearman improved
materially and u64 Huber remained within 1.02 times the best observed Huber.
Every row in a checkpoint received exactly the named number of updates.

The two fold-2 frozen-MLP paths stopped normally at u64 because Huber had
deteriorated beyond the best-value guard. All other paths continued to u128.
No path reached the corrected plateau condition.

### Sequence-Huber convergence

| Target | Fold | Path | u2 | u8 | u16 | u32 | u64 | u128 | Final |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| u112 | 0 | frozen linear | .4022 | .3119 | .2474 | .1517 | .0694 | .0502 | 128 |
| u112 | 0 | frozen MLP | .4002 | .3574 | .2771 | .1135 | .0737 | .0530 | 128 |
| u112 | 0 | joint MLP | .3993 | .3580 | .2925 | .1348 | .0894 | .0299 | 128 |
| u112 | 1 | frozen linear | .4788 | .2848 | .1609 | .0770 | .0305 | .0209 | 128 |
| u112 | 1 | frozen MLP | .5093 | .3165 | .1804 | .1102 | .0816 | .0601 | 128 |
| u112 | 1 | joint MLP | .5006 | .3019 | .1851 | .1964 | .0849 | .0379 | 128 |
| u112 | 2 | frozen linear | .2676 | .1683 | .1035 | .0504 | .0287 | .0115 | 128 |
| u112 | 2 | frozen MLP | .2780 | .1602 | .0922 | .0575 | .0828 | n/a | 64 |
| u112 | 2 | joint MLP | .2929 | .1326 | .0807 | .0524 | .0504 | .0349 | 128 |
| u128 | 0 | frozen linear | .4016 | .3282 | .2568 | .1490 | .0393 | .0159 | 128 |
| u128 | 0 | frozen MLP | .4029 | .3879 | .2957 | .1720 | .1246 | .0853 | 128 |
| u128 | 0 | joint MLP | .4042 | .3701 | .2983 | .1471 | .0986 | .0469 | 128 |
| u128 | 1 | frozen linear | .4908 | .3213 | .1715 | .0825 | .0360 | .0190 | 128 |
| u128 | 1 | frozen MLP | .6020 | .3195 | .1864 | .1449 | .1071 | .0984 | 128 |
| u128 | 1 | joint MLP | .5969 | .3288 | .1877 | .1030 | .0957 | .0651 | 128 |
| u128 | 2 | frozen linear | .2693 | .1643 | .1086 | .0532 | .0276 | .0119 | 128 |
| u128 | 2 | frozen MLP | .2684 | .1669 | .1349 | .0980 | .1080 | n/a | 64 |
| u128 | 2 | joint MLP | .2678 | .1709 | .1074 | .0778 | .0541 | .0417 | 128 |

### Final fold metrics

| Target | Fold | Path | Updates | Spearman | Sign | Sequence Huber | Max ratio | Plateau |
|---|---:|---|---:|---:|---:|---:|---:|---|
| u112 | 0 | frozen linear | 128 | .955723 | 1.000000 | .050237 | 1.0000001 | no |
| u112 | 0 | frozen MLP | 128 | .973031 | 1.000000 | .052952 | 1.0000002 | no |
| u112 | 0 | joint MLP | 128 | .987912 | 1.000000 | .029933 | 1.0000010 | no |
| u112 | 1 | frozen linear | 128 | .994460 | .981132 | .020878 | 1.0000001 | no |
| u112 | 1 | frozen MLP | 128 | .982143 | 1.000000 | .060143 | 1.0000007 | no |
| u112 | 1 | joint MLP | 128 | .989194 | 1.000000 | .037923 | 1.0000010 | no |
| u112 | 2 | frozen linear | 128 | .996200 | 1.000000 | .011499 | 1.0000001 | no |
| u112 | 2 | frozen MLP | 64 | .904121 | .976190 | .082850 | 1.0000005 | no |
| u112 | 2 | joint MLP | 128 | .979075 | 1.000000 | .034872 | 1.0000010 | no |
| u128 | 0 | frozen linear | 128 | .987179 | 1.000000 | .015862 | 1.0000001 | no |
| u128 | 0 | frozen MLP | 128 | .858059 | .954545 | .085278 | 1.0000008 | no |
| u128 | 0 | joint MLP | 128 | .950275 | .977273 | .046929 | 1.0000005 | no |
| u128 | 1 | frozen linear | 128 | .995650 | 1.000000 | .019037 | 1.0000001 | no |
| u128 | 1 | frozen MLP | 128 | .974588 | 1.000000 | .098405 | 1.0000010 | no |
| u128 | 1 | joint MLP | 128 | .976877 | 1.000000 | .065078 | 1.0000006 | no |
| u128 | 2 | frozen linear | 128 | .995971 | 1.000000 | .011947 | 1.0000001 | no |
| u128 | 2 | frozen MLP | 64 | .907234 | .952381 | .108009 | 1.0000010 | no |
| u128 | 2 | joint MLP | 128 | .983196 | 1.000000 | .041720 | 1.0000010 | no |

The ratio values up to `1.0000010` are within the configured numerical
tolerance. Decoder hashes are identical before and after every frozen-linear
and frozen-MLP inversion. Joint MLP is explicitly separated because decoder
updates are allowed only in that diagnostic upper bound.

## Pooled Held-Out Results

All rows below use the same 192 held-out pairs pooled across the three folds.

### Primary u112 target

| Method | Spearman | Pearson | Sign | Seq Huber | Target-delta corr | Sparse KL | Target NLL | Max ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen linear + optimized z | .988537 | .987678 | .992806 | .027538 | .869703 | .136920 | .780685 | 1.0000001 |
| frozen MLP + optimized z | .970199 | .968210 | .992806 | .065315 | .863037 | .152640 | .757676 | 1.0000007 |
| joint MLP/z oracle | .988757 | .987280 | 1.000000 | .034243 | .882557 | .127616 | .767682 | 1.0000010 |
| full direct DeltaE | .984810 | .985459 | .992806 | .029525 | .892731 | .096622 | .753281 | 1.0000001 |
| held-out rank-128 SVD projection | .661959 | .709783 | .784173 | .464324 | .496125 | .276703 | .810153 | .4118622 |
| matched-norm random DeltaE | .171966 | .063357 | .604317 | .514250 | .022633 | .279538 | .774689 | 1.0000005 |
| random z, frozen linear | .064492 | -.065966 | .539568 | .518412 | -.110434 | .275883 | .775440 | 1.0000001 |
| random z, frozen MLP | .013620 | .059942 | .503597 | .513538 | -.005746 | .277523 | .772997 | 1.0000002 |
| zero DeltaE | .151501 | .190621 | .575540 | .515256 | .033224 | .282498 | .777454 | 0 |

### Robustness u128 target

| Method | Spearman | Pearson | Sign | Seq Huber | Target-delta corr | Sparse KL | Target NLL | Max ratio |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| frozen linear + optimized z | .994685 | .995136 | 1.000000 | .015615 | .886760 | .126031 | .768367 | 1.0000001 |
| frozen MLP + optimized z | .932921 | .915190 | .971223 | .097231 | .810368 | .180906 | .784156 | 1.0000010 |
| joint MLP/z oracle | .976308 | .972605 | .992806 | .051242 | .863498 | .126408 | .766664 | 1.0000010 |
| full direct DeltaE | .979465 | .982839 | .992806 | .034512 | .875870 | .096836 | .753194 | 1.0000001 |
| held-out rank-128 SVD projection | .703014 | .706117 | .812950 | .468299 | .512267 | .275773 | .805294 | .4153944 |
| matched-norm random DeltaE | .209721 | .111321 | .640288 | .513836 | .022246 | .279979 | .775407 | 1.0000005 |
| random z, frozen linear | -.012947 | -.065264 | .482014 | .517268 | -.114897 | .280561 | .775003 | 1.0000001 |
| random z, frozen MLP | .147612 | .158734 | .568345 | .507313 | .012333 | .276784 | .774848 | 1.0000004 |
| zero DeltaE | .151501 | .190621 | .575540 | .515256 | .033224 | .282498 | .777454 | 0 |

Utility-category means support the same conclusion. Frozen-linear positive /
negative / neutral-absolute student utilities were
`+0.638475 / -0.803046 / 0.000116` on u112 and
`+0.669141 / -0.802849 / 0.000129` on u128.

## Fold-Level Controls

The no-optimization held-out rank-128 projection is not sufficient by itself,
while optimized z through a frozen decoder is positive in all folds.

| Target | Fold | Direct Huber | Rank128 held-out SVD Huber | Zero Huber | Matched-random Huber |
|---|---:|---:|---:|---:|---:|
| u112 | 0 | .035881 | .445527 | .452643 | .445433 |
| u112 | 1 | .035714 | .610734 | .741256 | .746093 |
| u112 | 2 | .016981 | .336712 | .351869 | .351225 |
| u128 | 0 | .027728 | .442606 | .452643 | .445371 |
| u128 | 1 | .037443 | .623718 | .741256 | .744277 |
| u128 | 2 | .038366 | .338572 | .351869 | .351861 |

## Paired Bootstrap

All intervals use 5,000 paired bootstrap samples over the same 192 rows and
are reported as first method minus second method.

| Target/path | Contrast | Point estimate | 95% CI |
|---|---|---:|---|
| u112 frozen linear | Huber vs zero | -.487718 | [-.555564, -.419715] |
| u112 frozen linear | Huber vs random | -.486712 | [-.555039, -.420569] |
| u112 frozen linear | Huber vs full direct | -.001987 | [-.022690, +.016961] |
| u112 frozen linear | sign vs zero | +.417266 | [+.333333, +.500000] |
| u112 frozen linear | Spearman vs random | +.816571 | [+.650091, +.982499] |
| u128 frozen linear | Huber vs zero | -.499641 | [-.569236, -.432749] |
| u128 frozen linear | Huber vs random | -.498221 | [-.567872, -.431199] |
| u128 frozen linear | Huber vs full direct | -.018897 | [-.035194, -.003961] |
| u128 frozen linear | sign vs zero | +.424460 | [+.342857, +.503759] |
| u128 frozen linear | Spearman vs random | +.784964 | [+.617079, +.957333] |

For comparison, frozen MLP was significantly worse than full direct on both
targets: u112 Huber difference `+.035790`, CI `[+.010121,+.063998]`; u128
`+.062719`, CI `[+.024643,+.105235]`. Joint MLP was statistically
indistinguishable from full direct on Huber: u112 CI
`[-.014853,+.024424]`, u128 CI `[-.006954,+.041997]`.

## Scientific Gate And Decision

For frozen linear, frozen MLP, and joint MLP on both targets, all numerical
gate checks passed:

- utility Spearman >= 0.80;
- sign agreement >= 0.85;
- sequence Huber at least 50% below zero;
- neutral mean absolute utility <= 0.05;
- ratio <= 1.0 within tolerance;
- positive result in all three folds.

The required `documented_plateau_all_three_folds` check failed for every path.
Consequently no frozen shared decoder formally passed the preregistered gate.

Decision branch on u112:
`shared_decoder_optimization_or_generalization_failure`.

The same branch is reproduced on u128. The run records the implementation's
identified bottleneck as `tensor_reconstruction_or_qwen_inversion`. The
evidence narrows this further:

- tensor reconstruction is effectively solved on all training folds;
- frozen-linear held-out inversion is the strongest path and is still
  improving through u128;
- therefore the unresolved formal issue is held-out Qwen inversion
  convergence/generalization, not train-tensor fitting or global 128D latent
  dimension;
- current MLP behavior is weaker and less stable than the SVD-initialized
  linear decoder, but branch C cannot be claimed because frozen linear did not
  meet the plateau requirement.

This is strong capacity evidence, not a formal shared-decoder gate pass.

## Attempt History And Repairs

All failed attempts were preserved:

- `_001` stopped before decoder training because float32 SVD could not satisfy
  the rank-192 exact-reproduction check;
- `_002` used float64 SVD but a tolerance helper scaled tiny numerical
  over-ratio rows and changed rank-192 Qwen behavior;
- `_003` retained exact tolerance-safe rows and was resumed atomically after
  implementation fixes for a missing import, tensor target device alignment,
  numerical-floor plateau handling, best tensor checkpointing, and the u64
  continuation predicate;
- the u64 continuation fix stopped fold-2 frozen MLP at u64 when its Huber
  deteriorated, exactly as the milestone contract required;
- one restart command used the wrong working directory and exited immediately
  before model/checkpoint loading. The corrected command resumed the same
  `_003` artifact.

Every code repair added or extended regression coverage. No scientific scope,
architecture, objective, fold, target, checkpoint schedule, K, injection site,
or ratio budget changed.

## Validation

- local related test suite: `54 passed, 1 skipped`;
- Lambda CUDA related test suite: `55 passed`;
- final local full suite: `143 passed, 1 skipped`;
- final Lambda full suite from the project working directory: `144 passed`;
- independent post-run audit: passed, `0` errors;
- source target shape and ordered pair IDs: passed;
- pair/state split and 36-memory train coverage: passed;
- rank-192 exact tensor/behavior reproduction: passed;
- frozen decoder hashes before/after held-out inversion: passed;
- per-pair update accounting at every checkpoint: passed;
- ratio bound within numerical tolerance: passed;
- pooled row counts: 192 for every method and target;
- prompt/pair-only/no-raw-memory/no-selector hard-scope contracts: passed.

One initial final Lambda full-suite command ran from `/home/ubuntu` and
reported eight relative-config-path failures because those tests resolve
`configs/base.yaml` from the current working directory. Re-running the same
suite with `env -C /lambda/nfs/rcmf-persist/project` passed all 144 tests. The
first result was a monitoring-command cwd error and changed no code or
artifact state.

## Runtime And Final Status

- final resumed process runtime recorded by the script:
  `146552.206 s = 40.7089 h`;
- `_003` artifact wall-clock span from first recorded validation/checkpoint to
  final summary: approximately `80.23 h`, including interruptions, repairs,
  resume gaps, and repeated read-only validation;
- final controls/report/validation after the last inversion checkpoint:
  approximately `2.31 h` wall time;
- exact aggregate GPU-active time across every interrupted attempt was not
  separately instrumented, so it is not inferred from wall time;
- final tmux: absent;
- final Python process: absent;
- final GPU: `0 MiB`, `0%` utilization;
- safe to terminate Lambda: **yes**.

## Recommendation

Do not start a memory compiler, selector work, Stage C2, or AppWorld
evaluation. The next separately reviewed milestone should convergence-extend
the existing frozen-linear held-out z checkpoints, preserving decoder hashes,
pair IDs, optimizer states, K=4 `last_user_k`, ratio 1.0, and the corrected
plateau rule. It should stop before compiler training even if the linear gate
passes.

Key artifacts:

- `summary.json`;
- `report.md`;
- `source_checkpoint_validation.json`;
- `decoder_split_manifest.json`;
- `geometry/u112_geometry.json` and `geometry/u128_geometry.json`;
- `low_rank/u112_low_rank.json` and `low_rank/u128_low_rank.json`;
- `decoders/u112/` and `decoders/u128/`;
- `postrun_validation.json` after final record sync.
