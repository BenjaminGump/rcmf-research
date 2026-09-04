# EXP-037A-R9 Checkpoint Resume Repair

Date: 2026-09-04

Decision: `READY_FOR_14I_REAUTHORIZATION`

Scientific result: `NOT_EVALUATED`

## Scope

R9 repaired and validated the deterministic checkpoint RNG-device restoration
failure that stopped the frozen 14h run at `D10_writer_reader_epoch_2`. It did
not resume 14h, migrate its checkpoint into a new scientific run, or launch a
long run. The failed 14h root remains immutable.

## Verified 14h Failure

The 14h epoch-1 checkpoint saved CPU and CUDA RNG states with
`torch.get_rng_state()` and `torch.cuda.get_rng_state_all()`. D10 loaded the
checkpoint with `torch.load(..., map_location=backend.device)`, where
`backend.device` was CUDA. That mapped the CPU RNG ByteTensor to CUDA. The old
restore path passed it directly to `torch.set_rng_state`, which accepts only a
CPU ByteTensor.

The sealed stderr traceback is:

```text
scripts/run_rcmf_joint_full_bank_9a.py:1086  _train
scripts/run_rcmf_joint_full_bank_9a.py:1006  _restore_checkpoint
torch.set_rng_state(payload["torch_rng_state"])
TypeError: RNG state must be a torch.ByteTensor
```

Classification: `DETERMINISTIC_CHECKPOINT_RESUME_DEVICE_MAPPING_BUG`.

The D10 attempt `D10_writer_reader_epoch_2-1788501645911565815-r1` performed
zero backward passes and zero optimizer steps. It produced no epoch-2
checkpoint and no valid D10 scientific output. D06B, D08B, and D09 had passed;
D22 was not reached and 1D did not start.

The D09 checkpoint is sealed and hash-valid:

```text
path: arms/3d/joint_training/checkpoints/epoch_01.pt
sha256: 60c40ca73ecdc7f8fea15ec50e87bca28ad8efcd4c33d98b91ea282273c2bd40
size: 321891075 bytes
completed units: 576
```

It remains evidence and a diagnostic anchor only. It is not a 14i scientific
input.

## Minimal Repair

The production checkpoint identity, module restoration, optimizer restoration,
training schedule, data order, losses, mixed precision, and seeds are
unchanged. The repair only canonicalizes RNG state before calling the PyTorch
RNG APIs:

- CPU RNG: require a nonempty one-dimensional `torch.uint8` tensor, detach,
  transfer to CPU, and make contiguous.
- CUDA RNG: apply the same CPU ByteTensor canonicalization to every saved
  device state and require the saved state count to match available devices.
- Invalid type, dtype, shape, or CUDA device count fails closed.

The repaired code is in `scripts/run_rcmf_joint_full_bank_9a.py`, beginning at
`_canonical_rng_byte_tensor` and `_restore_rng_states`. Optimizer behavior was
not altered.

## Validation

### Synthetic cross-process equivalence

A CUDA diagnostic ran three independent processes: uninterrupted `N+1`, a
prefix of `N` plus checkpoint save, and a fresh resume process for the final
unit. All preregistered comparisons were exact: completed unit IDs and loss
history; writer, reader, and optimizer hashes; CPU and CUDA RNG hashes; next
Python, CPU, and CUDA random values; and backward/optimizer-step counts.

Both paths ended with writer hash `77241bfd...`, reader hash `e7af2cf...`,
optimizer hash `3297efc...`, CPU RNG hash `23cd072d...`, and CUDA RNG hash
`204156fa...`. Exact losses were `0.1606936157`, `0.2737042904`, and
`0.09246108681`.

### Sealed D09 one-unit diagnostic

The actual D09 checkpoint SHA was independently recomputed before use. A copy
and its read-only training inputs were placed under an isolated diagnostics
root. The repaired production resume path restored 576 completed units, ran
exactly one additional unit, and ended at 577: one backward; one optimizer
step; finite loss `0.03523876518`; finite nonzero writer/reader gradients;
Qwen frozen; selector tensors frozen; optimizer restored; CPU/CUDA RNG restore
passed.

