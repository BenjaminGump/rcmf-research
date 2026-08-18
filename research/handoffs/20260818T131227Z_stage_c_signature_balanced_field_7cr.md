# Structured Handoff: EXP-025C-R

## Identity

- Run UUID: `signature_balanced_field_7cr_20260818_001`
- Branch: `research/v4-identity-reconciled-corpus`
- Parent EXP-025C HEAD: `841cbe179d0d577e9eb9cd4e37299cb9b123915f`
- Initial implementation commit: `f92659ecfa0de7b89cbff7ddfc40bb5901f1e8ef`
- Experiment source commit: `c908c9a21ba2040bab29e760790c1879da95a972`
- Final record commit: commit containing this handoff
- Clean lineage: `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/signature_balanced_field_7cr_20260818_001`

## Recovery and attempts

- Network reconnection used the verified Lambda host and inspected the existing
  project before launch. No duplicate run UUID or condition output was created.
- The run manifest remains immutable. A provenance-only supersession records
  correction of a malformed 65-character expected selector SHA to the
  immutable 64-character artifact SHA; scientific parameters did not change.
- `attempts.jsonl` contains eight closed attempts and 16 events, no open
  attempt. Failed rows record the malformed request hash and two operator
  transcription errors. Smoke, formal, analysis, and final validation passed.

## Frozen inputs

- Ensemble SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Three seed checkpoint hashes:
  `bef5944417ddb41a230b7434393e0c3d3ca75316720c3c6bcc2e34a6a2238494`,
  `c95e166ee32f9ba1740a097ae32119b9cf91954f51f469e66177e43218e6da9c`,
  `71830292e1a8ca170fe6bdacaeeafdcffc208f2acd21f0f406158a0bda3cdaf3`.
- EXP-025C strict-B, deployment-E, and held-out-parent D selector gates remain
  passed; no score, calibration, ranking, class, or transition was changed.

## Condition accounting

- Logical slots: `225` (`45` each F1-F5)
- Executable: `224`; explicit missing: `1`
- Reused EXP-025B outputs: `125`
- In-run aliases: `36`
- New Qwen/AppWorld conditions: `63`
- Formal outputs: `224/224`; replay/execution infrastructure exceptions: `0`
- Missing key has no output or imputed value.
- Missing row: state `appworld:trace:2a163ab_1:step:13:line:33`, F5,
  class `procedure:046349e3bb380f803cd6bfd1545a10f26aa9cca1cf98bfa5db70b9b4772bf08f`,
  transition `9a6f8704-c1f5-5f99-84e4-a1e57a23ec83`, singleton class,
  `41,134/40,960` tokens.

## Primary verified metrics

On 32 non-documentation Tier-3/4 states:

| Condition | Exact API | Signature | Execution | Successor |
| --- | ---: | ---: | ---: | ---: |
| C0 bare | .7813 | .3125 | .9375 | .4375 |
| C1 oracle | .9063 | .6563 | 1.0000 | .8438 |
| F1 strict-B raw | .8438 | .6250 | 1.0000 | .7500 |
| F3 deployment-E raw | .8750 | .6875 | 1.0000 | .7813 |
| F4 deployment signature card | .6875 | .4063 | .9375 | .4688 |
| F5 predicted-intent raw (n=31) | .8710 | .5161 | 1.0000 | .6129 |

- F3-C0: API `+.0938`, signature `+.3750`, execution `+.0625`, successor
  `+.3438`; signature and successor task-bootstrap intervals exclude zero.
- F3-F4: API `+.1875`, signature `+.2813`, successor `+.3125`; all three
  intervals exclude zero.
- F3-F5 complete-case successor: `+.1613`, 95% CI `[.0690,.2414]`; the
  adverse one-row bound remains `+.1563`.
- Deployment oracle retention: API `.7500`, signature `1.0909`, successor
  `.8462`. Strict-B retention: `.5000/.9091/.7692`.
- F3 and F1 each show positive relative behavior on `8/9` tasks.
- Documentation-only states do not drive the primary result.

## Scientific interpretation

VERIFIED: the clean signature-balanced field selector retains the required
one-step oracle benefit in deployment-E and strict-B, beats its signature-only
card and required controls, and passes the missing-control robustness rule.

INFERENCE: automatic field selection captures useful raw episodic transition
content beyond procedural metadata or intent-only retrieval.

UNVERIFIED: no state-conditioned program, compiler, injector, Stage C2,
end-to-end RCMF, or full trajectory behavior was tested.

Decision branch:
`signature_balanced_field_selector_behaviorally_validated`.

Automatic selector behavior is validated. `p(s,m_transition)` remains blocked
until separate review. Recommend a separately preregistered EXP-025D
state-conditioned transition-program distillation milestone using the frozen
clean selector and behavioral target, with strict-B and deployment-E reported
separately.

## Operational closeout

- Formal wall: `554.638 s`; smoke plus formal allocation wall: `0.1638 H100 h`.
- Artifact size: `8,704,684 bytes`.
- Final validator: passed; summary SHA256
  `1590e6e0bdd77f877cfb865adfa289bb869d7ff4131d359ee8497b3af79fb719`.
- At handoff, no EXP-025C-R Python process remains. The `exp025cr` tmux session
  is idle and the GPU is unused. The experiment artifacts are atomic and it is
  safe to terminate the Lambda instance after final Git synchronization.
