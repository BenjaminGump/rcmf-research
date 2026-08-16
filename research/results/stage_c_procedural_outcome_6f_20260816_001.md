# EXP-022 Procedural and One-Step Outcome Supervision Audit

## Status

- Milestone: 6F / EXP-022.
- Run UUID: `procedural_outcome_6f_20260816_001`.
- Branch: `research/v4-decision-transition-memory`.
- Source/validation commit: `1c9ed7fca9517e0cf75b5589862d60674a17c4da`.
- Final record commit: the commit containing this report.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_outcome_6f_20260816_001`.
- Decision branch: `transition_panel_procedural_coverage_insufficient`.
- Procedural supervision validated: no.
- Behavioral `p(s,m_transition)` remains blocked.
- V4 remains a candidate; no V4 tag was created or moved.

## Scope And Immutable Contract

EXP-017, EXP-020, and EXP-021 remained read-only. The immutable contract was
reproduced exactly: 92 query states (74 train / 18 held out), 148 transitions,
13,320 legal rows, 13,128 scoreable rows, 192 over-context rows, and scoreable
A/B/C/D cells of `8,205/2,051/2,296/576`.

The parser and label compiler made zero Qwen forward calls and created zero
AppWorld instances. No field model, behavioral program, injector, selector,
production field, Stage C2, end-to-end model, generated action, full trajectory,
new query, new transition, demo change, injection change, or V4 tag was run.
The locked raw-NLL cache was not rewritten.

## Procedural Parser

All 638 successful query next actions and all 148 transition actions parsed via
Python AST. Regex fallback count was zero; no action was dropped. Every query
target exactly matched its source successful trajectory step. Signature scans
found no raw email, phone-number, credential, token, or ID value leakage.

Query action types were: API documentation 275, API other 20, authentication
59, completion 46, Python reasoning 47, read/query 162, and write/mutation 29.
Transition action types were: API documentation 61, API other 12,
authentication 8, Python reasoning 12, read/query 41, and write/mutation 14.

The 148-transition panel contains 61 API-doc, 34 Spotify, 14 supervisor, 12
file-system, 6 phone, 6 Simple Note, 3 Venmo, and 12 no-API transitions. One
transition has no scoreable pair after existing over-context masking; the
validator reports the complete 148-signature distribution separately from the
147 transitions represented in scoreable rows.

## Procedural Labels

| Cell | Tier 0 | Tier 1 | Tier 2 | Tier 3 | Tier 4 | Tier-3/4 states | Exact-API states |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 6,103 | 1,145 | 494 | 186 | 277 | 60/74 (81.08%) | 64/74 (86.49%) |
| B | 1,560 | 284 | 97 | 27 | 83 | 12/18 (66.67%) | 14/18 (77.78%) |
| C | 1,639 | 368 | 202 | 30 | 57 | 41/74 (55.41%) | 52/74 (70.27%) |
| D | 422 | 89 | 39 | 6 | 20 | 9/18 (50.00%) | 11/18 (61.11%) |

Hard same-intent comparisons exist for A/B/C/D in
`27,029/6,654/2,457/615` pairs, covering `74/17/63/16` states respectively.

The six held-out-state/train-transition states without any Tier-3/4 candidate
are:

- `229360a_1` step 12: Python reasoning/no API, maximum tier 1.
- `2a163ab_1` step 13: `phone.login`, maximum tier 1.
- `2a163ab_1` step 14: `phone.show_contact_relationships`, maximum tier 2.
- `771d8fc_3` step 9: `phone.search_voice_messages`, maximum tier 2.
- `b0a8eae_1` step 9: `simple_note.show_note`, maximum tier 2.
- `b0a8eae_1` step 16: `spotify.show_playlist`, maximum tier 2.

No state was substituted, no third state from another task was used, and the
fixed panel was not expanded.

## Gate And Stop

Gate 1 required at least 70% of held-out query states to have one legal
Tier-3/4 candidate. The observed value is `12/18 = 66.6667%`, a shortfall of
one covered state. The gate therefore failed before model training.

Consequently, A-only grouped CV, B/C/D field metrics, state/transition shuffle
controls, AppWorld replay preflight, Qwen generation, and the one-step outcome
audit were intentionally not run. Their actual counts are all zero. The
deterministic 45-state audit manifest was prepared before the gate decision
(five states from each of nine tasks, including all 18 EXP-020 held-out
states), but no condition manifest was executed.

No raw-NLL/outcome correlation exists for EXP-022 because there are no one-step
outcome rows. Raw-NLL remains a valid secondary comparator from EXP-021, not
the primary deployment teacher.

## Recovery And Validation

Four append-only attempts share one run UUID and all record
`scientific_parameter_changed=false`:

1. Attempt 001 stopped on an overbroad credential scanner.
2. Attempt 002 added redacted paths and showed UUID numeric segments, not
   signature content, caused the false positives.
3. Attempt 003 restricted scanning to signature payloads and wrote the complete
   cache, then stopped on an invalid `AttemptLedger.checkpoint` bookkeeping
   call.
4. Attempt 004 used the tested `progress(latest_validated_checkpoint=...)`
   API and completed from the existing preflight summary.

No laptop/network disconnect created a duplicate run. The final independent
validator passed `20/20` checks: counts, hashes, unique pair keys, tier range,
credential safety, behavior-manifest identity, closed attempt pairs, unchanged
scientific parameters, and absence of model/replay artifacts.

Local tests: `254 passed, 1 skipped`. Lambda focused tests: `16 passed`; the
final Lambda full suite passed `255`. H100 use was `0.0 h`; final artifact size
before the record commit was `27,282,707` bytes (about `26.02 MiB`).

## Interpretation

VERIFIED: deterministic procedural signatures and labels can be compiled for
the locked cache without leakage, but the fixed 148-transition panel does not
meet the preregistered held-out Tier-3/4 coverage requirement.

INFERENCE: model and one-step behavioral results from this panel would not be a
fair test of procedural supervision because one third of held-out states lack
the required high-tier candidate.

UNVERIFIED: expanding from the 148 panel to all 499 legal training transitions
may repair coverage. That expansion requires a separately reviewed milestone
with exact pair/context/runtime preflight before any model or AppWorld work.

## Artifacts

Key Lambda files are `run_manifest.json`, `attempts.jsonl`, `heartbeat.json`,
`procedural_signatures.jsonl`, `procedural_label_rows.jsonl`,
`one_step_query_manifest.json`, `preflight_summary.json`,
`postrun_validation.json`, and `final_exp022_summary.json`.

At final source audit there was no tmux server or Python experiment process;
the H100 was at `0% / 0 MiB`.
