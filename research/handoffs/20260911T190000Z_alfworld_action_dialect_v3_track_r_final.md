# ALFWorld Action-Dialect V3 Final Track R Handoff

## Outcome

Decision:
`COMPLETE_ELIGIBLE_TRACK_R_PAIRED_RESULT_NO_OBSERVED_RCMF_IMPROVEMENT`.

The corrected, preregistered full bare and RCMF-C arms are complete and
eligible. Each solved 45 of the same 134 official `valid_unseen` Track R tasks
in the same frozen manifest order, with zero typed failures. The paired point
estimate is exactly zero. This supplies no observed RCMF advantage; the
confidence interval spans effects in both directions, so it does not establish
equivalence.

This completes the action-boundary/action-dialect corrective milestone. It
does not authorize tuning, rerunning, retraining, or another ALFWorld condition.

## VERIFIED

### Source and frozen identities

- Branch: `adapt/alfworld-v1`.
- Starting records commit for the final RCMF-C activity:
  `e57ce7ceb16ab292f8d2c91413b64051b7526890`.
- Executable RCMF source:
  `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094`.
- RCMF source archive:
  `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`.
- Harness source/archive:
  `ef31ccba6092e9b7dc9f0a9c5f5310c6e20ccc74` /
  `archive/alfworld-action-dialect-correction-ef31ccb`.
- Track/role: `alfworld_upstream_react_valid_unseen_reference_v1` /
  `UPSTREAM_PROTOCOL_REFERENCE`.
- Task set/order SHA-256:
  `2410f2c2a92346d63bc00e403a51122c8123d3978c3c29f8c86864556ead5534` /
  `c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8`.
- Model: `Qwen/Qwen3-8B` at exact revision
  `b968826d9c46dd6066d109eabc6255188de91218`, bfloat16, thinking disabled.
- Prompt/chat SHA-256:
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a` /
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`.
- V3 lock file/identity SHA-256:
  `8917fa535623d7678497a75c80c6d4b7481fe11c3c52ba2d245c8923bb0fd44e` /
  `f6103813cd2f4c76d15e2784c2eece9758c250042ef3c06d01ccde89daa4ba05`.
- Generation/action identity:
  `80400891d46bc0395961b978449d03ae709d50d93d39e8d665ce9560a0224705`.
