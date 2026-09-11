# ALFWorld Action-Dialect V3 First-30 Pass

Decision: `READY_FOR_CORRECTED_FULL_134_BARE`

RCMF-C is still blocked. The only next model run is the already-preregistered
complete bare UUID `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47`; it must finish and
pass its full structural audit before RCMF-C may start.

## Identity and result

- Branch/source: `adapt/alfworld-v1` /
  `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094`.
- Source archive: `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`.
- Pre-run records: `496d9cc52851790eaf7287652d8c781d6263bad5`.
- Run UUID: `de0e3118-e852-4709-8887-304620c27cc6`.
- Audit UUID: `0d41c5b9-921f-41dd-a464-657afd7595da`.
- Exact task count/order/set: 30 / `dec3192d...` / `40593c9c...`.
- Model revision: `b968826d9c46dd6066d109eabc6255188de91218`.
- Lock file/identity: `8917fa5356...` / `f6103813cd...`.
- Generation/action identity: `80400891d4...`.
- Bridge: `react_put_in_on_to_alfworld_move_to_v1`.
- Batch size: 16.
- Runtime: 2,260.963159 seconds; peak CUDA 25,119,626,240 bytes.
- Episodes SHA-256:
  `af891bc6c101f303d1426cfd5a2483d5b9056088ab6b710dc533809b952620e5`.

## Gate

- Population/order/episode/run/model/lock/generation/bridge identities: pass.
- Raw first line, normalized model action, exact executed action, bridge flag,
  environment observation, done/won, and evaluator closure: pass.
- Rows/steps: 30/1,000.
- Structural violations: 0.
- Typed failures: 0.
- Exact bridge activations: 9.
- Matching puts sent literally: 0.
- Official successes: 24/30 versus fixed v2 reference 13/30.
- `pick_and_place`: 9/12 versus fixed v2 reference 0/12.
- Outcome-improvement gate: pass.

## Representative raw chains

These are the first three bridge activations in frozen task/step order,
selected for illustration only after the gate and not used to change settings:

1. `trial_T20190908_125200_737896`, step 30: raw first line `> put mug 1
   in/on desk 1` -> parsed `put mug 1 in/on desk 1` -> executed `move mug 1 to
   desk 1` -> observation `You move the mug 1 to the desk 1.` -> reward 1,
   done true, won true.
2. `trial_T20190909_203041_433487`, step 27: the same exact model/environment
   mapping and official success chain for `mug 1` to `desk 1`.
3. `trial_T20190909_210238_431966`, step 26: parsed `put mug 2 in/on desk 1`
   -> executed `move mug 2 to desk 1` -> exact move observation, reward 1,
   done true, won true.

Descriptive-only totals: 488 think actions, 60 immediate repeated model
actions, 569 `Nothing happens.` replies, and 431 other environment replies.
There were 25 `put` model actions. The nine exact literal `in/on` forms were
bridged. Sixteen natural `in`, `on`, or `under` variants were outside the
preregistered bridge, passed through unchanged, and received `Nothing
happens.`. No post-outcome broadening was made.

## Artifacts

- Audit: `research/results/alfworld/action_dialect_v3_first30_audit.json`,
  SHA-256 `dcd93bcd...`.
- Summary: `research/results/alfworld/action_dialect_v3_first30_summary.json`,
  SHA-256 `eb2dfb1a...`.
- Descriptive evidence:
  `research/results/alfworld/action_dialect_v3_first30_descriptive_evidence.json`,
  SHA-256 `7be3f8ba...`.
- Artifact index:
  `research/results/alfworld/action_dialect_v3_first30_artifact_index.json`.
- Raw episodes remain on Lambda only at the UUID-owned output root.

## Runtime deviations

- Attempt 001 stopped before model load or forward because the already sealed
  task-owned FlashAttention package directory was absent from `PYTHONPATH`.
  Its immutable log SHA-256 is `f11fcba5...`; no episode row existed.
- Attempt 002 added only the already-sealed package path and completed under
  the same UUID; log SHA-256 `50f261eb...`.
- H100 was empty at preflight. An unrelated AppWorld pilot started later. It
  was identified only to avoid interference, never modified, and exited
  naturally before ALFWorld completion. No AppWorld scientific output was read.

## Exact execution and audit commands

```text
PYTHONPATH=.:<task-owned-alfworld-packages>:<sealed-flash-attn-packages> PYTHONHASHSEED=25101 CUDA_VISIBLE_DEVICES=0 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_alfworld_agent.py --condition bare --split valid_unseen --task-manifest <sealed-task-manifest> --trajectory-corpus <sealed-train-corpus> --prompt-root assets/prompts/alfworld/react_task_type_two_demo_v1 --data-root <sealed-data-root> --model-snapshot <exact-b968826-snapshot> --benchmark-lock configs/benchmark/alfworld/track_r_execution_lock_v3.json --task-ids-json <fixed-first30-list> --output <uuid-root>/episodes.jsonl --summary-output <uuid-root>/summary.json --run-uuid de0e3118-e852-4709-8887-304620c27cc6 --source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094 --generation-batch-size 16
PYTHONPATH=. PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/audit_alfworld_action_dialect_run.py --episodes <episodes> --summary <summary> --task-manifest <sealed-task-manifest> --benchmark-lock configs/benchmark/alfworld/track_r_execution_lock_v3.json --output <audit> --expected-count 30 --expected-source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094 --expected-run-uuid de0e3118-e852-4709-8887-304620c27cc6 --expected-generation-batch-size 16 --diagnostic-uuid 0d41c5b9-921f-41dd-a464-657afd7595da
```

NO TRAINING OR RETRAINING WAS RUN.

NO RCMF CHECKPOINT WAS LOADED.

NO RCMF-C RUN WAS STARTED.

NO PROMPT, MODEL, CHECKPOINT, TASK, GENERATION SETTING, OR EVALUATOR CHANGED.

NO WEBSHOP OR UNRELATED GPU PROCESS WAS MODIFIED.
