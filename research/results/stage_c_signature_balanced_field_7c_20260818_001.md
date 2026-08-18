# EXP-025C Signature-Balanced Procedural Field Selector

## Outcome

- Run UUID: `signature_balanced_field_7c_20260818_001`
- Branch: `research/v4-identity-reconciled-corpus`
- Starting SHA: `d2dd315260906aa1aec71ff4fc9e58f297c506b1`
- Final source SHA: `4dabb51c84a0886fadb69bbd43d9941d482c72e5`
- Decision: `clean_corpus_behavioral_audit_infrastructure_invalid`
- Strict-B selector gate: passed
- Deployment-E selector gate: passed
- Held-out-parent D check: passed
- Deployable one-step behavioral audit: not run
- Automatic field selection behaviorally validated: no

The signature-balanced selector itself passed every preregistered ranking and
generalization check. The conditional one-step phase stopped before loading
Qwen because the locked `45 x 5` condition manifest cannot be constructed
without violating the no-truncation and same-signature-class rules. One F5
predicted-intent raw condition has no context-feasible member in its selected
signature class.

## Verified Scope

VERIFIED:

- The clean replay-validated EXP-025B lineage and all immutable parent hashes
  passed validation.
- Clean multiview caches were assembled for all `638` states and `499` train
  transitions. Only `35` state and `355` transition rows required new frozen-
  Qwen forwards; all reuse hashes and lineage checks passed.
- Signature-class balancing gives equal total mass to every train state and to
  every legal signature class within a state.
- The clean action-intent probe and the three-seed low-rank field completed.
- B, D, and E selector gates passed before any one-step output was inspected.
- No Qwen generation, AppWorld condition reconstruction/execution, lifecycle
  smoke, oracle-retention analysis, or F1-F5 behavioral comparison ran.

INFERENCE:

- Procedural compatibility is learnable by the field-compatible architecture
  on the clean corpus, including state and transition shuffle sensitivity.
- This is not evidence that the selected raw transition preserves the EXP-025B
  causal benefit; that requires the blocked one-step phase.

UNVERIFIED:

- F1/F3/F4/F5 behavioral outcomes, oracle gain retention, raw-versus-card
  content advantage under automatic selection, and p(s,m_transition) remain
  unverified.

## Data And Class Balance

| Cell | States | Transitions | Classes | Legal pairs | Tier 0/1/2/3/4 |
|---|---:|---:|---:|---:|---|
| A train state / train parent | 499 | 413 | 141 | 199,116 | 151,200 / 17,692 / 16,793 / 3,011 / 10,420 |
| B held-out state / train parent | 139 | 413 | 141 | 57,407 | 42,470 / 5,293 / 5,111 / 863 / 3,670 |
| C train state / held-out parent | 499 | 86 | 35 | 41,956 | 31,744 / 3,894 / 3,631 / 389 / 2,298 |
| D held-out state / held-out parent | 139 | 86 | 35 | 11,954 | 8,824 / 1,179 / 1,071 / 97 / 783 |
| E held-out state / all parents | 139 | 499 | 150 | 69,361 | 51,294 / 6,472 / 6,182 / 960 / 4,453 |

The full Cartesian set contains `318,362` pairs: `7,929` illegal and `310,433`
legal. Class balancing covers `68,726` state-class groups. Expected state mass
is `0.002004008016`; maximum state-mass error is `7.37e-18`, maximum within-
state class-mass spread is `5.42e-20`, and total-mass error is `5.23e-13`.

## Clean Intent Probe

The train-only probe selected `60` epochs. On `139` held-out decisions:

| Target | Strict accuracy | Balanced accuracy | ECE | Brier | NLL |
|---|---:|---:|---:|---:|---:|
| App | 0.8489 | 0.8124 | 0.1415 | 0.2782 | 1.1237 |
| API | 0.8058 | 0.8396 | 0.1748 | 0.3252 | 1.3568 |
| Action type | 0.8633 | 0.8255 | 0.1269 | 0.2466 | 1.0906 |
| Completion | 0.9856 | 0.9923 | 0.0125 | 0.0224 | 0.0324 |

Mean strict accuracy is `0.8759`; the correct-minus-shuffled-state mean
accuracy gap is `0.4640`. API train-vocabulary coverage is `137/139`.

## A-Only Selection And Seeds

Grouped CV inspected only cell A. Candidate NDCG@4 values were `0.7220`,
`0.7260`, and `0.7289`; the selected fixed configuration was
`hard_lr3e4_e120_t075` (`lr=3e-4`, `120` epochs, temperature `0.75`). B/C/D/E
were not inspected during selection.

Each final seed completed exactly `120` epochs and `7,560` optimizer updates:

| Seed | B NDCG@4 / Tier-3/4 R@4 / API R@4 | D NDCG@4 | E NDCG@4 / Tier-3/4 R@4 / API R@4 |
|---:|---|---:|---|
| 25071 | 0.7856 / 0.7986 / 0.8345 | 0.8284 | 0.7817 / 0.7986 / 0.8345 |
| 25072 | 0.7815 / 0.7698 / 0.8129 | 0.8190 | 0.7812 / 0.7698 / 0.8129 |
| 25073 | 0.7653 / 0.7482 / 0.7914 | 0.8077 | 0.7648 / 0.7482 / 0.7914 |

The calibrated ensemble SHA256 is
`c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`.

## Selector Evaluation

| Cell | NDCG@4 | Tier-3/4 R@4 | API R@4 | Same-intent pairwise | Spearman | Top-1 Tier-3/4 |
|---|---:|---:|---:|---:|---:|---:|
| B | 0.7766 | 0.7626 | 0.8129 | 0.9034 | 0.4374 | 0.7266 |
| C | 0.9491 | 0.7435 | 0.8397 | 0.9109 | 0.5608 | 0.6052 |
| D | 0.8264 | 0.6978 | 0.7626 | 0.8760 | 0.4894 | 0.5036 |
| E | 0.7780 | 0.7698 | 0.8129 | 0.8964 | 0.4304 | 0.7194 |

Strict-B NDCG@4 gains are `+0.40799` over transition-only, `+0.60701` over
state shuffle, and `+0.77153` over transition shuffle. The transition-shuffle
task-bootstrap 95% CI is `[0.69747, 0.85197]`; all `9/9` tasks are positive.

Deployment-E gains are `+0.40838`, `+0.61345`, and `+0.76833` respectively;
the transition-shuffle CI is `[0.70130, 0.84206]`, with `9/9` positive tasks.
D gains `+0.41834` over transition-only and `+0.72605` over transition shuffle.

Selected-class diversity is `49` classes over `24` parents in B and `49`
classes over `26` parents in E. Maximum class shares are `18.71%` and `17.99%`;
API-documentation selection is `43.88%` in each. D selects `25` classes across
`7` parents, with maximum share `36.69%`.

## Behavioral Preflight Blocker

The intended manifest contains `225` logical conditions: `45` each for F1-F5.
F1 strict-B raw and F3 deployment-E raw have a context-feasible same-class
exemplar for every state. Signature cards F2/F4 are structurally available.
F5 has one non-substitutable failure:

- state: `appworld:trace:2a163ab_1:step:13:line:33`
- audit stratum: B, primary non-documentation Tier-3/4 state
- selected class:
  `procedure:046349e3bb380f803cd6bfd1545a10f26aa9cca1cf98bfa5db70b9b4772bf08f`
- class size: `1`
- transition: `9a6f8704-c1f5-5f99-84e4-a1e57a23ec83`
- selected-class procedure: `phone.login` then
  `phone.show_contact_relationships`
- raw prompt tokens: `41,134`
- locked context limit: `40,960`
- overflow before generation: `174` tokens
- same-class scoreable alternate: none

Cross-class fallback, truncation, or changing the context/model contract would
alter the preregistered audit. The attempt therefore stopped before manifest
freeze, smoke, model load, generation, or AppWorld execution. No partial
`224/225` result was treated as scientific evidence.

## Runtime And Artifacts

- Multiview cache: `438.04 s` (`0.1217` H100 h)
- Selector phase: `7,913.67 s` (`2.1982` observed GPU wall h)
- Total observed GPU wall-equivalent: about `2.3199 h`
- One-step Qwen generations/AppWorld executions: `0/0`
- Artifact size at stop: `1,929,412,599` bytes
- Attempts: `7` closed attempts, `14` ledger events, no open attempt

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/signature_balanced_field_7c_20260818_001`

Key files are `data_preparation_summary.json`,
`representation_cache/multiview/clean_multiview_cache_summary.json`,
`clean_intent_probe/clean_intent_summary.json`,
`selector/a_only_cv/a_only_cv_report.json`, `selector/selector_summary.json`,
`selector/ensemble_scores.pt`, and `attempts.jsonl`.

## Decision

Reached `clean_corpus_behavioral_audit_infrastructure_invalid` at the locked
condition/context preflight. This does **not** retract the verified selector
gates and does **not** validate automatic behavioral selection.

The next review should preregister exactly one treatment for an otherwise
unscoreable selected class, without retraining the selector: either preserve a
missing F5 paired row, adopt a deterministic score-ranked context-feasible
fallback, or separately validate a larger Qwen context contract. Until that
review, p(s,m_transition), program/compiler, injector, Stage C2, end-to-end
RCMF, and V4 tagging remain blocked.
