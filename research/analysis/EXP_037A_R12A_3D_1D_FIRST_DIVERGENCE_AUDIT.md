# EXP-037A-R12A 14j 3D-vs-1D First-Divergence Audit

Date: 2026-09-05

Decision: `READY_FOR_SCOREABILITY_REPAIR_DESIGN`

Phase-A classification: `THREE_DEMO_STATIC_FILTERED_STATE`

First executable divergence: `PROMPT_PROFILE_CONFIG_SOURCE_MISMATCH`

Secondary finding: `TOKEN_COUNT_CONTRACT_MISMATCH` (four tokens, non-causal
for the target state)

Scientific result: complete 1D and cross-arm comparison `NOT_EVALUATED`

## Executive Summary

The O06 failure is not explained by live AppWorld observations making the
one-demo prompt about 4.8k tokens longer. The shared O06 runner did not execute
the one-demo prompt at all. O05 correctly read the arm-resolved
`full_demo_first_only` profile, while O06 was launched with the shared legacy
replay config, whose causal-audit profile is hard-coded to `full_demo`.

For the failing state, D05 and O05 selected the same class and the same sole
legal candidate memory. D05 correctly rejected that memory under `full_demo`
at 42,924 tokens. O05 correctly admitted it under `full_demo_first_only` at
38,075. O06 then rendered the live state with `full_demo`: bare was 23,809 and
the exact no-generation raw count was 42,927, 1,967 tokens over the 40,960
limit. The correctly profiled live one-demo raw count was 38,078, with 2,882
tokens of headroom.

The 3D positive control remains valid: D05 and D06 both used `full_demo`, so
their scoreability and runtime prompt profiles were aligned. The defect is in
the shared dispatch/config path and became an arm mismatch only in O06.

No production source was changed, no Qwen answer was generated, no target
action was executed, no formal run was retried, and the sealed 14j root's
22,434-file inventory hash remained unchanged.

## Identity

