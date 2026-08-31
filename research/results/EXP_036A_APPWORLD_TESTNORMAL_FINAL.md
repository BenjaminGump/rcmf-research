# EXP-036A Frozen AppWorld Test-Normal Final Evaluation

## Status

EXP-036A stopped before formal Test-Normal execution at the preregistered
complete-path determinism gate. The formal 840-trajectory evaluation,
efficiency/scaling benchmark, and numerical-reversibility phase are `NOT_RUN`.
No performance conclusion is available from this run.

Run UUID:
`rcmf_appworld_testnormal_final_13a_20260831_002`

Branch: `research/v5-rcmf-appworld-testnormal-final`

Starting commit: `4aa0fd89b2b63d5a9bcbf1e6b18395e0a14e847b`

Scientific source commit used by the smoke:
`61af5066d9642984384bd2e2cfda73a0a1612daf`

## Frozen Contract

- Primary: BEST, the historical selector plus EXP-031A epoch-2 writer/reader.
- Secondary: FULL1D, the EXP-034B one-demo selector plus epoch-1 writer/reader.
- Shared bare: B0.
- Formal conditions: `B0`, `BEST-C`, `BEST-S`, `FULL1D-C`, `FULL1D-S`.
- Prompt: `full_demo_first_only`.
- Evaluation seed: `25101`.
- Qwen, tokenizer, selectors, writers, readers, fields, prompt, evaluator, and
  generation configuration remained frozen.
- No optimizer or backward pass ran.

The exact machine-readable identities are in
`exp036a_appworld_testnormal_final/frozen_model_manifest.json`.

| Identity | SHA256 |
| --- | --- |
| BEST selector ensemble | `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f` |
| BEST writer/reader | `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1` |
| BEST 499-memory field | `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e` |
| FULL1D selector ensemble | `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300` |
| FULL1D writer/reader | `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764` |
| FULL1D 499-memory field | `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169` |
| Common shuffle manifest | `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170` |

Prompt identities:

- one-demo initial messages:
  `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`
