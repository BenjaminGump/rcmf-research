# EXP-037A 14k O08 Terminal Failure

## Decision

`INFRASTRUCTURE_IMPLEMENTATION_FAILURE`

Root-cause classification:

`VERIFIED_STALE_THREE_DEMO_SCOREABLE_COUNT_CONTRACT`

The frozen 14k run is terminal. It completed the fresh three-demo positive
control and entered the conditional one-demo arm, but the one-demo arm stopped
at `O08_zero_cache_and_training_units` before zero-cache construction or any
writer/reader optimization.

Scientific status:

- three-demo positive control: `THREE_DEMO_REPRODUCTION_PASS`;
- complete one-demo arm: `NOT_EVALUATED`;
- final 3D-vs-1D comparison: `NOT_EVALUATED`;
- final reporting stage F03: `NOT_REACHED`.

## Frozen identity

- Launch source: `004f866647cfabb38a141b88e6d83821df88c403`.
- Run UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.
- Root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001`.
- Pipeline config SHA256:
  `f075eead4bd77e92546a876c24979e1882a2bfded5853624aa665ce93c84af69`.
- Contract SHA256:
  `eea5fb745ecd5041ed07e65be55d6a4a3b774caa67239e0d040100bcd9a8cce6`.
- R12B artifact-index SHA256:
  `d94770dc7e8114b63973d74cd14a40f721d92c2c25a5011a1ccbd92b947af448`.

The Lambda execution checkout remained clean at the frozen launch source. The
formal root was inspected read-only and was not resumed, retried, or modified.

## Terminal state

- Failed stage: `O08_zero_cache_and_training_units`.
- Failed attempt:
  `O08_zero_cache_and_training_units-1788717793178938884-r1`.
- Failed subcommand:
  `scripts/prepare_rcmf_joint_full_bank_9a.py`.
- Child exit code: `65`.
- Scheduler classification: `fatal`, `recoverable=false`.
- Failure time: `2026-09-06T18:03:21.266688Z`.
- Latest valid completed stage: `O07_policy_teacher`.
- Attempts: 45 total, 44 complete, 0 open, 1 failed/interrupted.
- Parent PID `790588`: dead.
- Formal tmux `exp037a_14k_formal`: absent.
- H100: idle, 0% utilization, 0 MiB / 81,559 MiB.
- NFS: mounted, approximately 3.0 PiB free.
- Monitor terminal state:
  `TERMINAL_INFRASTRUCTURE_FAILURE`.

D06B, D08B, and D22 all passed. The scheduler correctly launched the one-demo
arm after D22, and O00 through O07 completed before O08 failed.

## Exact traceback

```text
Traceback (most recent call last):
  File ".../scripts/prepare_rcmf_joint_full_bank_9a.py", line 750, in <module>
    main()
  File ".../scripts/prepare_rcmf_joint_full_bank_9a.py", line 611, in main
    raise RuntimeError(f"Prepared data counts differ: {count_checks}")
RuntimeError: Prepared data counts differ: {'train_tasks': True,
'heldout_tasks': True, 'train_memories': True, 'heldout_memories': True,
'scoreable_train': False, 'scoreable_heldout': False}
```

The production stage wrapper records the exception through
`scripts/run_rcmf_reproducible_stage_14b.py:154`,
`rcmf/benchmarks/appworld/reproducible_stages_14b.py:2174`, and
`_joint_prepare():1050`.

## Root cause

The failure is a producer/consumer contract mismatch at the arm boundary.

1. Fresh one-demo O06 completed and sealed its prompt-dependent paired causal
   panel under `full_demo_first_only`; O07 then completed from that fresh O06
   output.
2. `_joint_prepare()` correctly applies the exact D06 reproduction prerequisite
   only to the 3D arm.
3. It nevertheless invokes the same legacy
   `prepare_rcmf_joint_full_bank_9a.py` for both arms.
4. The resolved 1D configuration inherits the historical EXP-031A
   `stage_c_9a.expected.scoreable_train_state_count=366` and
   `scoreable_heldout_state_count=98`.
5. The preparation script unconditionally compares the fresh arm's paired
   train/heldout row counts against those historical 3D values.
6. The fixed task and memory invariants all passed, while both scoreable-count
   checks failed. The script raised before O08 preflight, smoke, or zero-cache.

Therefore 366/98 was accidentally treated as a shared downstream structural
invariant. It is actually the exact 3D positive-control outcome contract. A
fresh 1D panel is prompt-dependent and its completed train/heldout counts must
come from its own sealed O06 output.

The prior isolated R12B diagnostic, generated under the same repaired one-demo
prompt contract, produced 407 paired rows split 324 train / 83 heldout. That is
supporting evidence for the mismatch, but this report does not substitute those
diagnostic counts for the formal O06 rows. The Lambda traceback independently
proves that both formal scoreable-count comparisons differed from 366/98.

## O08 partial outputs

O08 stopped during the prepare substep. It created these partial files under
`/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14k_20260905_001/arms/1d/data`:

- `memory_provenance.jsonl` (1,520,488 bytes);
- `rcmf_source_cache.pt` (69,828,848 bytes);
- `key_payload_shuffle_manifest.json` (272,329 bytes).

It did not create a valid O08 `output_manifest.json` or `validator.json`.
No `full_bank_data_manifest.json`, zero cache, training-unit manifest, or
writer/reader checkpoint was sealed. All partial O08 files are diagnostic-only.

O08 backward count: `0`.

O08 optimizer-step count: `0`.

No model, checkpoint, config, selector, writer, or reader parameter was
modified.

## Preserved valid evidence

VERIFIED:

- D06B exact 3D reproduction PASS.
- D08B isolated writer/reader smoke PASS.
- D09 and D10 complete.
- D22 `THREE_DEMO_REPRODUCTION_PASS`.
- Fresh 3D dev bare/correct/shuffle: `12/17/11` of 57.
- Corrected one-demo O06 and O07 completed with strict stage validation before
  the O08 consumer assertion.
- O08 failure is pre-optimization infrastructure logic, not a model outcome.
- The formal root remains preserved and the run is terminal.

INFERENCE:

- Formal one-demo O06 is expected to reproduce the R12B isolated 407-row,
  324/83 split because source/config/seed and prompt contract are deterministic.
  Exact formal counts were not copied into this Git-safe record and must be
  read from the sealed formal O06 artifact for any later repair validation.

UNVERIFIED / NOT EVALUATED:

- Complete one-demo writer/reader training.
- One-demo heldout selection, fields, dev evaluation, and final cross-arm result.
- F00-F03 final report path.

## Safe next action

Do not resume or retry 14k under the frozen source or authorization. Correcting
this defect changes executable count-ownership logic and therefore requires:

1. a reviewed new source;
2. an explicit arm-resolved count policy;
3. 3D retaining exact 366/98 enforcement;
4. 1D deriving counts only from its own strict, hash-valid O06 output while
   preserving 29/8 task membership and 401/98 memory invariants;
5. O06/O07 state-ID equality and panel-completion validation;
6. bounded O08/O09 integration diagnostics;
7. a fresh run UUID/root and new authorization.

No repair was implemented in this publication task.


## Publication

This Git-safe failure record is published on branch
`research/v6-rcmf-exp037a-14k-o08-failure-records`. Production source and
configuration remain byte-identical to frozen launch source
`004f866647cfabb38a141b88e6d83821df88c403`.
