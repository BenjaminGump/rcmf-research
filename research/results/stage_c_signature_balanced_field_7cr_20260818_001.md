# EXP-025C-R: Missing-Control-Aware Deployable Selector Audit

Date: 2026-08-18  
Branch: `research/v4-identity-reconciled-corpus`  
Run UUID: `signature_balanced_field_7cr_20260818_001`  
Initial implementation commit: `f92659ecfa0de7b89cbff7ddfc40bb5901f1e8ef`  
Experiment source commit: `c908c9a21ba2040bab29e760790c1879da95a972`  
Final record commit: commit containing this report  
Decision: `signature_balanced_field_selector_behaviorally_validated`

## Scope and provenance

EXP-025C-R froze the completed EXP-025C selector, three seed checkpoints,
calibration, candidate banks, scores, rankings, and selected transitions. It
introduced only the preregistered missing-row policy for one impossible F5
control and completed the one-step audit. Qwen3-8B remained frozen, every new
condition used the verified AppWorld 0.1.0 same-world bridge, and no selector,
intent probe, representation cache, program, injector, or Qwen parameter was
trained or changed.

The first preflight exposed a malformed 65-character selector hash in the
written request/config. The immutable ensemble and EXP-025C summary agree on
the canonical 64-character SHA256 below. Commit `c908c9a` corrected only that
preflight constant. The original manifest was preserved; the append-only
supersession row records old/new config hashes and
`scientific_parameter_changed=false`.

Frozen selector ensemble SHA256:
`c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`

Seed checkpoint SHA256 values:

| Seed | Checkpoint SHA256 | Score SHA256 |
| --- | --- | --- |
| 25071 | `bef5944417ddb41a230b7434393e0c3d3ca75316720c3c6bcc2e34a6a2238494` | `ce39726fecb0509f295f1717fca9d3dd5084b1a1db0cacbdf741ce9d5225ee9d` |
| 25072 | `c95e166ee32f9ba1740a097ae32119b9cf91954f51f469e66177e43218e6da9c` | `a38eb3f914d16c87ea1dd272c60b85b21d29e1b1b5b0944905682c9a8e613278` |
| 25073 | `71830292e1a8ca170fe6bdacaeeafdcffc208f2acd21f0f406158a0bda3cdaf3` | `96802233965286971843e7af8cf618fe46b80bbd212359762480ce4a8b002a82` |

The frozen selector metrics remain unchanged from EXP-025C. Strict-B B,
held-out-parent D, and deployment-E NDCG@4 are `0.7766/0.8264/0.7780`.
Strict-B state/transition-shuffled NDCG@4 is `0.2109/0.0257`; deployment-E is
`0.2065/0.0301`, giving a deployment transition-shuffle drop of `0.7683`.
All three selector gates pass, with positive held-out behavior on `9/9` tasks.

## Missing-row contract

The frozen logical manifest contains `225` slots: `45` each for F1-F5.
Exactly `224` are executable and one F5 slot is an explicit missing
measurement. No result, outcome, label, metric, or imputation exists for it.

| Field | Value |
| --- | --- |
| State | `appworld:trace:2a163ab_1:step:13:line:33` |
| Condition | `F5_predicted_intent_raw` |
| Status | `over_context_missing` |
| Reason | `selected_signature_class_has_no_context_feasible_raw_member` |
| Selected class | `procedure:046349e3bb380f803cd6bfd1545a10f26aa9cca1cf98bfa5db70b9b4772bf08f` |
| Transition | `9a6f8704-c1f5-5f99-84e4-a1e57a23ec83` |
| Class size | `1` |
| Prompt/context tokens | `41,134 / 40,960` |

Preflight verified `224/224` executable prompts, zero truncation, zero
cross-class fallback, zero changed selections, and zero executable over-context
rows. The longest executable prompt is `39,614` tokens, leaving `1,346`
tokens of headroom.

## Execution accounting

The frozen manifest reused `125` hash-identical EXP-025B outputs, resolved `36`
in-run semantic aliases, and required `63` unique new Qwen generations and
AppWorld executions. The lifecycle smoke completed all four required condition
types, same-world replay, namespace continuity, interruption/resume, atomic
write, finalization, validation, and report generation; its rows were excluded
from scientific metrics.

The formal run completed `224/224` executable logical conditions with zero
exceptions, duplicate outputs, missing executable results, replay errors, or
cross-condition contamination. No output exists for the missing F5 key.

| Runtime item | Result |
| --- | ---: |
| Formal wall time | `554.638 s` |
| Formal Qwen generation time | `136.877 s` |
| Smoke + formal allocation wall | `589.520 s` (`0.1638 H100 h`) |
| Smoke + formal generation time | `149.234 s` (`0.0415 H100 h`) |
| Artifact size | `8,704,684 bytes` |

The projected H100 range was `0.035/0.070/0.140 h`
(best/expected/conservative), below the 12-hour review threshold.

