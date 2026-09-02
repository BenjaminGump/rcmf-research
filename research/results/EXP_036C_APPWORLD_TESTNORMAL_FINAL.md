# EXP-036C Authorized AppWorld Test-Normal Execution

## Outcome

EXP-036C completed the frozen official AppWorld 0.1.0 Test-Normal manifest:
168 tasks crossed with five conditions, for 840/840 complete trajectories.
There were no infrastructure failures, optimizer steps, parameter updates, or
outcome-dependent configuration changes.

Run UUID:
`rcmf_appworld_testnormal_final_13c_20260901_002`

Branch: `research/v5-rcmf-appworld-testnormal-execution`

The primary BEST system has positive absolute and matched-shuffle point
estimates, but both paired 95% confidence intervals include zero. FULL1D is
below bare and below its matched shuffle. Test-Normal is partially exposed, so
these are final development-benchmark results rather than untouched-test
confirmatory evidence.

## Frozen Contract

- AppWorld: legacy `0.1.0`, root
  `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root`.
- Prompt: `full_demo_first_only`.
- Qwen: `Qwen/Qwen3-8B`, frozen.
- Seed and process hash seed: `25101`.
- Determinism mode: `hash_seed_only`; observation canonicalization disabled.
- Generation: temperature 0, top-p 1, no sampling, thinking disabled.
- Tasks: all 168 ordered `test_normal` tasks.
- Conditions: `B0`, `BEST-C`, `BEST-S`, `FULL1D-C`, `FULL1D-S`.
- No retrieval, top-k, runtime memory scan, raw-memory prompt text, gate, Q90,
  field scale, or calibration was used.

| Identity | SHA256 |
| --- | --- |
| Config | `a178b6c203e329d43cf2a976f7f8261ce93abd70b8123f1c00a965202e805201` |
| Ordered task list | `990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83` |
| Test manifest | `c76b939420f76596dc7a9a45f8cb5dc9f8b60b05b9d1465b57160d3121cdbde8` |
| One-demo initial messages | `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe` |
| Retained demo | `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713` |
| BEST selector | `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f` |
| BEST writer/reader | `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1` |
| BEST 499-memory field | `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e` |
| FULL1D selector | `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300` |
| FULL1D writer/reader | `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764` |
| FULL1D 499-memory field | `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169` |
| Common key-payload shuffle | `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170` |
| Formal summary | `6c5dd14dee9aa60c433a2b7cb8509975b697c66f3a873d48a82ba17a5b7908cd` |
| Paired analysis | `900a49c098f100e24351d0b524cf2d03129ab1ca13fe4c9ddf68da34d566a1ac` |

## Task Success

| Condition | Success | Rate |
| --- | ---: | ---: |
| B0 | 44/168 | 0.26190 |
| BEST-C | 48/168 | 0.28571 |
| BEST-S | 42/168 | 0.25000 |
| FULL1D-C | 40/168 | 0.23810 |
| FULL1D-S | 48/168 | 0.28571 |

The complete success IDs and all 168 per-task five-condition outcomes are in
`exp036c_appworld_testnormal_final/paired_analysis.json` and `per_task.jsonl`.

## Paired Effects

All intervals use 100,000 paired task bootstrap replicates with analysis seed
25101. McNemar values are exact two-sided tests.

| Contrast | Net tasks | Rate | 95% CI | Wins/losses | McNemar p | LOO range |
| --- | ---: | ---: | --- | ---: | ---: | --- |
| BEST-C - B0 | +4 | +0.02381 | [-0.04167, 0.08929] | 17/13 | 0.58466 | [0.01796, 0.02994] |
| BEST-C - BEST-S | +6 | +0.03571 | [-0.01786, 0.08929] | 14/8 | 0.28628 | [0.02994, 0.04192] |
| BEST-C - FULL1D-C | +8 | +0.04762 | [-0.02381, 0.11905] | 23/15 | 0.25588 | [0.04192, 0.05389] |
| FULL1D-C - B0 | -4 | -0.02381 | [-0.08929, 0.04167] | 13/17 | 0.58466 | [-0.02994, -0.01796] |
| FULL1D-C - FULL1D-S | -8 | -0.04762 | [-0.11310, 0.01786] | 12/20 | 0.21533 | [-0.05389, -0.04192] |