- retained complete demo:
  `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- full-demo non-regression: passed.

## Test-Normal Manifest

The legacy AppWorld 0.1.0 loader returned all 168 ordered `test_normal` task
IDs. The ordered-list SHA256 is
`990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83`.
The complete ordered list is published in
`exp036a_appworld_testnormal_final/test_normal_manifest.json`.

Leakage preflight passed:

- Test-Normal ground-truth model-input leaks: 0.
- memory-parent overlaps with Test-Normal: 0.
- retained-demo overlap: false.
- formal manifest was frozen before any Test-Normal outcome.

## Tests And Identity Checks

Before the complete-path smoke:

- local focused: 14 passed;
- local full: 763 passed, 1 skipped;
- Lambda focused: 14 passed;
- Lambda full: 764 passed.

After the determinism-stop exporter was added:

- local focused: 15 passed;
- local full: 764 passed, 1 skipped;
- Lambda focused: 15 passed;
- Lambda full: 765 passed.

All package, field, prompt, shuffle, task-list, frozen-parameter, no-retrieval,
no-runtime-scan, leakage, atomic-resume, and audit-export checks passed before
the smoke. These checks do not override the failed complete-path determinism
gate.

## Complete-Path Smoke

The engineering smoke used the first two frozen task IDs:
`3d9a636_1` and `3d9a636_2`. It completed 15 trajectories and 624 steps: all
five conditions on both tasks, plus all five repeated from fresh worlds on
`3d9a636_1`.

| Condition | Task 1 steps | Task 2 steps | Repeat steps | Exact repeat |
| --- | ---: | ---: | ---: | --- |
| B0 | 50 | 25 | 50 | failed |
| BEST-C | 50 | 24 | 50 | passed |
| BEST-S | 50 | 50 | 50 | passed |
| FULL1D-C | 29 | 50 | 29 | passed |
| FULL1D-S | 50 | 17 | 50 | failed |

All smoke task outcomes were unsuccessful. Smoke success is non-scientific
and was not used to alter any configuration.

The 15 task rows used 2,189.576 seconds of task wall time (0.6082 H100-hours
of sequential complete-path task work). The smoke attempt itself spanned
2,239.285 seconds (0.6220 hours), including setup and final validation.

### Determinism failures

`B0` first diverged at step 18. `FULL1D-S` first diverged at step 19. At each
first divergence:

- rendered history, model response, and executed code matched;
- the complete environment observation differed;
- the differing observation lines parsed to equal Python sets with different
  `repr` element order;
- that textual observation difference then changed later prompts and token IDs.

Observation hashes:

| Condition | Left | Right |
| --- | --- | --- |
| B0 | `43263e605ca3bf2328506060d744a6ddca2b98edb215480fb3898b0018fa2c2d` | `c6f815d82b9153e7aac68724d19990f6d36058bdce23ea1a30de58ac398c9c4c` |
| FULL1D-S | `c64fa7824a3ad5e8a04bfb40e4435d5607bcee7cfa11848d3694f0f4285b0bd6` | `74e6624d7a67c72f7c8cd6925bf15311a1404e272a664217d21322c308b79692` |

The contract requires exact prompts, token IDs, responses, code,
observations, step count, and outcome across fresh identical worlds. Because
two conditions failed, EXP-036A stopped. No observation normalization,
`PYTHONHASHSEED` change, rerun seed, or formal-row exception was introduced.

## Scientific Outputs

| Output | Status |
| --- | --- |
| Formal Test-Normal trajectories | `0/840`, `NOT_RUN` |
| B0/BEST/FULL1D success counts | `NOT_RUN` |
| Paired effects, CIs, McNemar, LOO | `NOT_RUN` |
| Formal steps/tokens/loops/GPU memory | `NOT_RUN` |
| Raw encoding and writer compilation | `NOT_RUN` |
| Field-update/read scaling | `NOT_RUN` |
| TTFT/prefill/decode | `NOT_RUN` |
| Active/archival serving bytes | `NOT_RUN` |
| Numerical remove/restore | `NOT_RUN` |

No `POSITIVE_PATTERN`, `NEGATIVE_PATTERN`, or `INCONCLUSIVE` scientific
classification is assigned because formal evidence was not generated.

## Attempt Ledger

The clean scientific run has three closed attempt IDs:

- `exp036a-prepare-001`: completed;
- `exp036a-smoke-001`: failed at the determinism gate;
- `exp036a-stop-export-001`: completed.

Failed attempts: 1. Open attempts: 0.

An earlier non-scientific `_001` artifact root is preserved. It contains four
closed attempt IDs, including a preflight SHA transcription failure and an
incorrectly ordered efficiency pilot. It produced no task trajectory and was
superseded by the clean `_002` run.

## Deviations

VERIFIED:

1. The initial selector SHA in the config had 65 characters because it
   contained one extra `b`. The immutable 64-character SHA above was verified
   and the correction was append-only recorded.
2. In the superseded `_001` run, an efficiency pilot was launched before the
   formal phase. That violated the required phase order and failed before an
   efficiency result. The source was corrected to enforce formal-before-
   efficiency and a new run UUID was used.
3. The aborted pilot treated BF16 re-encoding equality to a historical cache
   as a hard gate. Raw transition text, tokenization, and provenance matched,
   while current BF16 numerical output drifted. The clean code retains this as
   a diagnostic rather than changing the frozen cache or scientific model.

No deviation changed a model, field, prompt, task manifest, or scientific
outcome. No formal row exists in either run.

## Evidence Classification

VERIFIED:

- identities, leakage checks, tests, smoke rows, first-divergence locations,
  equal set contents, attempt records, and the mandatory stop;
- zero formal, efficiency, and reversibility rows;
- strict Git-safe secret scan passed with 0 registered leaks.

INFERENCE:

- process-level Python hash/set iteration state is the likely source of the
  set-order difference. EXP-036A did not manipulate or causally test it.

UNVERIFIED:

- whether a newly preregistered harness with a pinned hash seed or canonical
  observation serialization would make all 840 trajectories reproducible;
- all Test-Normal performance, scaling, serving, and reversibility claims.

## Audit And Disclosure

Raw Lambda root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13a_20260831_002`

Git-safe audit index:
`research/audits/rcmf_appworld_testnormal_final_13a_20260831_002/index.json`

Git-safe audit bytes: 32,712,412. Raw root size at finalization: 296 MiB over
1,367 files. Strict scan covered 625 redacted step rows and passed.

The official Test-Normal split was partially exposed during prior exploratory
development. The complete five-condition manifest was frozen here, but no
formal result was generated. Nothing in EXP-036A supports an untouched-test
generalization claim.

