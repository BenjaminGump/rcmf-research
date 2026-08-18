# Structured Handoff: EXP-025D Runtime Preflight

## Identity

- Run UUID: `state_conditioned_transition_program_7d_20260818_001`
- Archive branch/SHA: `archive/v4-selector-behaviorally-validated` at
  `f05a40dfc095fa3ec655441642b0548ef382cf10`
- Program branch initial SHA: `f05a40dfc095fa3ec655441642b0548ef382cf10`
- Initial implementation commit: `ef552844ec28964deff7587d2a51343c28a3fa5c`
- Preflight source commit: `cd5341412842137be0a250ff2f744c4f309e2b30`
- Final preflight record commit: commit containing this handoff
- Parent run: `signature_balanced_field_7cr_20260818_001`
- Artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_transition_program_7d_20260818_001`

## Recovery and attempts

Five preflight attempts are closed with one start and one end event each.

1. Attempt 001 exposed forbidden post-selection context substitution.
2. Attempt 002 confirmed remaining exemplar mismatches and led to the finding
   that a sorted token-count distribution had been incorrectly zipped to
   transition IDs.
3. Attempt 003 reproduced all frozen choices but rejected the corrected
   transition-token input because its hash differed from the original run
   manifest.
4. Attempt 004 rejected an operator-transcribed malformed prior hash.
5. Attempt 005 recorded the exact single-key `transitions` data-manifest
   supersession and completed normally.

The original run manifest is byte-identical to its first attempt. The one-row
append-only supersession records `transitions` as the only changed input key,
the original manifest SHA matches, and `scientific_parameter_changed=false`.
No attempt loaded Qwen or used the GPU.

## Exact preflight result

- Logical A/B/C/D/E: `640/139/112/112/139`.
- Scoreable A/B/C/D/E: `607/135/112/112/135`.
- Over-context occurrences: `33/4/0/0/4`; no truncation or replacement.
- Frozen B/D/E transition identity: `139/139`, `112/112`, `139/139`.
- Decoder split: `192` calibration, `64` grouped-heldout, zero state overlap.
- Sparse-teacher cache: `970` unique new rows, `0` complete reusable rows;
  `17` scalar overlaps are insufficient.
- Expected updates: decoder `16,384`; program `352,704`.
- Expected H100 time: `201.72 h`; expected wall including records: `203.72 h`.
- Best/conservative H100: `144.13/518.42 h`.
- Projected storage: `21,462,500,000` bytes.

## Scientific status

VERIFIED: immutable clean inputs, frozen selector identity, pair construction,
context accounting, decoder split, field algebra, zero/raw-input student
contract, update schedule, cache accounting, and resumability preflight pass.

UNVERIFIED: no clean decoder capacity, state-conditioned program fidelity,
B/C/D/E teacher-forced metrics, ablation, shuffle, or compiled one-step result
exists yet.

No scientific decision branch has been reached. The state-conditioned program
is not validated. Full-bank training, Stage C2, end-to-end RCMF, and V4 tagging
remain blocked.

## Required next action

The 12-H100-hour threshold requires explicit approval. After approval, resume
this same run UUID from the validated preflight summary and execute the full
prespecified experiment unchanged. Do not reduce pairs, updates, seeds,
architectures, controls, or held-out cells.

At handoff no `exp025d` tmux session or EXP-025D Python process remains, the
H100 is idle, all preflight artifacts are atomic, and the Lambda instance is
safe to terminate while approval is pending.