- Starting records commit: `53b534263e6bd8de3f1602fdfd5d99cfa9f88954`
- Frozen execution source: `0e4015547da45802cc7b6ff3a9b92adce73077fc`
- Source archive: `archive/exp037a-r10-launch-source-0e40155`
- Formal UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Formal root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14j_20260904_001`
- Diagnostic ID: `rcmf_exp037a_r12a_3d_1d_first_divergence_20260905_001`
- Diagnostic root: `/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r12a_3d_1d_first_divergence_20260905_001`

The frozen execution checkout remained detached and clean at `0e40155`; no
formal parent, formal tmux, or formal GPU process was active.

## Preserved Science

The following sealed 14j evidence remains valid:

- D06B: train `366`, heldout `98`, labels `129/300/35`, exact historical PASS;
- D08B: PASS;
- D09/D10: complete;
- D22: `THREE_DEMO_REPRODUCTION_PASS`;
- fresh 3D one-demo-deployment dev: bare/correct/shuffle `12/17/11`.

The complete 1D causal panel, 1D teacher, 1D writer/reader, 1D dev, and final
3D-vs-1D comparison remain not evaluated. The 357 partial O06 condition files
are diagnostic evidence only.

## First Divergence In Source

The frozen source establishes this exact chain:

1. `prepare_appworld_structured_rescue_7hr.py:455` passes
   `settings["appworld"]["prompt_profile"]` into O05/D05 selection. The
   resolved arm configs set 3D to `full_demo` and 1D to
   `full_demo_first_only`.
2. `reproducible_stages_14b.py:59,905` passes the same constant
   `configs/benchmark/stage_c_replay_clean_rebuild_7b.yaml` as
   `--replay-config` for both D06 and O06.
3. `run_appworld_train_causal_gate_7hr.py:334,399` extracts `stage_c_7b` from
   that replay config and passes it as the runtime settings.
4. `run_procedural_causal_audit_7b.py:276` reads the runtime prompt profile
   from `settings["causal_audit"]["generation"]["prompt_profile"]`.
5. `stage_c_replay_clean_rebuild_7b.yaml:131` fixes that value to `full_demo`.

Thus both D06 and O06 run `full_demo`, regardless of the arm-resolved prompt.
This is the earliest directly proven executable divergence between O05 and
O06. Relevant frozen-source SHA256s are in `input_artifact_index.json`.

## Target-State Comparison

Target: `appworld:trace:229360a_3:step:27:line:382`

| Field | D05 / 3D | O05 / 1D |
|---|---:|---:|
| Prompt profile | `full_demo` | `full_demo_first_only` |
| 638-state universe membership | yes | yes |
| State row SHA256 | `7ce074f6...cf304` | `fe4208ee...b34a` |
| Selected class | same `procedure:e5ff...16c1d` | same |
| Class score | 4.207823 | 3.847773 |
| Class margin | 1.380438 | 1.289442 |
| Class rank | 1 | 1 |
| Canonical transition | `1b2b3a6a...04c1` (illegal) | same |
| Sole legal attempted transition | `60716ec9...b2f0` | same |
| Memory-section tokens | 19,102 | 19,102 |
| Static base tokens | 23,806 | 18,957 |
| Static raw tokens | 42,924 | 38,075 |
| Static headroom | -1,964 | +2,885 |
| Scoreable | no | yes |
| Panel position | initial 188 | initial 187 |

The class contains two members. The 23,881-token canonical member is illegal
for this state; the 19,102-token alternate is the only legal member and is the
one both arms attempted. This rules out selected-memory divergence as the
target's cause.

## Exact D06/O06 Fate

`D06` marked the target `selected_signature_class_over_context` in D05 and
skipped it without generating either condition. Immediately before its
position, D06 had 177 complete pairs, two replay-missing rows, eight static
over-context rows, and labels harmful/neutral/positive `11/108/58`.

`O06` reached the target in the initial panel after 178 complete pairs, three
replay-missing rows, five static over-context rows, and labels `13/103/62`.
It wrote the bare condition, then failed before raw generation. Both states
are in the initial panel, so adaptive quota stopping is not eligible and label
evolution did not cause O06 to reach the state.

D06 eventually exhausted all 499 logical states: 464 paired rows, 27 static
over-context rows, eight replay-semantic missing rows, and labels
`129/300/35`. Its harmful count never reached 40.

## No-Generation 2x2 Token Decomposition

D05 has no selected 3D memory because its only legal member is over context.
The memory axis therefore collapses to the same `60716ec9...b2f0` candidate;
there is no distinct 3D-selected memory to cross.

| Runtime-equivalent live messages | Bare | Raw with `60716ec9...b2f0` | Headroom |
|---|---:|---:|---:|
| `full_demo` | 23,809 | 42,927 | -1,967 |
| `full_demo_first_only` | 18,960 | 38,078 | +2,882 |

- prompt-profile effect: `4,849` tokens;
- selected-memory effect: `19,118` tokens under either profile;
- prompt-memory interaction: `0` tokens;
- runtime live-vs-runtime static observation effect: `-1` token.

Two fresh diagnostic replays took 4.980 and 5.054 seconds. Both reproduced the
same 26 observation hashes, state fingerprint, prompt hashes, and token counts.
Every repeat is materialized in `live_token_2x2.json`. Qwen generation and
target-action execution counts are both zero.

## Token-Count API Audit

For every fixed message array, `HFQwenBackend.tokenize_messages()` and the
generation-equivalent path agree exactly. `add_special_tokens=true` and
`false` also produce the same count for these rendered prompts.

The O05/D05 helper is consistently four tokens shorter because
`_render_and_count()` calls `apply_chat_template()` without the runtime
backend's explicit `enable_thinking=False`. Rendered SHA256 values therefore
differ. This is a real `TOKEN_COUNT_CONTRACT_MISMATCH`, but it does not change
the target's classification: 38,075 and 38,079 are both feasible; 42,924 and
42,928 are both over context.

## Hypothesis Disposition

- H1, 3D static filtering: **verified for the target**. D05 marks it over
  context and D06 never attempts it.
- H2, selector divergence: **not causal for the target**. Class and sole legal
  memory agree, although 85/499 classes and 94/499 transitions differ globally.
- H3, 3D quota stopped first: **rejected**. Both positions are initial, and D06
  later exhausts the full state universe.
- H4, D06 executed with different feasibility: **rejected**. D06 wrote no
  target condition output.
- H5, tokenization contribution: **verified secondary** at four tokens, but
  non-causal for the target.

## Global D05/O05 Comparison

The two sealed ledgers contain the same 499 logical states:

- same scoreability: `483`; both scoreable: `472`;
- 1D-only scoreable: `16`; 3D-only scoreable: `0`;
- both unscoreable: `11`;
- same selected class: `414`; different: `85`;
- same selected transition including null: `405`; different: `94`;
- same-class substitutions: 3D `43`, 1D `54`.

The failing state is one of the 16 1D-only-scoreable states, so it is a small
but systematic arm-differential category, not a unique case.

## Phase B Targeted Census

Phase B ran because the wrong runtime profile could affect more than one
state. The predeclared union contained 276 states:

- 16 1D-scoreable/3D-unscoreable;
- 94 with different selected transitions;
- 17 with O05 static raw headroom at most 6,000 tokens;
- 207 through the failure position plus 20 states;
- 27 D06 static-over-context controls.

The union, not the category sum, is 276. Runtime was 3,472.1 seconds (57.9
minutes), below the predeclared 1.35h expected and 2h conservative bounds.

Results:

- replay ready: `259/276`; locked replay unavailable: `17/276`;
- among all 259 replay-ready states, 3D and 1D static scoreability each matched
  the correctly profiled live feasibility exactly (`259/259`);
- O05-scoreable but correctly profiled live-1D-infeasible: `0`;
- production O06's erroneous full-demo profile would be over context for six
  replay-ready states;
- alternate live-feasible member in the already-selected class: `0` in both
  arms.

The six wrong-profile failures occur at 1D panel positions 187, 188, 223, 279,
350, and 438. The known target is the first executable one. An earlier
1D-only-scoreable state at position 41 was unavailable under locked replay and
therefore became typed replay missing rather than reaching raw generation.

The 17 replay-unavailable census rows are concentrated in histories already
subject to locked replay-semantic mismatch; they have no token-feasibility
classification and remain explicitly unresolved. The targeted set was enough
to establish the general pattern, so no full-499 census was run.

## VERIFIED

- O05 and D05 use the arm-resolved prompt profile; O06 and D06 use the shared
  legacy replay profile `full_demo`.
- The target's selection class and sole legal attempted transition agree
  across arms.
- D05 filters the target, D06 skips it, O05 admits it, and O06 fails after its
  bare output because O06 used the 3D prompt.
- Correctly profiled live one-demo rendering is feasible for the target and
  every replay-ready targeted state that static O05 marked scoreable; across
  all 259 replay-ready rows, the static and live classifications match.
- The sealed formal-root inventory is unchanged before and after every audit
  phase: 22,434 files, SHA256
  `6eb6b3b1afa25b9837c49eea1033d09b909a35e6a509c0954fc8f1741a788e87`.
- No Qwen generation, target action, optimizer step, production edit, formal
  retry/resume, or new long run occurred.

## INFERENCES

- Aligning O06 with the arm-resolved prompt profile should remove this exact
  fatal condition and five additional wrong-profile over-context conditions
  in the replay-ready targeted set.
- The four-token render mismatch should be repaired at the same contract
  boundary so preflight and runtime share one renderer/counting API, even
  though it is not causal here.

## UNVERIFIED

- Feasibility for the 17 targeted histories that did not pass locked replay.
- Behavior, paired labels, quota evolution, and eventual 1D panel size after a
  future repair.
- Full-499 live-feasibility equivalence; it was unnecessary for this diagnosis.

## Next Task Direction

Use **Direction A: preflight source correction**, scoped more precisely as a
prompt-profile source-of-truth repair:

1. make the paired runtime consume the arm-resolved prompt profile already
   frozen in its arm config;
2. make preflight and runtime call one exact rendering/counting contract with
   explicit `enable_thinking=False`;
3. regression-test both arms, the known target, all 16 arm-differential static
   states, and fail-closed config identity;
4. preserve panel, selector, context limit, memories, and gates unchanged;
5. freeze a new source/config/contract/run identity and seek new authorization.

Do not implement typed runtime missingness or same-class substitution based on
this evidence. The selected class had no alternate live-feasible member, and
correctly profiled runtime feasibility matched O05 throughout the replay-ready
targeted set.

No production repair was implemented in R12A.

## Evidence

Git-safe evidence is under
`research/analysis/exp037a_r12a_3d_1d_first_divergence/`.
Raw stderr and AppWorld diagnostic worlds remain Lambda-only. The content-
addressed input list is `input_artifact_index.json`; the diagnostic artifact
index is `artifact_index.json`; all 276 census rows are in
`targeted_census_rows.jsonl`.

The initial known-live audit attempt failed before replay because the audit
utility referenced a nonexistent `example.task_id` attribute. That audit-only
bug was corrected to use the repository's `example_task_id()` helper. No
formal artifact or scientific behavior was affected. Two later command-start
attempts also failed before audit initialization because `_bootstrap` was not
on `PYTHONPATH`; successful final invocations included the frozen source and
scripts directories explicitly.
