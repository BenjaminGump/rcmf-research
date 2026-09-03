# EXP-037A-R7 Stage-Manifest Identity Repair

## Decision

- Decision: `READY_FOR_REAUTHORIZATION`
- Scientific result: `NOT_EVALUATED`
- New launch source:
  `272b0e9a1f8b29f898024f1115d1710d41270758`
- Archive branch: `archive/exp037a-r7-launch-source-272b0e9`
- New run UUID:
  `rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001`
- New canonical root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14h_20260904_001`
- Authorization: `NOT_AUTHORIZED`

## Exact Defect And Repair

The R6 stage runner received and checked the scheduler's run UUID, root, and
config SHA, while the failure path also recorded the contract SHA. The
successful `write_stage_manifest()` path nevertheless emitted only the source
commit and stage metadata. Strict `validate_stage_completion()` correctly
required the missing UUID, canonical root, config SHA, and contract SHA and
rejected S00 after its executable exited zero.

R7 added one canonical verified identity payload containing:

- `source_commit`
- `run_uuid`
- `run_root`
- `pipeline_config_sha256`
- `contract_sha256`
- `stage_id`
- `attempt_id`

The formal runner now resolves and compares scheduler/config/runtime values
before stage execution and passes that payload explicitly to the successful
manifest producer. Its post-identity failure path consumes the same payload.
Incomplete strict identity fails closed. The strict validator was not
weakened.

The full resume audit found one related gap: dependency completion identities
were recorded but not hash-validated. Strict completion validation now verifies
those hashes, so an upstream replacement invalidates affected downstream
completion evidence.

R5 missed the producer defect because
`tests/test_exp037a_r5_final_launch_preflight.py::_write_output()` manually
synthesized a valid strict manifest instead of calling the production writer.
R7 positive coverage calls the real writer followed by the real validator.

## Producer Audit

`build_exp037a_stage_graph()` contains 60 formal stages: 11 shared, 25 3D, 20
conditional 1D, and 4 final stages. Every formal command uses
`scripts/run_rcmf_reproducible_stage_14b.py`, and every successful formal stage
therefore uses the repaired
`rcmf.benchmarks.appworld.reproducible_stages_14b.write_stage_manifest`.

Repository output-manifest writers outside that path are bounded mock or test
helpers and are not reachable from the formal stage graph. The machine-readable
60-row audit is in `stage_manifest_producer_audit.json`.

## Real Production-Path Validation

Diagnostic root:
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/rcmf_exp037a_r7_stage_manifest_smoke_14h_20260904_002`

This used the actual `EventDrivenScheduler`, `subprocess_stage_runner`, formal
stage script, production manifest writer, scheduler environment variables, and
strict validator. The contract ended at S04 and contained no scientific stage.

| Stage | Exit | Manifest SHA256 | Strict | Completion |
|---|---:|---|---|---|
| S00_environment_manifest | 0 | `a264a6dfa0dda8b9162c0d70729005d0e879788f65fab5d484c24108e825e825` | PASS | PASS |
| S01_authoritative_corpus | 0 | `f0fda34b22b434039fe69fe3b637013cc90ce7868518a9d403bceec003a1e89c` | PASS | PASS |
| S02_task_and_parent_splits | 0 | `b2f7b78287673a09971003921640e1c377cfc213219dcab4a374efef3e16552e` | PASS | PASS |
| S03_transition_records | 0 | `847f7232dc4a929f3dbdfa38e04c48d435eab0ba52fb852c12c2ba75d58783ab` | PASS | PASS |
| S04_selector_supervision | 0 | `c69482554d91ab42916e00d94962859e34a339f837829632f64e1c219d4285b9` | PASS | PASS |

First-run elapsed time was 14.529715 seconds. A second scheduler invocation
validated and skipped all five stages and executed zero subprocesses. A copied
manifest with a changed run UUID failed strict validation. Scheduler regression
coverage separately proves that such a rejected manifest is rerun.