- Bridge: `react_put_in_on_to_alfworld_move_to_v1`.
- RCMF checkpoint SHA-256:
  `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
- Generation batch size: 16.

### Complete run results

- Bare UUID `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47`:
  134/134 tasks, 45 official successes, zero typed failures,
  10,700.145958 seconds. Raw episodes SHA-256:
  `d6865e084135cdf9031cd98228c35163cceb08faa7302ce51e03081c17541f6e`.
- RCMF-C UUID `fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23`:
  134/134 tasks, 45 official successes, zero typed failures,
  10,910.843307 seconds. Start/end:
  `2026-09-11T15:42:59.755111+00:00` /
  `2026-09-11T18:44:50.598390+00:00`. Peak CUDA allocation:
  43,686,247,936 bytes. Raw episodes SHA-256:
  `c5aa390ecdc68c41bda0e35d9a401184e7e9b77a3be1ee41089e6edc6c4f8953`.

Strict paired analysis UUID `2a1acdf5-7841-43e9-9f27-52f7f8b4a6d5`
produced:

- Bare: 45/134 = 0.3358208955.
- RCMF: 45/134 = 0.3358208955.
- Both correct / both wrong: 40 / 84.
- RCMF gains / losses: 5 / 5.
- Absolute accuracy delta: 0.
- Paired nonparametric task bootstrap: 100,000 replicates, seed 25,101,
  nearest-rank 95% CI [-0.0447761194, 0.0447761194].
- Exact two-sided McNemar: discordant=10, p=1.0.
- Canonical result SHA-256:
  `912d6ecbd04184a43abbcdd5166821dc4047a2350b9f25967aa81662431da2a7`.

Family results are descriptive and selected no setting:

| Family | n | Bare | RCMF | Delta |
|---|---:|---:|---:|---:|
| `look_at_obj` | 18 | 15 | 15 | 0 |
| `pick_and_place` | 24 | 12 | 13 | +1/24 |
| `pick_clean_then_place` | 31 | 11 | 9 | -2/31 |
| `pick_cool_then_place` | 21 | 4 | 5 | +1/21 |
| `pick_heat_then_place` | 23 | 1 | 1 | 0 |
| `pick_two_obj` | 17 | 2 | 2 | 0 |

### Action, environment, evaluator, and order closure

Bare audit UUID `68dc78c6-61f6-48b5-9630-7c5706d0d7dd` checked all
5,472 steps and passed with 32 exact bridges, zero matched puts sent literally,
zero typed failures, and zero violations.

RCMF audit UUID `d2b94d8b-92c3-470c-8536-228911603e71` checked all
5,483 steps and passed:

- raw model text to raw first decoded line: exact;
- raw text to normalized parsed action: exact;
- parsed action to translated/pass-through environment command: exact;
- step bridge flag and bridge identity: exact;
- physical 134-task manifest order and embedded identities: exact;
- every episode content hash: exact;
- returned observations present;
- `official_success == terminal environment_done AND official_won`: exact;
- binary raw reward and terminal status: exact;
- violations: 0; typed failures: 0;
- exact bridge activations: 33; matched puts sent literally: 0.

Descriptive RCMF behavior counts demonstrate why nonempty-action validation
alone is not evidence of semantic correctness: 2,967 think actions, 713
consecutive repeated executed commands, 3,644 `Nothing happens.` responses,
1,839 nonempty non-rejection observations, and 5,483 syntactically nonempty
actions. These observations did not change the parser, prompt, model,
checkpoint, generation configuration, task set, or analysis.

Representative exact bridge evidence includes the model line
`> put mug 1 in/on desk 1`, parsed model action
`put mug 1 in/on desk 1`, executed command `move mug 1 to desk 1`, returned
observation `You move the mug 1 to the desk 1.`, and terminal done/won true.
Representative non-think rejected commands and observations are preserved in
`action_dialect_v3_full_rcmf_audit.json`.

The earlier real-population CPU closure remains valid: correct set plus
manifest order passes; the same set in wrong order is rejected; reversed
adapter input is restored exactly to `c49e3fab...`; wrong embedded order, lock,
generation, bridge, and executed-action identities are rejected.

### P00-P11 closure

The corrected evidence map binds the v3 execution lock, unchanged TRAIN
corpus/ledger/training/checkpoint evidence, corrected bare/RCMF summaries, and
the new paired summary. Portable run UUID
`2a1acdf5-7841-43e9-9f27-52f7f8b4a6d5` passed all 12 phases. Its compact
summary SHA-256 is
`9183842f06955339a6c2bfba839edef16e5809d00020552f54cb935c0e0483ae`.
The earlier sorted-order P00-P11 result remains preserved and ineligible.

### Runtime and postconditions

- Lambda Python: 3.11.15.
- Linux: 6.8.0-1046-nvidia, glibc 2.35.
- PyTorch: 2.11.0+cu128; compiled CUDA: 12.8.
- Transformers/tokenizers: 4.57.6 / 0.22.2.
- TextWorld: 1.7.0.
- FlashAttention: 2.8.3.post1; sealed installation-manifest SHA-256
  `34a7a900ea4e60af4f9b8b7c6ee410926f5d55c653aea971e40541b501cbb15c`.
- GPU/driver: NVIDIA H100 80GB HBM3 / 580.105.08.
- Post-run GPU compute process count: zero.
- Lambda worktree: clean, detached exactly at `37e3ad1`.

## Artifacts

Committed compact records:

- `research/results/alfworld/action_dialect_v3_full_rcmf_summary.json`
  (remote-byte-exact SHA-256 `5e40b76d...`).
- `research/results/alfworld/action_dialect_v3_full_rcmf_audit.json`
  (remote-byte-exact SHA-256 `a428e81d...`; canonical identity
  `fb884c37...`).
- `research/results/alfworld/action_dialect_v3_paired_summary.json`
  (remote-byte-exact SHA-256 `06c12612...`).
- `research/results/alfworld/action_dialect_v3_per_task_results.jsonl`
  (134 rows, remote-byte-exact SHA-256 `b0821da8...`).
- `research/results/alfworld/action_dialect_v3_phase_evidence_map.json`
  (remote-byte-exact SHA-256 `dd8cfa28...`).
- `research/results/alfworld/action_dialect_v3_p00_p11_summary.json`
  (remote-byte-exact SHA-256 `9183842f...`).
- `research/results/alfworld/action_dialect_v3_final_artifact_index.json`.

The 21,139,000-byte RCMF episode JSONL remains Lambda-only. The launch log and
launch shell also remain Lambda-only and are content-addressed in the artifact
index. No model weights, checkpoint, cache, dataset, credential, or giant log
was committed.

## Exact execution commands

`RUN_ROOT` below is
`/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
The task-owned RCMF launch shell is SHA-256
`a19302e1503da66d62966aaf46974c3f3fe842a3ebe2aa3b25c18b2d2aef9a43`.
Its exact model command was:

