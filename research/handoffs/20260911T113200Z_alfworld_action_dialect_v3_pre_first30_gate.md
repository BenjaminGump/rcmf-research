# ALFWorld Action-Dialect V3 Pre-First-30 Gate

## Decision

`READY_TO_START_PREREGISTERED_FIRST_30_BARE_ONLY`

Full-134 bare and RCMF-C remain blocked. The first-30 run must pass every
structural check, exceed the fixed v2 reference of 13/30 total successes, and
produce at least 1/12 `pick_and_place` successes. No validation outcome may
change a prompt, model, checkpoint, task, generation setting, or evaluator.

## Authority and source

- Branch: `adapt/alfworld-v1`.
- Starting action-boundary source: `91598b6d734cc5a5b3d1ccc1d21a7f2fb1bb2741`.
- Action-dialect preregistration commit: `42698dd`.
- Executable source: `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094`.
- Source archive: `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`.
- Harness executable source: `ef31ccba6092e9b7dc9f0a9c5f5310c6e20ccc74`.
- Harness source archive: `archive/alfworld-action-dialect-correction-ef31ccb`.
- Main Lambda root: `/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.

## Frozen identities

- Model/tokenizer: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Terminal RCMF checkpoint SHA-256:
  `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`;
  it was not retrained.
- v3 lock file SHA-256:
  `8917fa535623d7678497a75c80c6d4b7481fe11c3c52ba2d245c8923bb0fd44e`.
- v3 lock identity:
  `f6103813cd2f4c76d15e2784c2eece9758c250042ef3c06d01ccde89daa4ba05`.
- Generation/action identity:
  `80400891d46bc0395961b978449d03ae709d50d93d39e8d665ce9560a0224705`.
- Action bridge: `react_put_in_on_to_alfworld_move_to_v1`.
- Task set/order: `2410f2c2...` / `c49e3fab...`.
- Prompt/model/checkpoint/task/generation/evaluator identities are unchanged
  except for the prospectively versioned exact action-dialect bridge.

## Verified evidence

1. The preserved v2 bare output completed 134/134 with 16 successes and zero
   typed failures. A complete 6,215-step audit found exact parser/environment/
   evaluator correspondence, then found all 549 upstream ReAct `put ... in/on
   ...` commands returned `Nothing happens.`.
2. Pinned ReAct source emits `put ... in/on ...`; pinned ALFWorld grammar accepts
   `move ... to ...`. Same-state TRAIN controls made literal `put` inert and
   official `move` exact and terminal-successful in all five placement families.
3. The actual RCMF adapter TRAIN probe UUID
   `16108bc6-36c2-43ad-9941-5c763adcc276` recorded five bridge activations and
   five exact official environment actions/observations/rewards/done/won values,
   with 5/5 terminal success. Artifact SHA-256: `c4e1b121...`.
4. The independent Harness-owned TRAIN probe UUID
   `2ba17f91-d6a9-4105-9226-a53cd787f62a` passed the same fixed cases without
   importing RCMF.
5. Real 134-task CPU closure UUID
   `f0644e1d-488e-4233-ba98-0e40c01e8b73` accepted correct set/order, rejected
   the same set in wrong order, restored reversed adapter input exactly to
   `c49e3fab...`, accepted correct embedded-v3 identity, and rejected wrong
   embedded order, lock file, lock identity, generation identity, bridge
   identity, and executed action. Artifact SHA-256: `c55ca047...`.
6. Local final source tests passed 1,120 with three skips; Lambda focused tests
   passed 31. Historical counts are separately preserved: old source 1,111;
   `12d4b1a` 1,113; `91598b6` 1,116.

Git-safe evidence:

- `research/results/alfworld/action_dialect_v3_rcmf_adapter_train_probe.json`
- `research/results/alfworld/action_dialect_v3_real_track_r_order_closure.json`
- `research/results/alfworld/action_dialect_v3_test_report.json`
- `research/plans/alfworld_track_r_run_identities_v3.json`
- `research/plans/alfworld_track_r_first30_task_ids_v3.json`

## Preregistered first-30 gate

- Task selection: first 30 tasks in the unchanged physical valid-unseen
  manifest order, fixed before v3 model output.
- List file SHA-256: `2c96acd7f...`.
- Order/set identities: `dec3192d...` / `40593c9c...`.
- Bare UUID: `de0e3118-e852-4709-8887-304620c27cc6`.
- Audit UUID: `0d41c5b9-921f-41dd-a464-657afd7595da`.
- Batch size: 16; no task removal, substitution, or truncation.
- Required structural result: exact population/order/run/model/lock/generation/
  bridge identity, exact raw-line to model-action to environment-action chain,
  at least one bridge activation, zero matching `put` sent literally, zero
  typed failures, exact evaluator closure.
- Required outcome-independent repair signal: total successes >13/30 and
  `pick_and_place` successes >=1/12.
- Pass: `READY_FOR_CORRECTED_FULL_134_BARE`.
- Fail: `STOP_ACTION_DIALECT_CORRECTION_DID_NOT_IMPROVE_FIRST_30`; no 134 or
  RCMF-C launch.

Conditional full bare UUID `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47` and RCMF-C
UUID `fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23` were also fixed before first-30
outcomes, but they are not yet authorized.

## Exact commands

Local tests used process-start `PYTHONHASHSEED=25101`:

```text
conda run -n appworld_env python -m pytest -q tests/test_alfworld_portable_v2.py tests/test_alfworld_result_analysis.py --basetemp C:/gbz/pytest-tmp/alfworld-v3-focused
conda run -n appworld_env python -m pytest -q --basetemp C:/gbz/pytest-tmp/rcmf-alfworld-v3-source-final-full
```

Lambda TRAIN probe and order closure used:

```text
PYTHONPATH=.:<task-owned-python-packages> PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/probe_alfworld_action_dialect_adapter.py --task-manifest <sealed-task-manifest> --trajectory-corpus <sealed-train-corpus> --task-ids <fixed-six-family-train-list> --data-root <sealed-data-root> --output <uuid-owned-output> --diagnostic-uuid 16108bc6-36c2-43ad-9941-5c763adcc276 --source-commit 057693f86b7f599a3f42ed9548c9d543739a61f5
PYTHONPATH=. PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/verify_alfworld_track_r_order_closure.py --task-manifest <sealed-task-manifest> --output <uuid-owned-output> --diagnostic-uuid f0644e1d-488e-4233-ba98-0e40c01e8b73 --source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094
```

## Deviations and failures

- Lambda cannot fetch either private repository using its local GitHub SSH
  credentials. Source was transferred as verified complete/incremental Git
  bundles, SHA-256 `038faecb...` and `c2e57a96...`, then applied only through
  `git fetch <bundle>` and `git merge --ff-only`.
- An initial direct private Harness clone left an incomplete preserved path;
  the verified Harness source was instead cloned from complete bundle SHA-256
  `b355c38...` into a separate exact path.
- The first RCMF TRAIN probe invocation lacked repository `PYTHONPATH`; the
  second lacked the already-existing task-owned ALFWorld/TextWorld package
  path. Both stopped before any environment reset and emitted no artifact. The
  third invocation resolved the documented task-owned runtime paths and passed.
- The first full local suite exposed only a two-line bootstrap-field formatting
  error (1,118 passed, two failed, three skipped). Rendering the same handoff
  path on the required single line repaired it; final source passed 1,120.
- The initial executable archive `archive/rcmf-alfworld-action-dialect-correction-057693f`
  remains preserved. It was prospectively superseded before model execution by
  final source/archive `37e3ad1` solely to include the durable real-order closure
  script.

## Negative statements

NO MODEL FORWARD OR GENERATION WAS RUN BY THESE PRE-MODEL GATES.

NO TRAINING OR RETRAINING WAS RUN.

NO RCMF-C OR FULL-134 V3 RUN WAS STARTED.

NO WEBSHOP PROCESS OR ARTIFACT WAS MODIFIED OR PREEMPTED.

NO PROMPT, MODEL, CHECKPOINT, TASK, OR EVALUATOR IDENTITY WAS TUNED.
