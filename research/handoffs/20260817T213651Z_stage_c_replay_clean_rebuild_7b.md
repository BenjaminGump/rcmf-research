# EXP-025B Structured Handoff

## Identity

- Run UUID: `replay_validated_clean_rebuild_7b_20260818_001`
- Branch: `research/v4-identity-reconciled-corpus`
- Starting SHA: `d74b8e9aa1c15dce1516e5a43e53e20b2970cc38`
- Final implementation/analysis source SHA:
  `38be6cab6d5fae6aa60a9cbd7aeab65014b9f63b`
- Final record SHA: the commit containing this handoff
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/replay_clean_rebuild_7b_20260818_001`

## Verified

- Root-login JWT semantic-v3 replay passed 13-state, 3-state, and full
  45-state gates twice, including `372/372` priors and zero exceptions.
- Clean-cache rebuild and merged-cache validation passed. Final Qwen-scoring
  recomputation was `3,963` rows plus `35/2/17` state/memory/transition
  representations; no checkpoint was retrained.
- The condition manifest has `323` conditions over `45` states and `9` tasks.
- Lifecycle smoke and live bridge passed. Formal generation/execution completed
  `323/323` unique conditions with zero infrastructure exceptions.
- On 32 primary states, C1-C0 is `+0.1250` exact API, `+0.3438` action
  signature, `+0.0625` execution, and `+0.4063` semantic successor.
- C1-C2 is `+0.2188` exact API and `+0.3125` action signature, with confidence
  intervals excluding zero. Positive behavior occurs on `7/9` tasks.
- Same-signature effect direction is `86.49%`; API and execution agreement are
  `97.30%`.

## Inference

- Raw episodic transition content provides one-step behavioral information
  beyond the normalized procedural metadata card under oracle selection.

## Unverified

- No deployable field, p(s,m_transition), injector, selector, Stage C2, full
  trajectory, or end-to-end RCMF result exists.
- Raw-NLL/outcome analysis is underpowered at 16 conditions and 8 states.

## Decision

- Reached `raw_transition_content_behaviorally_validated_on_clean_corpus`.
- Raw transition content: validated for the clean oracle one-step gate.
- Field/program training: remains blocked pending separate EXP-025C review.
- V4 remains a candidate; no tag was created or moved.

## Next Review

Proposed EXP-025C should train a signature-class-balanced field predictor with
inverse-frequency weights, API-documentation stratification, separate strict-B
and deployment-E gates, and a deployable top-transition one-step audit. It must
not automatically start p(s,m_transition), injector, selector, Stage C2, or
end-to-end training.

## Operational State

- Attempt ledger: `22` closed attempts, `44` events, no open attempt.
- Tmux: `exp025b` alive at an idle shell after completion.
- Active experiment Python process: none.
- GPU: idle, `0 MiB`, `0%` utilization after completion.
- Safe to terminate the Lambda instance after final Git sync: yes.
