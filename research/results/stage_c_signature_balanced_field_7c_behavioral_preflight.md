# EXP-025C Behavioral Preflight

The selector gates passed, but the F1-F5 audit did not start.

The locked manifest requires `225` conditions over `45` states. Exactly one
F5 predicted-intent raw condition is impossible under the existing contract:
`2a163ab_1` step 13 selects a singleton signature class whose raw prompt is
`41,134` tokens against a `40,960` limit. There is no legal scoreable member in
the same class. F1 and F3 are scoreable on all 45 states.

The preflight correctly rejected truncation and cross-class substitution.
Consequently:

- selector-condition manifest: not frozen;
- lifecycle smoke: not run;
- F1/F3/F4/F5 Qwen generations: `0`;
- AppWorld reconstructions/executions: `0`;
- oracle-retention and raw-versus-card behavioral reports: not estimable.

Decision: `clean_corpus_behavioral_audit_infrastructure_invalid`.

The complete failed attempt is `exp025c-audit-preflight-001` in the append-only
ledger. Its latest validated checkpoint is `selector/selector_summary.json`.
