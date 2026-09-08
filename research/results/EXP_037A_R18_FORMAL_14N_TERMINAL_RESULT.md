# EXP-037A-R18 Formal 14n Terminal Result

## Decision

`FORMAL_EXP037A_14N_CONTINUATION_COMPLETE_NEGATIVE_ONE_DEMO_RESULT`

The self-healing loop stops at the completed F03 result. No follow-on run,
optimization, confirmation, or new scientific direction was launched.

## Verified execution

- Launch source: `98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`.
- Frozen archive: `archive/exp037a-r18-launch-source-98f917d`.
- Run UUID:
  `rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`.
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`.
- Config SHA256:
  `6e4be2be11e608436f5b5ebfdeee45d94a61f2831c841be72cd97e8050107e01`.
- Contract SHA256:
  `e479889fba498401e50ff7f309668629c3745d8ea5d91d84d8600c62986e61c7`.
- Explicit authorization SHA256:
  `dd8ccaab2be4bd33ad88f9d1746f758060fa6dd44ef71feb16214d28a6e747f8`.
- Runtime authorization SHA256:
  `3039410968805ed1e671db21363300256733232e827c372b2a8103a2ae9854f1`.
- Runtime authorization passed the frozen validator with continuation-only
  scope and a 32-hour cap. No prior authorization was reused.
- Formal wall span was `32,884.564264 s` (`9.134601 h`), from
  `2026-09-07T16:18:08.983597Z` to `2026-09-08T01:26:13.547861Z`.
- All 18 authorized stages completed on first attempt. There were zero failed,
  interrupted, retried, skipped, or open attempts.
- The production strict validator passed `18/18` stage completions. F03 passed,
  the orchestrator returned `complete`, the formal tmux exited normally, and
  terminal H100 utilization/memory were `0% / 0 MiB`.

## Parent provenance

- Parent run: `rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001` at
  source `004f866647cfabb38a141b88e6d83821df88c403`.
- Parent boundary: after O07 and before O08.
- The parent manifest SHA256 is
  `c4cc6501982ec2b8ff9bc7061ad5ed649476d69ad606ac3e39229f16c8768a62`.
- The complete parent closure revalidated after terminal completion: `922/922`
  files and closure SHA256
  `f5424356e1ae469f2533136c37e28d8864d43e83c26902ed136b164104f3b0b6`.
- Parent D22 remained `THREE_DEMO_REPRODUCTION_PASS`.
- Parent O08 partials, 14l/14m stages, diagnostic checkpoints, and old
  authorizations were not used as formal 14n inputs.
- This is explicitly `cross_source_provenance_validated_continuation`, not a
  single-source fresh S00-F03 rerun.

## One-demo construction

- O08 independently validated the sealed one-demo population: `324` model
  train, `83` heldout, and `407` paired states with labels
  `120 POSITIVE / 247 NEUTRAL / 40 HARMFUL`.
- Training completed exactly `1,032` units and backward passes, two epochs,
  with Qwen frozen and no runtime retrieval.
- Epoch 1 was selected using heldout-train evidence only. Its checkpoint SHA256
  is `c3bc33f38dd0ea58e368c38f582775c355902bb9a01671f21e4e485add9daf2c`.
  Epoch 2 SHA256 is
  `1db373ba6399aac1adcd6fd721a05e79f3a9acfc39d8a03f2e0a5d8c49ea1457`.
- The selected 401-memory field SHA256 is
  `bec6ae119ffd858a07eeafafbb65f55e42b39a0f71d224c3dd128afae0cf22af`.
- Feed-forward addition of 98 heldout-parent memories produced the validated
  499-memory deployment field SHA256
  `3bda71b91f7e1f5147a8399c5fdcb41207869ffbd5f0236bfbdecaa88d4c4110`.
  No retraining or optimizer step occurred during instant add.

## Dev result

All conditions used the same ordered 57-task official AppWorld dev set.

| Condition | Successes |
|---|---:|
| Shared bare `B0_1D` | 12/57 |
| Fresh 3D correct field | 17/57 |
| Fresh 3D matched shuffle | 11/57 |
| Fresh 1D correct field | 8/57 |
| Fresh 1D matched shuffle | 18/57 |

Paired descriptive comparisons:

- 1D correct minus bare: `-4/57`, with `1` correct-only and `5` bare-only
  successes; exact two-sided McNemar `p = 0.21875`.
- 1D correct minus 1D shuffle: `-10/57`, with `2` correct-only and `12`
  shuffle-only successes; exact two-sided McNemar `p = 0.012939453125`.
- 1D correct minus 3D correct: `-9/57`, with `4` one-demo-only and `13`
  three-demo-only successes; exact two-sided McNemar `p = 0.049041748046875`.
- The sealed 3D positive-control directions remain `+5/57` versus bare and
  `+6/57` versus its matched shuffle.

## Interpretation

VERIFIED:

- The fresh one-demo correct field underperformed both the shared bare
  condition and its own matched shuffled control.
- The 3D positive control remained valid, and the complete continuation
  executed without infrastructure exception or artifact reuse.

INFERENCE:

- This is a clearly negative one-demo memory-specificity result under the
  intended causal direction. The one-demo retraining path did not preserve the
  positive correct-field behavior of the three-demo control.
- The especially strong reversed correct-versus-shuffle ordering is evidence
  against attributing the one-demo behavior to the intended memory binding.

LIMITATIONS:

- Dev is exposed development data.
- This result spans the sealed 14k parent source and the 14n continuation
  source. It is provenance-validated but not a single-source clean rerun.
- The exact McNemar values are descriptive terminal analyses; they do not add
  a new post-hoc gate or alter the frozen scientific method.

## Repair ledger

- 14m source `a8cd3b6e5457b858e0e4705913b2283dfaf99a0f` failed at C00 before
  science because literal `continuation_14l_v1` dispatch fell into full-run
  compatibility setup.
- Source `98f917d...` replaced both reachable literal-version checks with one
  fail-closed semantic continuation classifier. Local full tests passed
  `1008` with `3` skips; Lambda full/CUDA tests passed `1011`.
- A real scheduler/subprocess/stage-runner C00/C01 diagnostic passed before
  formal launch with zero backward and optimizer actions.
- The fresh 14n root then produced O08-F03 without another failure. Scientific
  configuration changes from the approved continuation method were zero.

## Evidence

- Formal final record SHA256:
  `531ba157c1f42d64c40161124d70121e9ea18e35922d01c26b2b86e8b8d89054`.
- Paired analysis SHA256:
  `fb8a6d79b48bb80fc1719efeb30e9a753a7bcbab4e04e07469d8f3d3ed0cbc92`.
- Formal audit index SHA256:
  `52cd18fb150aa2390104056e29362c37419a1fb6ed273d2a5161286b7ef8babb`.
- Orchestrator result SHA256:
  `81b2946c4bc8325c45142058f4c3a05ccd2a9ef01e42f72104467b0d6e477dc0`.
- Git-safe copies and a compact terminal summary are under
  `research/results/exp037a_r18_continuation_self_healing/formal_terminal/`.
- Raw trajectories, tensors, checkpoints, and unredacted evidence remain only
  under the immutable Lambda root.

No new experiment was launched after F03.
