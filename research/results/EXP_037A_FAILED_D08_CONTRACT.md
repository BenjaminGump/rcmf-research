# EXP-037A `_001` D08 Contract Failure

## Status

| Field | Value |
|---|---|
| Run UUID | `rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001` |
| Scientific source | `02ef94726ea0fe566f7eea4fa137fb91da92977f` |
| Decision | `INFRASTRUCTURE_IMPLEMENTATION_FAILURE` |
| Scientific result | `NOT_EVALUATED` |
| Failed stage | `D08_zero_cache_and_training_units` |
| Three-demo reproduction gate | `NOT_REACHED` |
| One-demo arm | `NOT_LAUNCHED` |

This publication records a fatal implementation/data-contract failure. It does
not repair the producer or consumer, fill missing fields, run a new diagnostic,
resume the parent, create a run, or change any scientific result.

## Immutable Raw Evidence

The raw run root remained read-only during this publication:

`/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`

The Git-safe content-addressed index is
`research/results/exp037a_reproducible_pipeline_failure/artifact_index.json`
(SHA256 `370b46e3aca0f99ade20e334a208de6ff94e83c74e164b5c80716aa297c546af`).
It indexes 45 required state, ledger, log, completion, validator, manifest, and
declared-output files without copying raw logs or tensors into Git.

## Git And Runtime Identity

At collection time:

- Local branch was `research/v6-rcmf-reproducible-3d-gated-pipeline` at
  `7cc63c0a430153b8e6be2a81536fbd1bbaa1d365`, clean and `0/0` against GitHub.
- GitHub branch HEAD was independently queried as
  `7cc63c0a430153b8e6be2a81536fbd1bbaa1d365`.
- Lambda used the same branch name but intentionally remained at frozen source
  `02ef94726ea0fe566f7eea4fa137fb91da92977f`, clean and `0/2` relative to the
  records HEAD. Lambda was not switched to the records commit.
- `/lambda/nfs/rcmf-persist` was mounted. The H100 reported `0%`, `0 MiB`, and
  no scientific compute process.
- Parent tmux `exp037a_repro_14b`, supervisor, orchestrator PID `692491`, and
  stage workers had exited. This was not a CPU validator interval.

## Completed Stages

The scheduler completed `S00-S09` and `D00-D07` before D08. This publication
independently rehashed D00-D07: all `8/8` completion records passed, all
validators passed, all manifests name source `02ef947`, and every declared
output exists with the recorded size and SHA256.

| Stage | Attempt | Output-manifest SHA256 | Declared output SHA256 |
|---|---|---|---|
| D00 state representations | `D00_state_representations-1788381098035148` | `e78e67124c8523a75c4f35aa65aea01e7957c8611ecf8d5aa9ecab2a9b56d842` | `f585a0033d936aa00813a75678f35f9af80fcc1b4471880a2d682bfc4bb058d2` |
| D01 selector candidate CV | `D01_selector_candidate_cv-1788381723050415` | `bce410379c776ee65379222c35b20aef72df34ae9fb41e10b523857ec117eefc` | `8ea3c435d13c3a3b75d03aaf4df1eea38a6ca1415e1e7b6b323bdb3195ecc7c5` |
| D02 candidate selection | `D02_selector_candidate_selection-1788385332645244` | `1a9682b7fa1339b63460f0d88a2f13ce18a144fedeb82be32db9e10c87d4b543` | `a04bc243b0a4ceb5fc91ea36cae35bba3207008608c7333ff9db0568308c7274` |
| D03 final selector ensemble | `D03_final_selector_ensemble-1788385335460237` | `9d6c27da8894e58e9308735cf50f530223ca8bbbd8992665a822f41b177028ee` | `eab8668a62db097f8223c047dba7fecd0a8e5b9b2d32f6a847ad9b66cc98e860` |
| D04 selector factorization | `D04_selector_factorization-1788386857227204` | `3f857cee829501d8e5fe95ad6bb80098a7fc8542055b0057b6272abcdbb6bcf1` | `42f332530f60c17942b5ba595c93628b5de592146f916eec5d2fc89abfd49127` |
| D05 selected-memory manifest | `D05_selected_memory_manifest-1788386861295833` | `c7636628f05f06eca51c4c818458216ac72a7b7883ea64aac8d382db457d3016` | `6a349d4fcc5be1b5e666429f6e9e8a44b241a0f156ec4d2865a9a6660232f9b8` |
| D06 paired causal outcomes | `D06_paired_causal_outcomes-1788386910599058` | `b32a877c62c10342d8905d86e31188abfcbebd700cb91e290450221ce5142c49` | `0e1f8fac82b951309ec39891a9ba939e4f016c84ad0de26fe132c4a0d32d751c` |
| D07 policy teacher | `D07_policy_teacher-1788393640653363` | `69b947f5c29834d80b06b50ca3727cb94b96390b4177051881cd12eb08d66347` | `83a164afbe8f08f1841e7caf8c3a037b701ee4f4be554e07f318322850a463da` |