No interval excludes zero. Removing any one task does not reverse any listed
point-estimate direction. BEST-C gains versus B0 span 14 task families and
losses span 12; the largest family fractions are 11.8% and 15.4%. BEST-C-only
successes versus BEST-S span 12 families; BEST-S-only successes span eight.
The observed effects are not concentrated in one family.

## Trajectory Diagnostics

| Condition | Steps total | Mean / median | Prompt tokens | Generated tokens | Completion calls | Context stops | Repeat / no-progress | Task wall h |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| B0 | 4,791 | 28.52 / 22.0 | 40,879,739 | 501,529 | 149 | 2 | 1,086 / 1,077 | 5.1983 |
| BEST-C | 4,800 | 28.57 / 21.0 | 40,958,972 | 466,985 | 108 | 5 | 1,170 / 1,170 | 6.1424 |
| BEST-S | 5,169 | 30.77 / 22.5 | 44,847,404 | 496,737 | 114 | 4 | 1,670 / 1,670 | 6.5338 |
| FULL1D-C | 5,287 | 31.47 / 26.5 | 52,991,255 | 434,007 | 89 | 5 | 1,926 / 1,926 | 6.1784 |
| FULL1D-S | 4,604 | 27.40 / 18.0 | 38,291,507 | 421,237 | 108 | 1 | 1,091 / 1,080 | 5.5105 |

Infrastructure execution exceptions were zero in every condition. Mean query
latencies were 0.4267, 0.4373, 0.5286, and 0.4119 seconds for BEST-C, BEST-S,
FULL1D-C, and FULL1D-S. Their mean compiled-field read latencies were 0.215,
0.214, 0.222, and 0.212 milliseconds.

## Efficiency And Serving State

The compiled field shape is fixed at `A=[960,8,256]`, `B=[8,256]`, totaling
7,872,512 bytes, independent of memory count. At 499 memories, median compiled
read latency was 0.150-0.153 ms across short/medium/long prompts, versus about
30.29 ms for an explicit per-memory sum. The fitted compiled-read slopes were
0.000056-0.000078 ms per memory, compared with about 0.06054 ms per memory for
the explicit sum.

Median per-memory offline compilation costs were 452.23 ms for raw encoding,
3.25 ms for writer/key compilation, and 0.063 ms for field addition. The raw
ledger remains authoritative and scales with memory count: at N=499 it uses
23,559,155 bytes; deletion state uses 6,007,960 bytes; provenance uses
1,521,063 bytes. Fixed selector/writer/reader model-side state totals
268,555,199 bytes.

TTFT token-equivalence passed. Representative medians were:

| Prompt | Bare TTFT ms | BEST N=1 | BEST N=499 | FULL1D N=499 |
| --- | ---: | ---: | ---: | ---: |
| Short | 294.99 | 299.08 | 299.27 | 299.15 |
| Medium | 421.14 | 425.62 | 425.49 | 425.58 |
| Long | 809.21 | 816.12 | 815.94 | 816.43 |

Efficiency result identities are `09d10ef7fe6cb51408af7c378dce69ede856728a9a53e9dce3fb7f7ece35eeeb`
and serving-state identity
`3b41f0e2cbcc9ced8dc9d50565a99b59fb619ddf35d9f9f66dac4b551a757838`.

### Compilation provenance correction

The immutable raw compilation artifact correctly stored each record's cache
comparison, but its aggregate `raw_reencoding_frozen_cache_equivalence_passed`
field was hard-coded `true`. Recalculation shows 0/499 raw re-encodings match
the frozen cache at `1e-5`; maximum key and payload differences are 0.0013963
and 0.1620693. Commit `b99a87f` derives the aggregate from all rows, and the
append-only correction record is
`exp036c_appworld_testnormal_final/compilation_provenance_correction.json`.

