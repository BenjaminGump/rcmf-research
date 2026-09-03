# EXP-037A-R5 Final Launch-Readiness Audit and Contract Freeze

Date: 2026-09-03

## Decision

`READY_FOR_FINAL_RUN_APPROVAL`

The final 14g launch package is internally consistent, source-bound, and
fail-closed. It is explicitly `NOT_AUTHORIZED`; no long scientific run was
started.

## Git Freeze

- Starting commit: `c898d86e77eec353ec8f2103ae285dbdbc5bb9a0`
- Final launch source: `31c9e98ab408d4768c83d2a45bb1b21ae565b4be`
- Frozen launch archive branch:
  `archive/exp037a-r5-launch-source-31c9e98`
- Working branch: `research/v6-rcmf-exp037a-final-launch-freeze`
- Records commit: the commit containing this report; formal execution must
  use the launch source above, never the later records commit.

The executable delta from the starting commit contains only launch identity,
authorization, orchestration, validation, stage gating, preflight, smoke, and
test changes. Scientific data, model, prompts, selector method, panel
semantics, writer/reader mathematics, losses, and evaluation criteria did not
change.

## Final Run Identity

- UUID:
  `rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`
- Canonical root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`
- Pipeline config: `configs/pipeline/rcmf_appworld_repro_14g.yaml`
- Pipeline config SHA256:
  `1678368f05b717c56f970f3aab9a7da13adbb9c42a45e2076bc7fc1493564b1a`
- Serialized stage-DAG/contract SHA256:
  `5c254fb2ccb4cf76c7275451c9bdfc3332962e6d196ed8691cbd3206d6e2b47f`
- Git-safe artifact-index SHA256:
  `d8de848d336aa4172d908af278c629512f39da2ddf5aed87f38cce53a5907f46`

The canonical root did not exist before the final builder ran. It now
contains `preflight/` only: no `stages/`, no runtime authorization, and no
scientific output. The failed 14b root, R3 diagnostics, and R4 14f identity
remain untouched historical evidence.

## Authorization

The frozen request records:

- `authorization_status = NOT_AUTHORIZED`
- `authorized_to_launch = false`
- `granted_by_user = false`
- `full_pipeline_authorized = false`
- `d06_or_later_authorized = false`
- `one_demo_authorized = false`
- `previous_200_hour_authorization_inherited = false`
- proposed hard cap = 120 hours

A future authorization must bind the exact UUID, canonical root,
`LAUNCH_SOURCE_SHA`, pipeline-config SHA, stage-DAG SHA, 120-hour cap,
authorization version, and full scope
`complete_fresh_3d_then_conditional_fresh_1d_and_final_reporting`.
The launcher and scheduler independently validate these bindings. Missing,
stale, partial, wrong-cap, wrong-source, wrong-root, wrong-config, and old
200-hour grants fail closed.

As a real fail-closed check, the unapproved `authorization_request.json` was
submitted to the launcher. It exited 1 before stage execution, produced no
runtime authorization, and created no `stages/` directory. The preserved
stderr SHA256 is
`607f2cf901fa448b047223b53e4ae1151e136ee8bbb3888a04dc71aacb467b97`
under the diagnostics root.

After a later explicit approval is persisted as a distinct valid grant, the
launch template is:

```bash
git switch --detach 31c9e98ab408d4768c83d2a45bb1b21ae565b4be
ROOT=/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001
PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python \
  scripts/run_rcmf_reproducible_pipeline_14b.py \
  --contract "$ROOT/preflight/stage_dag.json" \
  --run-root "$ROOT" \
  --authorize-and-run \
  --authorization-file "$ROOT/preflight/explicit_user_authorization.json"