The source snapshot was checked after the smoke: all 481 files retained their
hash, size, and modification time. The diagnostic update is not eligible as a
scientific checkpoint.

### Production-path and tests

The real S00-S04 scheduler/subprocess/producer/strict-validator path passed
5/5 in 14.797 seconds. A second invocation skipped all 5 hash-valid stages and
launched zero subprocesses. It stopped before S05 and used zero scientific
H100 time.

Test results with process-start `PYTHONHASHSEED=25101`:

| Environment | Suite | Result |
|---|---|---|
| Local `appworld_env` | focused R7/R9 | 30 passed, 1 skipped in 29.91s |
| Local `appworld_env` | full | 915 passed, 3 skipped in 40.97s |
| Lambda `rcmf-py311` | focused R7/R9 | 31 passed in 14.07s |
| Lambda `rcmf-py311` | full | 918 passed in 20.17s |

Focused coverage includes CPU/CUDA RNG canonicalization, invalid-state
fail-closed checks, writer/reader and optimizer identity, strict checkpoint
identity mutation rejection, cross-process equality, stale 14h authorization
rejection, 14i invariants, and unchanged D06B/D08B/D22 ordering.

Exact commands are recorded in
`research/results/exp037a_r9_checkpoint_resume_repair/tests.json`. One local
invocation using the default pytest temp root hit the known Windows ACL error
before two `tmp_path` tests ran; the unchanged suites above passed with fresh
writable `C:/gbz` basetemp paths.

## Fresh 14i Package

Final launch source and archive:

```text
fa069cd3619ddbd9d4ebe0fd82038ca25b60c75d
archive/exp037a-r9-launch-source-fa069cd
```

Fresh unlaunched package:

```text
UUID: rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_002
root: /lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_002
config SHA256: 88e7eebbb47f680bf330ad5667f98ce8983aadc3894a48f51d79a5b1fefeb48d
contract SHA256: 6d580bc616136c2b696efc1a37c6aa14fb9474decead0adcdfeaf1d1e3e72d83
artifact-index SHA256: 863227c89d1d63c665ca142307c9a32468a2b9b8d433135c4868668aecb923cd
```

Its root contains preflight only: no runtime authorization, scientific stage,
formal attempt, or migrated checkpoint. Authorization remains false. The 14h,
R6, and old 200-hour authorizations are not inherited.

Scientific invariants are unchanged: seed 25101; selector parent split seed
18018 and 29/8 parents; CV seed 25071; final seeds 25071/25072/25073; panel
256/499/40; expected D06 366/98 with 129/300/35 labels; 3D `full_demo`; 1D
`full_demo_first_only`; unchanged D06B, D08, D08B, writer/reader recipe, D22,
and conditional 1D behavior.

## Runtime And Migration

Measured 14h start through D09 was 5.840 hours. D06 took 1.851 hours and D09
took 1.029 hours.

| Branch | Expected wall | Conservative wall | Expected H100-active |
|---|---:|---:|---:|
| 3D reproduction fails | 26.75 h | 56.5 h | 21.85 h |
| 3D passes and full 1D runs | 47.75 h | 92.5 h | 39.05 h |

Expected/conservative storage is 46/90 GiB. The proposed 120-hour hard cap is
an anomaly ceiling and remains unapproved.

Decision: `FRESH_RERUN_PREFERRED`. Reusing D09 could save approximately 5.84
wall hours, but production migration would require a reviewed cross-source
checkpoint provenance contract, scheduler/gate support, and new completion
identity semantics. That complexity and risk exceed the saved time; none was
implemented.

## Deviations

- The first D09 diagnostic root omitted `runtime/static_counts.json` and
  stopped before model loading, backward, or optimizer work. It was preserved;
  a fresh `_002` diagnostic passed.
- A first 14i `_001` preflight was generated from a superseded launch-source
  candidate. It remains immutable and unlaunched. The final source uses the
  fresh `_002` package and the correct R9 decision.
- These iterations changed no scientific semantics and launched no long run.

## Final Decision

`READY_FOR_14I_REAUTHORIZATION`

The 14i package requires a new explicit authorization bound to its exact
source, UUID, root, config, contract, scope, and cap before launch.