The preserved `_001` diagnostic attempt failed before science because its
diagnostic fixture omitted `transition_signatures.jsonl` and
`signature_equivalence.json`. It also exposed that runtime-layout failures were
outside the canonical failure guard. Both diagnostic/infrastructure issues were
fixed before the successful `_002` run. No failed formal 14g artifact was
modified or reused as completion evidence.

## New 14h Package

- Pipeline config:
  `configs/pipeline/rcmf_appworld_repro_14h.yaml`
- Config SHA256:
  `53b87b0624fe08824b4d976554e745c95753ab59ecbf338a1c060a069259cfd9`
- Stage DAG/contract SHA256:
  `dce77ba06e39b8e38449f10f514e3a0972d0e15ef04e9a5e762720e8d1f0fc29`
- Preflight artifact-index SHA256:
  `0cf9266928330a7a2bec732441b470e8dbbac3c575ebcd7037f3ebcd96b8f9e8`
- Preflight-summary file SHA256:
  `9afcaaa3ecbde3a4652207141281764f8450989f0db0ed57b3bb115c87c56d4d`
- Production-path smoke-summary SHA256:
  `d73737d10f636532e792acb3568db239ef4951190a65f7308c0577cacd970f07`

The root was absent before preflight. It now contains only `preflight/`; there
is no runtime authorization, stage directory, formal attempt ledger, or
scientific output. The authorization request remains false in every scope
field. Neither the failed R6 120-hour authorization nor the historical
200-hour authorization is inherited.

## Scientific Invariance

Scientific configuration changes are zero. The package preserves:

- selector parent split algorithm and seed 18018 with 29/8 parents;
- 310,433 legal selector cells;
- selector CV seed 25071 and final seeds 25071/25072/25073;
- causal panel 256 initial, 499 maximum, 40 minimum per label;
- `full_demo` for 3D and `full_demo_first_only` for 1D;
- post-D06 outcome gate 366 train, 98 heldout, and label counts 129/300/35;
- Qwen, tokenizer, corpus, AppWorld, selector recipe, writer/reader, losses,
  two epochs, checkpoint selection, D06B, D08/S05B, D08B, D22, evaluation, and
  conditional 1D semantics.

Historical trained artifacts remain comparison-only after corresponding fresh
artifacts are sealed.

## Runtime And Authorization

The unchanged estimates remain:

| Permitted branch | Expected wall | Conservative wall | Expected H100 active |
|---|---:|---:|---:|
| 3D reproduction fails | 26.75 h | 56.5 h | 21.85 h |
| 3D passes and full 1D executes | 47.75 h | 92.5 h | 39.05 h |

Expected/conservative storage remains 46/90 GiB. The proposed 120-hour global
cap is derived by rounding upward from
`max(2 * 47.75, 1.25 * 92.5) = 115.625` hours. It is not authorized.

## Tests

- Local focused R3-R7 and core pipeline suite: 99 passed.
- Final local focused producer/scheduler/preflight suite: 60 passed.
- Final local full suite: 900 passed, 2 skipped in 18.72 seconds.
- Lambda focused suite: 60 passed; post-smoke R7 suite: 15 passed.
- Final Lambda full suite: 902 passed in 9.39 seconds.
- All process starts used `PYTHONHASHSEED=25101`.
- All 60 formal stage IDs passed production-writer/strict-validator synthetic
  round-trip coverage.
- The real S00-S04 production path passed 5/5 and resume skipped 5/5.

## Runtime State

At final preflight the H100 reported 0 MiB used and 0% utilization. No active
EXP-037A process or formal tmux exists. Historical idle tmux sessions remain.
The Lambda instance is safe to leave idle or terminate after records are
synchronized; this task does not terminate it.

## Verified, Inference, Unverified

VERIFIED: all identities, tests, stage results, hashes, authorization state,
scientific invariants, and runtime state above are direct artifacts or command
outputs.

INFERENCE: none is needed for the executable decision.

UNVERIFIED: behavior of a future full 14h scientific run, which has not been
authorized or launched.
