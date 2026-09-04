# EXP-037A-R9 Structured Handoff

## Decision

`READY_FOR_14I_REAUTHORIZATION`

Scientific result: `NOT_EVALUATED`.

## Git Identity

- Starting records SHA: `6a3b8740a9611564b66f898035299625ace93922`
- Repair branch: `research/v6-rcmf-exp037a-checkpoint-resume-repair`
- Launch source: `fa069cd3619ddbd9d4ebe0fd82038ca25b60c75d`
- Launch archive: `archive/exp037a-r9-launch-source-fa069cd`
- Records commit: the commit containing this handoff
- Formal execution must use the launch source, not the records commit.
- The launch-source-to-records diff is restricted to `research/`.

## Failure And Repair

- Failed run: `rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001`
- Failed stage: `D10_writer_reader_epoch_2`
- Failed attempt: `D10_writer_reader_epoch_2-1788501645911565815-r1`
- Root cause: `DETERMINISTIC_CHECKPOINT_RESUME_DEVICE_MAPPING_BUG`
- D10 backward/optimizer count: `0/0`
- D09 checkpoint SHA256:
  `60c40ca73ecdc7f8fea15ec50e87bca28ad8efcd4c33d98b91ea282273c2bd40`
- Repair: validate RNG tensors and canonicalize CPU/CUDA generator states to
  contiguous CPU `torch.uint8` before restoration. Module and optimizer
  restoration remain unchanged.

## Bounded Validation

- Synthetic uninterrupted vs fresh-process resume: exact PASS on unit IDs,
  losses, module/optimizer hashes, CPU/CUDA RNG hashes, next random values,
  and training counts.
- Actual sealed D09 one-unit resume: PASS; 576 to 577 units, one backward,
  one optimizer step, finite loss/gradients, Qwen and selector frozen.
- D09 diagnostic source immutability: 481/481 files unchanged.
- Real S00-S04 path: 5/5 strict PASS; second run skipped 5/5 with zero
  subprocesses; stopped before S05.
- Local tests: focused 30 passed/1 skipped in 29.91s; full 915 passed/3
  skipped in 40.97s.
- Lambda tests: focused 31 passed in 14.07s; full 918 passed in 20.17s.
- Process-start `PYTHONHASHSEED=25101` was used.
- Exact commands are recorded in the R9 `tests.json`. A default Windows pytest
  temp-root invocation hit an ACL error during setup; unchanged tests passed
  with a fresh writable `C:/gbz` basetemp.

## Fresh 14i Package

- UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_002`
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14i_20260904_002`
- Config SHA256:
  `88e7eebbb47f680bf330ad5667f98ce8983aadc3894a48f51d79a5b1fefeb48d`
- Contract SHA256:
  `6d580bc616136c2b696efc1a37c6aa14fb9474decead0adcdfeaf1d1e3e72d83`
- Artifact-index SHA256:
  `863227c89d1d63c665ca142307c9a32468a2b9b8d433135c4868668aecb923cd`
- Authorization: `NOT_AUTHORIZED`
- Scientific stage count: zero
- Root contents: preflight only

## Scientific Invariance

Scientific configuration changes are zero. Selector split, seeds, 310,433
cells, panel 256/499/40, D06 expectations, prompts, writer/reader recipe,
D06B, D08, D08B, D22, conditional 1D, and evaluation are unchanged.

## Runtime And Migration

- 14h start through D09: 5.840 h
- D06: 1.851 h
- D09: 1.029 h
- 3D-fail branch: 26.75 h expected, 56.5 h conservative, 21.85 H100 h
- 3D-pass-to-1D branch: 47.75 h expected, 92.5 h conservative, 39.05 H100 h
- Storage: 46/90 GiB expected/conservative
- Proposed cap: 120 h, not authorized
- Migration decision: `FRESH_RERUN_PREFERRED`; migration was not implemented.

## Runtime State

- Formal 14h tmux/process: absent
- H100: idle, 0 MiB used at final inspection
- Read-only 14h watchdog and five-minute status bridge remain active
- The bridge detected the D10 terminal state within one polling interval
- A manual reader must check `last_updated_utc` freshness before treating a
  GitHub monitoring snapshot as current
- No 14i process exists; the system is safe to leave idle. Lambda termination
  is operationally safe only after deciding whether continued monitoring or
  artifact access is still needed.

## Deviations

- Diagnostic D09 `_001` omitted one required static-count file and stopped
  before model load or training. It was preserved; `_002` passed.
- A superseded 14i `_001` preflight remains preserved and unlaunched. The
  final package is `_002` at the final launch source.

## Required Next Action

Review this packet. A future launch requires a new explicit authorization
bound to the exact 14i launch source, UUID, root, config SHA, contract SHA,
scope, and proposed cap. Do not reuse any earlier authorization.