## One-step results

Primary results use the preregistered `32` non-documentation Tier-3/4 states.
F5 has `31` paired primary states; every comparison not involving F5 retains
all `32` states.

| Condition | N | Exact API | Action signature | Execution | Semantic successor |
| --- | ---: | ---: | ---: | ---: | ---: |
| C0 bare | 32 | 0.7813 | 0.3125 | 0.9375 | 0.4375 |
| C1 raw oracle | 32 | 0.9063 | 0.6563 | 1.0000 | 0.8438 |
| C3 hard negative | 32 | 0.7188 | 0.3750 | 0.9688 | 0.4688 |
| C4 popularity | 32 | 0.8125 | 0.5000 | 0.9688 | 0.6875 |
| C5 unrelated | 32 | 0.8125 | 0.3750 | 1.0000 | 0.5625 |
| F1 strict-B raw | 32 | 0.8438 | 0.6250 | 1.0000 | 0.7500 |
| F3 deployment-E raw | 32 | 0.8750 | 0.6875 | 1.0000 | 0.7813 |
| F4 deployment-E signature card | 32 | 0.6875 | 0.4063 | 0.9375 | 0.4688 |
| F5 predicted-intent raw | 31 | 0.8710 | 0.5161 | 1.0000 | 0.6129 |

All-state results use `45` states except F5 (`44` complete cases):

| Condition | N | Exact API | Action signature | Execution | Semantic successor |
| --- | ---: | ---: | ---: | ---: | ---: |
| C0 | 45 | 0.7333 | 0.3556 | 0.9333 | 0.4222 |
| C1 | 45 | 0.8222 | 0.6222 | 1.0000 | 0.7111 |
| F1 | 45 | 0.7778 | 0.6000 | 1.0000 | 0.6222 |
| F3 | 45 | 0.8000 | 0.6444 | 1.0000 | 0.6667 |
| F4 | 45 | 0.6889 | 0.4444 | 0.9556 | 0.4222 |
| F5 | 44 | 0.8182 | 0.5227 | 1.0000 | 0.5455 |

The `12` documentation-only states do not explain the primary gains. F3 on
that stratum is exact API `0.5833`, action signature `0.5833`, execution
`1.0000`, and semantic successor `0.4167`, equal to C1 on API/signature/
successor and not stronger than the non-documentation result.

## Paired task-bootstrap comparisons

Values are primary-subset mean differences with 95% task-grouped bootstrap
confidence intervals.

| Comparison | Exact API | Action signature | Execution | Semantic successor |
| --- | --- | --- | --- | --- |
| F3 - C0 | `+.0938 [0,.1875]` | `+.3750 [.2059,.5455]` | `+.0625 [0,.1613]` | `+.3438 [.2069,.4667]` |
| F3 - C1 | `-.0313 [-.0882,0]` | `+.0313 [-.0606,.1429]` | `0 [0,0]` | `-.0625 [-.1765,0]` |
| F3 - F4 | `+.1875 [.0938,.2759]` | `+.2813 [.0968,.4333]` | `+.0625 [0,.1515]` | `+.3125 [.1875,.4194]` |
| F3 - F5 (31 complete cases) | `0 [0,0]` | `+.1613 [0,.3030]` | `0 [0,0]` | `+.1613 [.0690,.2414]` |
| F3 - C3 | `+.1563 [.0333,.2813]` | `+.3125 [.0303,.5588]` | `+.0313 [0,.1034]` | `+.3125 [.0313,.5484]` |
| F3 - C4 | `+.0625 [0,.1613]` | `+.1875 [.0625,.3333]` | `+.0313 [0,.1034]` | `+.0938 [-.0667,.2813]` |
| F3 - C5 | `+.0625 [0,.1667]` | `+.3125 [.1290,.5152]` | `0 [0,0]` | `+.2188 [.1071,.3333]` |
| F1 - C0 | `+.0625 [-.0588,.1765]` | `+.3125 [.1817,.4516]` | `+.0625 [0,.1613]` | `+.3125 [.1935,.4286]` |
| F1 - F3 | `-.0313 [-.0938,0]` | `-.0625 [-.1875,0]` | `0 [0,0]` | `-.0313 [-.0938,0]` |

F3 has positive relative behavior on `8/9` tasks; F1 also has `8/9`.
The only non-positive F3 task is `7d7fbf6_2`, where the primary contrasts are
zero rather than negative.

## Missing-control sensitivity

The complete-case F3-F5 comparison is robust on semantic successor. The
one-row bounded analysis never imputes a scientific result; it asks whether
either extreme missing outcome could reverse the sign.

