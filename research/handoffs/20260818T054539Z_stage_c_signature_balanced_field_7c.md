# EXP-025C Structured Handoff

## Identity

- Run UUID: `signature_balanced_field_7c_20260818_001`
- Branch: `research/v4-identity-reconciled-corpus`
- Starting SHA: `d2dd315260906aa1aec71ff4fc9e58f297c506b1`
- Final source SHA: `4dabb51c84a0886fadb69bbd43d9941d482c72e5`
- Final record SHA: the commit containing this handoff
- Parent: `replay_validated_clean_rebuild_7b_20260818_001`
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/signature_balanced_field_7c_20260818_001`

## Verified

- Clean data and class balance pass over `310,433` legal pairs.
- The clean intent probe reaches `0.8759` mean strict held-out accuracy.
- Three final field seeds complete `120` epochs and `7,560` updates each.
- Ensemble B/D/E NDCG@4 is `0.7766/0.8264/0.7780`.
- Strict-B and deployment-E selector gates pass, as does held-out-parent D.
- B/E correct-minus-transition-shuffle NDCG@4 is `0.7715/0.7683`, with
  confidence intervals excluding zero and `9/9` positive tasks.

## Blocker

The conditional behavior phase cannot construct its required `225`-condition
manifest. F5 predicted intent selects a singleton raw class for
`2a163ab_1` step 13 whose prompt is `41,134` tokens, `174` above the locked
limit. There is no same-class alternate. Truncation and cross-class replacement
are forbidden, so no smoke, Qwen generation, or AppWorld condition execution
ran.

## Decision

- Reached `clean_corpus_behavioral_audit_infrastructure_invalid`.
- Procedural selector ranking/generalization: validated for the preregistered
  selector gates.
- Automatic selection behavioral retention: not validated.
- p(s,m_transition) and all later training remain blocked.

## Attempts And Recovery

The ledger contains `7` closed attempts and `14` events. Three preflight
attempts exposed renderer/config/count provenance checks; their fixes changed
no scientific parameter. Multiview and selector attempts completed. The final
behavioral preflight failed atomically before model load. No disconnect created
a duplicate run.

## Next Review

Preregister a narrow EXP-025C-R context-feasibility amendment without
retraining or changing scores. It must choose explicitly among a missing F5
row, deterministic score-ranked feasible fallback, or a separately validated
larger context contract. Only then may the frozen 45-state behavioral audit be
resumed.

## Operational State

- Active EXP-025C Python process: none
- `tmux: exp025c`: not alive after the atomic preflight stop
- GPU: idle, `0 MiB`, `0%`
- Safe to terminate Lambda after final Git sync: yes