```bash
PYTHONPATH=.:$RUN_ROOT/python-packages:$RUN_ROOT/python-packages-flash PYTHONHASHSEED=25101 CUDA_VISIBLE_DEVICES=0 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_alfworld_agent.py --condition rcmf --split valid_unseen --task-manifest /lambda/nfs/rcmf-persist/project/runs/harness_readiness/alfworld/fd8622dd-482f-4eba-b27f-c3c32eb2b68f/harness-source/research/benchmarks/alfworld/task_manifest.json --trajectory-corpus $RUN_ROOT/corpus-final/official_train_corpus.jsonl --prompt-root assets/prompts/alfworld/react_task_type_two_demo_v1 --data-root $RUN_ROOT/data/json_2.1.1 --model-snapshot /home/ubuntu/.cache/huggingface/hub/models--Qwen--Qwen3-8B/snapshots/b968826d9c46dd6066d109eabc6255188de91218 --benchmark-lock configs/benchmark/alfworld/track_r_execution_lock_v3.json --checkpoint $RUN_ROOT/formal/training_c5f81480/terminal_checkpoint.pt --output $RUN_ROOT/corrective_action_v3/formal/rcmf_fbef9769/episodes.jsonl --summary-output $RUN_ROOT/corrective_action_v3/formal/rcmf_fbef9769/summary.json --run-uuid fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23 --source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094 --formal --generation-batch-size 16
```

Paired analysis:

```bash
PYTHONPATH=. PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/analyze_alfworld_paired_results.py --bare $RUN_ROOT/corrective_action_v3/formal/bare_dcc9d31d/episodes.jsonl --rcmf $RUN_ROOT/corrective_action_v3/formal/rcmf_fbef9769/episodes.jsonl --task-manifest /lambda/nfs/rcmf-persist/project/runs/harness_readiness/alfworld/fd8622dd-482f-4eba-b27f-c3c32eb2b68f/harness-source/research/benchmarks/alfworld/task_manifest.json --output-dir $RUN_ROOT/corrective_action_v3/formal/paired_2a1acdf5
```

Portable closure:

```bash
PYTHONPATH=. PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_alfworld_p00_p11.py --config configs/pipeline/rcmf_alfworld_v1.yaml --evidence-map $RUN_ROOT/corrective_action_v3/formal/phase_evidence_map_2a1acdf5.json --output-root $RUN_ROOT/corrective_action_v3/formal/p00_p11_2a1acdf5 --run-uuid 2a1acdf5-7841-43e9-9f27-52f7f8b4a6d5 --source-commit 37e3ad1c2fd0c3bbcd54da3c64b5613edff29094
```

