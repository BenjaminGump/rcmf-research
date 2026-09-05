# EXP-037A-R12B Arm-Resolved Prompt-Profile Propagation Repair

## Decision

`READY_FOR_14K_REAUTHORIZATION`

The verified `PROMPT_PROFILE_CONFIG_SOURCE_MISMATCH` is repaired. The fresh
14k package is frozen and ready for review, but remains `NOT_AUTHORIZED`. No
long formal run was launched.

## Frozen interpretation

The sealed 14j three-demo evidence remains valid: D06B, D08B, D09/D10, and D22
passed; three-demo deployment evaluation under the common one-demo prompt was
bare/correct/shuffle `12/17/11`. Complete one-demo training and evaluation, and
the final cross-arm comparison, remain `NOT_EVALUATED`. The 357 partial 14j O06
condition outputs were not reused.

## Repair

The paired-causal runtime previously loaded
`stage_c_7b.causal_audit.generation.prompt_profile=full_demo` for both arms.
The new resolver deep-copies that legacy generation contract and applies the
resolved arm prompt profile as its sole authoritative override. It validates
model, context limit, temperature, top-p, sampling, and thinking settings,
while retaining legacy ownership of max-new-tokens, dtype, device map, and
replay mechanics.

- 3D effective change set: `[]`.
- 3D legacy/effective generation SHA:
  `2ba3ca93ebe6f5fe3339c786b0fc3a9748110df524ff2d6bfef5a25fdccb689b`.
- 1D effective change set: `[prompt_profile]`.
- 1D effective generation SHA:
  `178a9c8db84bddaac08d69ff80445adf22a73d4636f742abb705c722ff64413c`.
- Missing, unknown, or conflicting arm profiles fail before model loading.
- Paired artifacts seal arm, legacy, effective, source-config, and effective
  generation identities.

## Token contract

The static renderer was aligned with runtime chat-template semantics by
explicitly setting `enable_thinking=False`. Exhaustive static recount covered
all 499 logical states in each arm. Counts increased by four tokens, but there
were zero changes in scoreability, selected transition, over-context status,
or same-class substitution in either arm. The preregistered zero-discrete-
change gate therefore passed.

## Diagnostics

The known state `appworld:trace:229360a_3:step:27:line:382` measured 42,927
raw tokens with `full_demo` and 38,078 with `full_demo_first_only`. All six
R12A wrong-profile states were over context under the erroneous profile and
feasible under the corrected profile. No-generation replay took 38.50 seconds.

A two-condition production `_run_condition()` smoke generated fresh bare/raw
conditions in 20.76 seconds. Both sealed the one-demo profile; optimizer and
backward counts were zero.

The isolated O06 module diagnostic started from scratch and reused zero 14j
conditions. It completed in 5,679.38 seconds with 407 paired states and 814
fresh conditions: 324 model-train, 83 heldout, labels HARMFUL/NEUTRAL/POSITIVE
`40/247/120`, 11 static-over-context rows, and 10 replay-missing rows. The
minimum-per-label gate passed. These are engineering diagnostics, not one-demo
scientific results.

The bounded O07 consumer smoke used `full_demo_first_only`, produced counts
4,888 bare / 15,060 raw, and performed zero Qwen generations, backward passes,
or optimizer steps. The downstream audit classified all nine active prompt
consumers with zero formal-path mismatches; the hard-coded StaticFeatureBank
path is not reachable from the formal DAG.

## Three-demo compatibility

All 928 sealed D06 condition prompts were reconstructed without generation.
Prompt hashes and token counts matched 928/928; mismatch count was zero. The
3D effective generation config is byte-semantically identical to the legacy
generation config. Scientific changes to the 3D method are zero.

## Production-path validation

At launch source `004f866647cfabb38a141b88e6d83821df88c403`, the real
scheduler/subprocess/stage-writer/strict-validator path completed S00-S04 5/5
in 15.97 seconds and stopped before S05. A second scheduler invocation skipped
all 5 hash-valid stages and executed zero subprocesses. A tampered run UUID was
rejected. The source-bound whole-pipeline audit passed.

Tests used process-start `PYTHONHASHSEED=25101`:

- Local focused: 32 passed in 43.85 seconds.
- Local full: 952 passed, 3 skipped in 77.70 seconds.
- Lambda focused: 32 passed in 16.25 seconds.
- Lambda full: 955 passed in 33.70 seconds.

The local runs used a workspace-owned pytest base directory because the
default Windows pytest temporary directory was inaccessible.

## 14k package

- Launch source: `004f866647cfabb38a141b88e6d83821df88c403`.
- Archive: `archive/exp037a-r12b-launch-source-004f866`.
- UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.
- Config SHA: `f075eead4bd77e92546a876c24979e1882a2bfded5853624aa665ce93c84af69`.
- Contract SHA: `eea5fb745ecd5041ed07e65be55d6a4a3b774caa67239e0d040100bcd9a8cce6`.
- Artifact-index SHA:
  `d94770dc7e8114b63973d74cd14a40f721d92c2c25a5011a1ccbd92b947af448`.
- Authorization request SHA:
  `9219593a96591c2bc673879f5049154e2e0b0da0348ccea35a8398f03676f9ab`.
- Authorization: `NOT_AUTHORIZED`; no runtime authorization, stages, or formal
  attempts exist.

Runtime estimates use measured 14j and repaired O06 anchors. The 3D-fail branch
is 18 expected / 36 conservative wall hours and about 16 H100-active hours.
The full 3D-PASS-to-1D branch is 32 expected / 64 conservative wall hours and
about 29 H100-active hours. Storage remains 46 GiB expected / 90 GiB
conservative. The proposed 80-hour anomaly cap is
`max(2*32, 1.25*64)` and is not an authorization.

## Evidence classification

VERIFIED: prompt propagation, token-contract invariance, six-state context
classification, complete isolated O06 engineering completion, O07 consumer
identity, 928/928 D06 compatibility, tests, frozen package hashes, and false
authorization state.

INFERENCE: the measured isolated O06 timing is representative enough for the
32/64-hour future-run estimate. The estimate remains deliberately bounded by
a twofold conservative margin.

UNVERIFIED: scientific one-demo performance and the final 3D-vs-1D comparison.
They require a newly authorized fresh 14k run from S00.

## Deviations

The first whole-pipeline audit attempt compared a sealed 14h run to current
14k output declarations; it was corrected to validate each historical stage
against its own sealed output manifest. Disposable preflight builds then
failed closed on three record-builder schema issues: paired-smoke field names,
raw-vs-expanded arm include lookup, and effective-profile provenance naming.
All were fixed and the final source-bound disposable and real preflights
passed. The superseded `54e524c` archive remains immutable; it is not the
active launch source.

No selector, panel rule, memory, model, loss, training, D06B, D22, evaluator,
context limit, or approved prompt definition changed.

`NO LONG FORMAL RUN WAS LAUNCHED`