D00 additionally contains the valid progress file
`representation_progress.json`; it is recorded as an extra diagnostic file,
not as a partial declared output. The full machine table is in
`stage_status.json`. These stages may be read as hash-validated diagnostic
fixtures, but they are **not automatically authorized as scientific input to
any new run**.

## D08 Exact Failure

The outer stage ran for about `5.54s` and closed with exit `65`,
`recoverable=false`. Its preparation child closed with exit `1` and stop reason:

```text
KeyError: 'source_task_goal_tokens'
```

The exact failing expression is `int(transition[token_name])` in
`scripts/prepare_rcmf_joint_full_bank_9a.py::_section_contract`, line 204.
The first triggering transition is
`001629b3-5715-5e40-b42a-b7d00dc82b7d`.

The exact input manifest is:

`/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001/shared/compat_exp025b/clean_cache_rebuild/transition_preflight/transition_manifest.jsonl`

SHA256:
`f1484d419cb475efcf11231d0fa0548ee402322eba7c96db4301679255f829b3`.

The consumer requires:

- `source_task_goal_tokens`
- `canonical_pre_action_state_tokens`
- `complete_action_tokens`
- `complete_post_action_observation_tokens`

All four are absent from the triggering row. The row contains
`teacher_section_tokens`, but that aggregate is not one of the four required
per-section counts.

Producer-side provenance is:

- `scripts/prepare_rcmf_reproducible_pipeline_14b.py::_add_teacher_token_metadata`
  adds only `teacher_section_tokens`, `teacher_section_sha256`, and tokenizer
  identity to the authoritative transition rows.
- `rcmf/benchmarks/appworld/reproducible_stages_14b.py::_compatibility_inputs`
  copies that transition file unchanged into the EXP-025B compatibility path.

Consumer-side provenance is
`scripts/prepare_rcmf_joint_full_bank_9a.py::_section_contract`, which requires
all four per-section token counts while constructing source provenance.

The full traceback, actual row-field list, required/missing sets, code paths,
and D08 file window are preserved in `d08_contract_diff.json`.

## Side-Effect Audit

VERIFIED:

- No D08 `output_manifest.json` exists.
- The only files modified under `arms/3d` during D08 were `heartbeat.json` and
  append-only `attempts.jsonl`.
- The stage framework wrote `process.json`, empty stdout, stderr, failure,
  failed completion, root attempt/scheduler/orchestrator/supervisor records,
  and logs.
- No `.pt`, `.pth`, `.safetensors`, `.ckpt`, YAML, or resolved config changed.
- No smoke, zero-cache, or training subprocess was launched after prepare.
- Writer/reader optimizer updates consumed: `0`.
- Partial scientific output: none.

## Attempts And Watchdog

- Root ledger: `19` attempts, `38` rows, no open attempt.
- 3D arm ledger: `7` attempts, `14` rows, no open attempt.
- Supervisor ledger: one closed attempt, no open attempt.
- The final watchdog snapshot at `2026-09-03T02:13:39Z` recorded scheduler
  `failed`, stale heartbeat, H100 `0%/0 MiB`, bash PID `692487`, monitor PID
  `692489`, and watchdog-log SHA256
  `b375f308d6c0467b4cdeccfe103ea56bbc8b262a73d2473367c20e148f9b4e83`.
- The authorized orphaned watchdog tmux was then stopped. At
  `2026-09-03T02:14:09Z`, both PIDs and both EXP-037A tmux sessions were absent.

## Interpretation

VERIFIED: the run stopped because a producer/consumer schema contract was not
satisfied. It did not reach writer/reader training, the three-demo reproduction
gate, or the one-demo arm.

INFERENCE: replaying the identical frozen source and artifacts would encounter
the same missing-key failure.

UNVERIFIED: whether the reviewed repair should extend the fresh producer,
change the compatibility adapter, or make the consumer derive the counts. No
repair choice is made here.

## Deviations

There were no implementation or scientific deviations in this publication.
No source/config/model/checkpoint/run artifact was modified, no new diagnostic
was run, and no run UUID was created. Lambda remains at `02ef947`; it was not
terminated.