The RCMF audit was a CPU-only Python stdin diagnostic run at source `37e3ad1`
with `PYTHONPATH=.` and `PYTHONHASHSEED=25101`, taking the RCMF episodes,
summary, sealed task manifest, output path, and diagnostic UUID as exact
arguments. The complete predicates, counts, representative evidence, input
paths/hashes, and canonical result hash are serialized in the committed audit.

## Test report

Final focused tests passed 36/36. The complete suite passed 1,120 with three
skips and zero failures in 113.64 seconds. `compileall`, JSON/JSONL parsing,
paired/audit canonical-hash recomputation, 134-row uniqueness and delta checks,
`git diff --check`, remote-to-repository byte hashes, the records-only diff
guard, and the boundary-aware secret scan all passed. After final document
assembly, release-document tests passed again, 5/5.

Two intermediate focused attempts each passed 34 and failed two release-document
checks: first because the context referenced this handoff before the file was
created, then because the unchanged generic validator requires its historical
Portable V2.1 no-result marker. Creating the handoff and restoring that exact
marker with an explicit historical-only/superseded qualification resolved both;
no generic source or scientific contract changed. Full detail is in
`action_dialect_v3_final_test_report.json`.

Historical source counts remain distinct: 1,111 before order repair; 1,113 at
`12d4b1a`; 1,116 at action-boundary source `91598b6`; and 1,120 with three
skips at final executable source `37e3ad1`.

## INFERENCE

- The exact Track R result gives no observed benefit from the trained RCMF
  field under this frozen endpoint. Because only ten tasks are discordant and
  the interval spans negative and positive effects, the data are also
  compatible with modest effects in either direction.
- The very high think/rejection/repeat counts describe this frozen policy's
  interaction behavior. They are not grounds for post-outcome parser or prompt
  changes in this milestone.

## UNVERIFIED / out of scope

- This Track R result does not establish equivalence, generalize to Track S,
  or validate another memory method.
- No new ALFWorld run is authorized. Any follow-up needs a new prospective
  hypothesis, identity, and outcome firewall.

## Implementation deviations

1. The task-owned launch shell was invoked as
   `bash launch_alfworld_v3_full_rcmf_fbef9769.sh --preflight-only`, but that
   shell had no argument handling. It passed its internal source, clean-tree,
   empty-output, empty-GPU, checkpoint-hash, and full-bare-audit gates, then
   launched the exact RCMF command in the foreground SSH session instead of
   tmux. The active run was not killed or duplicated. No identity or setting
   changed.
2. The first P00-P11 invocation used
   `configs/benchmark/alfworld/compact_rcmf_v1.yaml`. The current strict runner
   rejected its legacy fields while parsing config, before output-root creation
   or any phase execution. The unchanged evidence map and UUID then passed
   12/12 with `configs/pipeline/rcmf_alfworld_v1.yaml`.
3. No network retrieval occurred. Existing task-owned dependencies, model
   snapshot, data, corpus, checkpoint, and caches were read in place.

## Negative statements

- NO UNAFFECTED MODEL OR CHECKPOINT RETRAINING WAS RUN.
- NO VALID_UNSEEN-DRIVEN PROMPT, MODEL, CONFIGURATION, PARSER, TASK, OR
  THRESHOLD SEARCH WAS RUN.
- NO TASK WAS REMOVED OR SUBSTITUTED.
- NO WEBSHOP PROCESS, SOURCE, OR ARTIFACT WAS MODIFIED OR PREEMPTED.
- NO NEW RCMF ADAPTER WAS IMPLEMENTED.
- NO RAW EPISODE FILE, MODEL WEIGHT, CACHE, CHECKPOINT, DATASET, OR CREDENTIAL
  WAS COMMITTED.
