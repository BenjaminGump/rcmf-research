# ALFWorld Action-Dialect V3 Full-Bare Structural Gate

## Outcome

Decision:
`PASS_FULL_134_BARE_ACTION_DIALECT_STRUCTURAL_AUDIT_RCMF_C_REVIEW_REQUIRED`.

The corrected full bare arm completed all 134 Track R `valid_unseen` tasks and
passed the complete fail-closed structural/action/evaluator audit. The
user-directed all-wrong/basic-fault stop branch did not trigger. This record
authorizes only the already-preregistered matched RCMF-C arm next; it is not a
paired scientific result by itself.

## VERIFIED

- Branch: `adapt/alfworld-v1`.
- Executable source: `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094`.
- Source archive: `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`.
- Harness source: `ef31ccba6092e9b7dc9f0a9c5f5310c6e20ccc74`.
- Bare UUID: `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47`.
- Audit UUID: `68dc78c6-61f6-48b5-9630-7c5706d0d7dd`.
- Population/order: 134/134 in physical manifest order, order SHA-256
  `c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8`.
- Task set SHA-256:
  `2410f2c2a92346d63bc00e403a51122c8123d3978c3c29f8c86864556ead5534`.
- Model revision: `b968826d9c46dd6066d109eabc6255188de91218`.
- Lock file/identity SHA-256: `8917fa535623d7678497a75c80c6d4b7481fe11c3c52ba2d245c8923bb0fd44e`
  / `f6103813cd2f4c76d15e2784c2eece9758c250042ef3c06d01ccde89daa4ba05`.
- Generation/action identity:
  `80400891d46bc0395961b978449d03ae709d50d93d39e8d665ce9560a0224705`.
- Bridge: `react_put_in_on_to_alfworld_move_to_v1`.
- Batch size: 16.
- Start/end: `2026-09-11T12:34:15.503751+00:00` /
  `2026-09-11T15:32:35.649683+00:00`.
- Elapsed: 10,700.145958 seconds.
- Exit: zero; model weights were loaded and generation was run for this bare
  arm.
- Results: 45/134 official successes, zero typed failures.
- Audit: 5,472 steps, 32 bridge activations, zero matched puts sent literally,
  zero violations, exact terminal done/won/evaluator correspondence.
- Post-run: bare tmux absent, H100 empty, Lambda worktree clean at exact source.

Family outcomes are descriptive only: look 15/18, placement 12/24, clean
11/31, cool 4/21, heat 1/23, two-object 2/17. They selected no setting.

## Artifact identities

- Raw episodes remain on Lambda only: SHA-256
  `d6865e084135cdf9031cd98228c35163cceb08faa7302ce51e03081c17541f6e`,
  20,428,061 bytes.
- Compact summary remote SHA-256:
  `6d1a852825bc17eb2a620ecb0e70f3ee5da69d49954b2939cd3f7a2d760e34f4`.
- Audit remote SHA-256:
  `3a54bf8d4ffe3a7b5b986e847672467a76f987c83abd80a382a5f84c1a433b46`;
  canonical identity SHA-256
  `b99f012074c8df8c27b8e2942ae10278ef05cd862aaca11d9968bbab51c6ee64`.
- Launch log SHA-256:
  `d9a134d840689afb572a6fe8a3a55d1e9782486b7a1105f033b3fa280f97c0a8`.
- Exact launch script SHA-256:
  `86b04535ee917a5f15f53ce3a7ede9c25e9e39de24266ce29bc988c7676fd1ef`.
- Repository artifacts:
  `research/results/alfworld/action_dialect_v3_full_bare_summary.json`,
  `research/results/alfworld/action_dialect_v3_full_bare_audit.json`, and
  `research/results/alfworld/action_dialect_v3_full_bare_artifact_index.json`.

## Exact execution commands

```bash
PYTHONPATH=.:$RUN_ROOT/python-packages:$RUN_ROOT/python-packages-flash PYTHONHASHSEED=25101 CUDA_VISIBLE_DEVICES=0 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_alfworld_agent.py --condition bare --split valid_unseen --task-manifest /lambda/nfs/rcmf-persist/project/runs/harness_readiness/alfworld/fd8622dd-482f-4eba-b27f-c3c32eb2b68f/harness-source/research/benchmarks/alfworld/task_manifest.json --trajectory-corpus $RUN_ROOT/corpus-final/official_train_corpus.jsonl --prompt-root assets/prompts/alfworld/react_task_type_two_demo_v1 --data-root $RUN_ROOT/data/json_2.1.1 --model-snapshot /home/ubuntu/.cache/huggingface/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218 --benchmark-lock configs/benchmark/alfworld/track_r_execution_lock_v3.json --output $RUN_ROOT/corrective_action_v3/formal/bare_dcc9d31d/episodes.jsonl --summary-output $RUN_ROOT/corrective_action_v3/formal/bare_dcc9d31d/summary.json --run-uuid dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47 --source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094 --formal --generation-batch-size 16
PYTHONPATH=. PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/audit_alfworld_action_dialect_run.py --episodes $RUN_ROOT/corrective_action_v3/formal/bare_dcc9d31d/episodes.jsonl --summary $RUN_ROOT/corrective_action_v3/formal/bare_dcc9d31d/summary.json --task-manifest /lambda/nfs/rcmf-persist/project/runs/harness_readiness/alfworld/fd8622dd-482f-4eba-b27f-c3c32eb2b68f/harness-source/research/benchmarks/alfworld/task_manifest.json --benchmark-lock configs/benchmark/alfworld/track_r_execution_lock_v3.json --output $RUN_ROOT/corrective_action_v3/formal/bare_audit_68dc78c6.json --expected-count 134 --expected-source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094 --expected-run-uuid dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47 --expected-generation-batch-size 16 --diagnostic-uuid 68dc78c6-61f6-48b5-9630-7c5706d0d7dd
```

`RUN_ROOT` is
`/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.

## INFERENCE

- The exact environment-boundary bridge repairs the independently proven
  prompt/environment action-dialect mismatch across the full population. The
  structural evidence supports proceeding to the matched RCMF-C arm; it does
  not predict whether RCMF will improve the bare result.

## UNVERIFIED / next step

- RCMF-C has not started in this record.
- Before launch, recheck H100 ownership, exact source/head/cleanliness, absent
  target output root, and checkpoint SHA-256
  `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
- Run only preregistered RCMF-C UUID
  `fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23` with the identical full population,
  order, model, prompt, generation/action lock, batch size, and evaluator.
- After completion, run the strict paired analyzer and preserve/push compact
  results. Do not retrain or performance-search.

## Negative statements

- NO MODEL OR CHECKPOINT RETRAINING WAS RUN.
- NO VALID_UNSEEN-DRIVEN PROMPT/MODEL/CONFIG SEARCH WAS RUN.
- NO TASK WAS REMOVED OR SUBSTITUTED.
- NO WEBSHOP PROCESS OR ARTIFACT WAS MODIFIED.
- NO RCMF-C MODEL RUN WAS STARTED BEFORE THE FULL-BARE STRUCTURAL GATE.
