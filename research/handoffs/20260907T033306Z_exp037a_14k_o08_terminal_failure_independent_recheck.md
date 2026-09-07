# EXP-037A 14k O08 Independent Terminal Recheck

## Status

- Decision: `INFRASTRUCTURE_IMPLEMENTATION_FAILURE`.
- Root cause: `VERIFIED_STALE_THREE_DEMO_SCOREABLE_COUNT_CONTRACT`.
- Three-demo: `THREE_DEMO_REPRODUCTION_PASS`.
- Complete one-demo and cross-arm result: `NOT_EVALUATED`.
- Launch source: `004f866647cfabb38a141b88e6d83821df88c403`.
- Records base: `31ffa6ad745f26abbb099dfed1b32fcc08575bd7`.
- Raw root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.

## Independent Recheck

The prior interrupted publication was not accepted as evidence. Lambda files,
frozen source, process state, stage ledgers, and hashes were read again.

Formal O06 passed strict validation and sealed 407 paired states / 814 fresh
conditions: 324 model-train, 83 heldout-train-validation, labels 120 POSITIVE,
247 NEUTRAL, and 40 HARMFUL. It records 11 static over-context rows, 10 replay
missing rows, and no reused conditions. `paired_outcomes.json` SHA256 is
`36b101f00da81f652bc621f9c60568402a961a5b042940eb7ae11012f1988a04`.

Formal O07 passed strict validation and consumed exactly 407 states. Its fresh
teacher cache SHA256 is
`05dd1d1ea94044a0a165eeb0749a76d55ed48a62423a14d55ae615b706210ed4`.

## Failure

O08 failed in `scripts/prepare_rcmf_joint_full_bank_9a.py:611`. The resolved 1D
config retained the historical 3D expected scoreable counts 366/98, and the
shared preparation script unconditionally checked the fresh 1D 324/83 counts
against them. Task counts 29/8 and memory counts 401/98 passed; only both
scoreable-count checks failed. Exit code was 65, the attempt was fatal and
nonrecoverable, and strict validation reported `missing_output_manifest`.

The failure occurred in the first O08 prepare substep. O08 created only three
partial diagnostic files under `arms/1d/data`: memory provenance, source cache,
and shuffle manifest. It did not seal a data manifest, zero cache, training
units, writer/reader checkpoint, output manifest, or validator. O08 backward
and optimizer-step counts are both zero.

There were 45 logical attempts: 44 complete, 0 open, and 1 failed. The latest
valid stage is O07. The formal PID and tmux are gone, the scheduler lock is
absent, and the H100 is idle. The sealed 14k root was not changed.

## Safe Next Action

Do not retry or resume 14k. A later, separately approved repair should retain
exact 366/98 enforcement for the 3D reproduction gate, while making 1D training
preparation validate its own sealed O06/O07 state population plus fixed 29/8
tasks, 401/98 memories, panel completion, and exact state-ID equality. This
requires a new reviewed source, package, root, and user authorization.

No source/config repair, test, retry, resume, training, or new experiment was
performed in this independent recheck.