| Scope | Metric | Field-adverse bound | Field-favorable bound |
| --- | --- | ---: | ---: |
| Primary 32 | Exact API | 0.0000 | 0.0313 |
| Primary 32 | Action signature | 0.1563 | 0.1875 |
| Primary 32 | Execution | 0.0000 | 0.0313 |
| Primary 32 | Semantic successor | 0.1563 | 0.1875 |
| All 45 | Exact API | -0.0222 | 0.0000 |
| All 45 | Action signature | 0.1111 | 0.1333 |
| All 45 | Execution | 0.0000 | 0.0222 |
| All 45 | Semantic successor | 0.1111 | 0.1333 |

Thus the primary complete-case successor contrast is positive, its confidence
interval excludes zero, and its worst-case bound remains positive. The
predicted-intent control is conclusive under the preregistered rule.

Exact F3-F5 complete-case denominators by task are:

| Task | All-state pairs | Primary pairs |
| --- | ---: | ---: |
| `229360a_1` | 5 | 4 |
| `2a163ab_1` | 4 | 3 |
| `771d8fc_3` | 5 | 4 |
| `7d7fbf6_2` | 5 | 3 |
| `82e2fac_3` | 5 | 3 |
| `aa8502b_3` | 5 | 5 |
| `b0a8eae_1` | 5 | 3 |
| `b0a8eae_2` | 5 | 3 |
| `e85d92a_1` | 5 | 3 |

The locked clean raw-NLL comparator intersects `25` selected conditions across
`8` states. Raw-NLL versus semantic-successor effect is Pearson/Spearman
`0.2384/0.2889`; versus action-signature effect it is `-0.0341/0.0857`.
Exact-API and execution effects are constant on this subset, so their
correlations are undefined. These descriptive correlations do not explain the
field's behavioral result.

## Oracle retention and decision

Deployment-E F3 retains `0.7500` of the raw-oracle exact-API gain, `1.0909` of
the action-signature gain, and `0.8462` of the semantic-successor gain. All
three exceed the required `0.70`. Strict-B F1 retains `0.5000`, `0.9091`, and
`0.7692`, respectively, passing on two of three metrics.

VERIFIED:

- Strict-B and deployment-E selector gates remain passed with unchanged
  predictions. The behavioral audit now passes for both claims separately.
- F3 improves action signature and semantic successor over bare with
  task-bootstrap intervals excluding zero, preserves execution, and beats
  negative/popularity/unrelated controls on preregistered primary metrics.
- F3 beats its signature-only card by more than five percentage points, with
  confidence intervals excluding zero for exact API, action signature, and
  semantic successor.
- F3 retains at least 70% of the oracle gain on all three retention metrics.
- The single F5 missing row does not reverse the predicted-intent conclusion.

INFERENCE:

- The clean signature-balanced field can automatically select raw episodic
  transition content whose one-step behavioral value substantially retains
  the procedural oracle benefit. The useful effect does not collapse to the
  signature card, transition frequency, or the clean intent-only baseline.

UNVERIFIED:

- No state-conditioned transition program, program compiler, additive-token
  injector, Stage C2 model, full ReAct trajectory, or end-to-end RCMF system
  was trained or evaluated. One-step selection does not establish those later
  claims.

Decision branch:

`signature_balanced_field_selector_behaviorally_validated`

Automatic field selection is behaviorally validated under the clean 45-state
one-step contract. `p(s,m_transition)` remains blocked pending a separately
reviewed milestone. The recommended next milestone is an explicitly
preregistered state-conditioned transition-program distillation study using
the frozen clean selector and clean behavioral target, while preserving
strict-B and deployment-E as separate claims.

## Validation and artifacts

The final independent validator passed all manifest, hash, duplicate,
missingness, complete-case, output, and metric checks. The final summary SHA256
is `1590e6e0bdd77f877cfb865adfa289bb869d7ff4131d359ee8497b3af79fb719`;
the generation summary SHA256 is
`74b62ade45d5148ac6287c31f558d3690909841390888c1d8e3ff610cbce67b0`.

Lambda artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/signature_balanced_field_7cr_20260818_001`

Key artifacts:

- `attempts.jsonl`
- `run_manifest.json` and `run_manifest_supersessions.jsonl`
- `selector_audit_preflight.json` and `selector_condition_manifest.json`
- `missing_f5_record.json`
- `lifecycle_smoke/smoke_summary.json`
- `selector_generation_summary.json`
- `deployable_one_step_metrics.json`
- `deployable_causal_comparisons.json`
- `complete_case_missing_bound_report.json`
- `oracle_retention_report.json`
- `raw_vs_signature_card_report.json`
- `strict_b_vs_deployment_e_report.json`
- `clean_raw_nll_field_outcome.json`
- `final_exp025cr_summary.json`
- `postrun_validation.json`

The append-only ledger contains eight closed attempts and 16 start/end events.
Two preflight/validation failures were operator transcription errors, one
preflight recorded the malformed request hash, and all are preserved. No
scientific parameter changed and no duplicate run was started after network
reconnection.