```

`explicit_user_authorization.json` does not exist in this milestone and must
not be created until a later task receives explicit approval for this exact
packet.

## Scientific Invariance

Static and resolved-config validation confirms:

- selector parent split algorithm
  `sha256_order_first_heldout_then_remaining_train`, seed 18018, 29/8;
- 310,433 legal selector label cells;
- selector CV seed 25071 and final seeds 25071, 25072, 25073;
- causal panel 256 initial, 499 maximum, 40 minimum per label;
- 366 train and 98 heldout completions remain post-D06 outcomes only;
- 3D prompt `full_demo` and 1D prompt `full_demo_first_only`;
- prompt profile remains the only intended inter-arm scientific difference;
- global seed 25101;
- historical trained artifacts remain comparison-only after matching fresh
  artifacts are sealed.

The machine invariant record reports `scientific_changes_from_r3 = 0`.

## Stage Gates

The serialized graph has 60 stages.

- `D06B_three_demo_causal_reproduction_gate` follows sealed D06 and blocks
  D07, D08, D09, and 1D unless the fresh completed sets, per-state status,
  labels, label counts (129/300/35), over-context set, and replay-semantic set
  exactly reproduce available historical evidence, including 366/98 counts.
- D07 consumes only fresh correct D06 artifacts after D06B passes.
- D08 requires D06B pass, 366/98 fresh populations, the 499-row S05B consumer
  contract, no truncation, no token subsampling, and no imputation.
- `D08B_writer_reader_one_unit_smoke` uses a cloned initialization, one
  predeclared unit, and one discarded technical optimizer step. D09 restarts
  from untouched initialization and cannot run if the smoke fails.
- D22 validates fresh 3D artifacts and is the only gateway to 1D. Only exact
  `THREE_DEMO_REPRODUCTION_PASS` makes O00 eligible; every other decision
  leaves O00-O19 skipped and follows failure reporting.
- Every resume skip validates run/root/source/config/contract/stage/output
  identity and completion self-hash. The global deadline is propagated as
  `RCMF_PIPELINE_HARD_DEADLINE_EPOCH` to every child process group.

## Runtime Proposal

The runtime now covers the actual permitted scope rather than 3D alone.

| Branch | Expected wall | Conservative wall | Expected H100 active |
| --- | ---: | ---: | ---: |
| 3D reproduction fails, then failure reporting | 26.75 h | 56.5 h | 21.85 h |
| 3D passes, complete conditional 1D, final reporting | 47.75 h | 92.5 h | 39.05 h |

Storage is estimated at 46 GiB expected and 90 GiB conservative. No local H100
hourly rate is configured, so a dollar estimate is unavailable.

The cap calculation uses the longest branch:

```text
max(2.0 * 47.75, 1.25 * 92.5)
= max(95.5, 115.625)
= 115.625 hours
```

Rounded upward, the proposed anomaly ceiling is 120 hours. This proposal is
not authorization.

## Validation

Final source validation under process-start `PYTHONHASHSEED=25101`:

- Local focused: 96 passed.
- Local full: 885 passed, 2 skipped.
- Lambda focused: 96 passed.
- Lambda full: 887 passed.
- Ruff undefined-name audit (`--select F821`): passed; the temporary lint
  environment was removed.
- Strict Git-safe scan: 0 JWT, 0 bearer-token, and 0 credential matches. The
  older broad scanner's 28 matches were all `stage_c_*` config-path false
  positives (7 unique paths), with no `eyJ` JWT prefix.

The bounded technical smoke passed in 46.629611 seconds at
`/lambda/nfs/rcmf-persist/project/runs/diagnostics/exp037a_r5_launch_smoke_31c9e98`.
It exercised 3D/1D representations, one technical selector update, two
discarded writer/reader updates, reversible 401-memory algebra, one-step
D0/D1/D2 AppWorld plumbing, scheduler PASS/FAIL paths, and frozen-Qwen
gradients. All three AppWorld conditions completed with zero infrastructure
exceptions. Peak GPU memory was 17,903,331,840 bytes. This is engineering
evidence, not a scientific result; H100 scientific active time is zero.

## Engineering Attempts And Deviations

- Two earlier source candidates (`f7c0ec0`, `70b49bc`) were superseded before
  the final freeze after cheap smoke uncovered a D22 mock-contract mismatch
  and a globally reused AppWorld experiment prefix. The final prefix is bound
  to source commit plus diagnostic run root.
- One resumed focused command named two nonexistent legacy test paths and
  collected no tests. A later local run then encountered only a Windows pytest
  temporary-directory ACL error. The exact suite passed after using an
  ignored repository-local basetemp.
- A compatibility call to the historical 14b preflight builder generated
  diagnostic-only shared inputs and then stopped on the intentionally absent
  legacy `approved_hard_cap_hours` key. The final 14g builder completed from
  scratch and is the sole approval source.
- No deviation changed scientific data, model, prompt, method, evaluator,
  result, or formal run content.

## Artifacts And Stop State

Git-safe records are under
`research/results/exp037a_r5_final_launch_preflight/`. Large shared tables and
initialization tensors remain Lambda-only under the canonical `preflight/`
root and are content-addressed in the artifact index.

- No selector retraining ran in R5.
- No representation rebuild ran in R5.
- No full D06, D07, D08, or D09 ran in R5.
- No 1D arm ran in R5.
- No new long scientific run launched.
- H100 scientific active time is zero.

## Final Runtime Snapshot

At 2026-09-03T17:54:40Z, NFS was mounted; the H100 was at 0% utilization and
0 MiB used; no EXP-037A process or tmux session existed; and the canonical
root had neither `runtime_authorization.json` nor `stages/`. Lambda remained
clean on `archive/exp037a-r5-launch-source-31c9e98` at the exact launch source.
The records branch is separately pushed and synchronized as a ref.

`SAFE_TO_TERMINATE = YES`: no active work depends on the instance, and all
preflight/diagnostic artifacts are persisted on NFS. The instance was not
terminated.