This does not alter the raw-text compilation timings, immutable deployment
fields, task trajectories, or scientific results. The original artifact was
not rewritten.

## Numerical Reversibility

Reversibility used the immutable EXP-031A source representation cache and
deployment writer, not numerically drifted raw re-encodings. All 499
remove/restore rows passed.

- Audit rebuild maximum absolute error: `3.814697e-6`.
- Insertion-order maxima: forward `3.814697e-6`, reverse `5.245209e-6`, seeded
  permutation `4.291534e-6`.
- Maximum per-record remove/restore error: `2.384186e-7`; median `5.960464e-8`.
- Median remove/restore: `0.0790/0.0389 ms`.
- Median add-to-empty/remove-after-add: `0.0428/0.0312 ms`.
- Median replace-same/replace-other/restore: `0.0656/0.0630/0.0495 ms`.
- Result SHA256:
  `e081be3b7996f5f5032a4b4ccaa455cdf5ec3a079f66f329571e49ca7bddcc97`.

This is numerical algebra validation, not a behavioral memory-deletion run.

## Runtime And Attempts

- Formal task wall time summed across conditions: 29.5635 H100-hours.
- Directly measured formal model GPU-active time: 27.2482 hours.
- Post-run compilation/read/TTFT/reversibility session time: about 0.433 hours.
- Attempt-ledger span through reversibility: 35.4802 wall-clock hours.
- Ledger: 14 attempt IDs, 28 append-only events, two failed, zero open.
- Failed attempts: `exp036c-efficiency-read-001` (missing prompt manifest) and
  `exp036c-efficiency-pilot-001` (legacy `cache_position` hook assumption).
  Both were corrected without changing scientific data and their replacements
  completed.
- The requested `_001` run root is preserved as a failed preparation-only run
  with no formal trajectory. The unique scientific run is `_002`.

One initial formal-finalizer shell quoting error and one canonical-rebuild
preflight using the raw re-encoding cache occurred before ledgered replacements.
They are recorded as implementation deviations rather than concealed as
scientific failures.

## Evidence Classification

VERIFIED:

- all 840 frozen trajectories, exact success sets, paired statistics,
  trajectory diagnostics, identity hashes, zero scientific updates, fixed
  field/read shapes, efficiency measurements, and all-record reversibility;
- BEST-C has positive point estimates versus bare and its matched shuffle;
- FULL1D-C has negative point estimates versus bare and its matched shuffle;
- every reported confidence interval includes zero.

INFERENCE:

- the BEST package is the stronger candidate for any paper-facing development
  result because both of its primary point estimates are positive and its
  trajectory length is lower than the matched shuffle;
- FULL1D's worse correct binding suggests its one-demo-fresh components did
  not preserve useful whole-bank specificity on this split.

UNVERIFIED:

- statistical generalization to an untouched AppWorld distribution;
- whether any observed task-level difference reproduces under another model
  or evaluation seed;
- a final framework verdict. This remains reserved for user and ChatGPT review.

## Artifacts

- Raw Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13c_20260901_002`
- Git-safe machine results:
  `research/results/exp036c_appworld_testnormal_final/`
- Git-safe reconstructible audit:
  `research/audits/rcmf_appworld_testnormal_final_13c_20260901_002/`
- Audit index: `research/audits/rcmf_appworld_testnormal_final_13c_20260901_002/index.json`
- Audit index SHA256:
  `682e5e7736706843b07da76e6df59716162de3dec17367a0934d3a05dca564db`
- Per-task paired results:
  `research/results/exp036c_appworld_testnormal_final/per_task.jsonl`
- Full paired analysis:
  `research/results/exp036c_appworld_testnormal_final/paired_analysis.json`

The committed audit contains 840 condition traces and 24,651 reconstructible
step rows. Its 1,762,201,416-byte tree passed the strict scan over 840 JSONL
files, 168 text comparisons, and two JSON assets. Raw unredacted traces and
tensors remain Lambda-only.

No follow-on experiment, retraining, calibration, portability run, or paper
automation was started.
