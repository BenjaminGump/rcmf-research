# Handoff: RCMF × AgentBench-FC WebShop Standard-200 Final

Timestamp: `2026-09-11T02:31:57Z`

## Terminal decision

`RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT`

The end-to-end WebShop milestone is complete. All 600 required trajectories
ran on exact standard indices `[0,200)` under the three jointly frozen
conditions. No task in `[200,500)` ran. The correct-field point estimate is
slightly positive but its paired interval includes zero, and matched shuffle
has the same exact-success count. This is not evidence of a reliable,
memory-specific improvement.

## Repository boundary

Harness:

- repository: `BenjaminGump/agent-memory-eval-harness`;
- branch: `dataset/webshop-e2e-v3`;
- executable source: `9e1f3963d42de871c23a1d0d87c1299e8387a420`;
- records: `2088d0d77b9a32a76a8d1823cb199dd13d0207af`;
- runtime source archive: `archive/webshop-agentbench-fc-runtime-v1-b5bd698`;
- records tag: `webshop-agentbench-fc-runtime-v1-records-c10dc1d`;
- worktree clean, branch pushed, 88/88 tests passed, `git fsck` reports only
  historical dangling objects and no corruption.

RCMF:

- repository: `BenjaminGump/rcmf-research`;
- branch: `adapt/webshop-v1`;
- construction source: `084566c56a4fb4f5457cd73719ed351bf8f3a594`;
- construction archive: `archive/rcmf-webshop-construction-v1-084566c`;
- frozen method source: `be711705b5824d6b61ca05a9ea0b19be8882ec8e`;
- method archive: `archive/rcmf-webshop-method-v1-be71170`;
- evaluator source: `77508bd8aba67576038ecdc513c0e91fd825a4a9`;
- evaluator archive: `archive/rcmf-webshop-evaluator-v2-77508bd`;
- final records: the records-only descendant containing this handoff, resolved
  by pushed `adapt/webshop-v1` HEAD and the final records archive tag;
- source-to-records changes are limited to docs, task state, result/audit JSON,
  experiment records, and this handoff; no code/config/test change follows the
  evaluator source.

## Lambda roots

- Harness runtime root:
  `/lambda/nfs/rcmf-persist/project/runs/webshop/webshop_e2e_v3_bd935345-ee26-4955-9290-682ba166f266`.
- RCMF root:
  `/lambda/nfs/rcmf-persist/project/runs/webshop/webshop_rcmf_v1_d7853aba-7e27-4bb9-9f79-04447416ca3e`.
- Method campaign UUID: `eb48a0ec-fa98-4178-af91-77382d803c0a`.
- Construction campaign UUID: `ec917d3b-52e5-43d8-b3ca-2831b9ce9e35`.

Raw tasks, model/field packages, representation tensors, Lucene index, data,
logs, and caches remain on Lambda NFS. They are not committed.

## Benchmark and runtime identity

- AgentBench `d1e4a10db08c87075c78972e48ecc182be03e2d5`, Apache-2.0.
- Princeton WebShop `64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd`, MIT.
- Immutable data image
  `sha256:e8bd3b120fe57653ecd902a6629d11a7d68f738e15f56a7e0aefdc1139f38b09`.
- Derived runtime image
  `sha256:af342a9b210bc8bde14eccb3c858beefc79de7203b63d3286beb327b099d0cb9`.
- Products SHA256 `2ef591d65df3af89e972ab72468eb82cbf124d876552d9f3678667edd620a6c8`.
- Instructions SHA256 `1d36af476bdb8f82a5da62bd8acdabe54cd8de2fa84010d37da5c4890feb447e`.
- Human-instruction metadata SHA256
  `cf78667548a71786e1d9049c24b802e48e1084ad4bb021cae56ce1f6d96954a3`.
- Lucene index: 19 files, 3,601,981,280 bytes, manifest
  `4357bdc2b93dd12036600f375aa15ec1fce65f20b7ffef797c65d8b35cc752ae`.
- Prompt `agentbench_fc_webshop_v1`, system SHA256 `4d2c3617...`, tools
  SHA256 `be6b939a...`, `content_changed=false`.
- Text observations; required search/click function calls; continuous raw
  reward; full success exactly `1.0`.
- Raw data redistribution rights are not claimed.

## Corpus and method

Construction processed train indices `[1500,3000)` before standard outcomes.
It admitted 302 `AGENT_GENERATED`, exact-success, replay-valid trajectories and
1,205 complete transitions (321 search, 884 click). Corpus SHA256 is
`b5f1252f9823dca36ef3ee2fd3f0c5278c5ca527016abee182844bd094ff958e`.
No human archive, IL/finalized archive, evaluation trajectory, or oracle was
admitted.

Frozen Qwen3-8B snapshot is `b968826d...`. The 1,205-memory field has shapes
`A[960,8,256]`, `B[8,256]`. Method package SHA256 is
`b54ef1c0119a95983ec19d068543ded14036dd5704ee9a200930d361deb6f4df`.
Qwen, selector, writer, and reader are frozen. Deployment has no raw-memory
prompt, top-k, nearest-neighbor retrieval, per-memory scoring, or memory scan.

## Validation and formal result

The fixed validation population `[500,550)` produced a pre-standard `PROCEED`
decision: RCMF-C minus B0 `+0.0398667`, and RCMF-C minus RCMF-S `+0.006`.
The first validation attempt's FP32/BF16 reader failure is superseded with full
typed evidence; it produced no environment step or token and did not change
the method package or scientific configuration.

Formal result:

| Condition | Mean | Median | Exact success | Steps | Search | Click |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| B0 | 0.580250 | 0.666667 | 39/200 | 910 | 272 | 638 |
| RCMF-C | 0.581917 | 0.666667 | 44/200 | 844 | 204 | 640 |
| RCMF-S | 0.580958 | 0.666667 | 44/200 | 859 | 203 | 656 |

RCMF-C minus B0: `+0.0016667`, CI `[-0.0297500, 0.0315854]`, gains/losses/ties
29/22/149, McNemar p `0.3017578125`.

RCMF-C minus RCMF-S: `+0.0009583`, CI `[-0.0175000, 0.0187094]`,
gains/losses/ties 7/7/186, McNemar p `1.0`.

Across all 600 rows: 2,613 steps, 679 searches, 1,934 clicks, 56 invalid
actions, 21 no-ops, 26 max-round terminations, 6,005,823 prompt tokens, 68,605
generated tokens, 8,957,014 representation tokens, and 0.85094 summed formal
H100-hours.

## Audit and tests

- Final audit logical SHA256:
  `00ef319b37d46923a4fb72749a5ae3acfd988e74d1fb230f637f473515a816a2`.
- Analysis logical SHA256:
  `94911cfa7abe47d8a36609f6b29aa001ca9ecc5ff9af194a661e86c5b372de9b`.
- Determinism logical SHA256:
  `1dbf43507671d15805a27967e84438622399dfd4146994a6f91c97aa969d6ac4`.
- Determinism: 6/6 exact semantic reruns on fixed tasks 00000/00001 under all
  conditions; not counted scientifically.
- RCMF evaluator source: 33 focused tests pass; local full 1,113 pass, 3 skip;
  Ruff passes.
- Harness: 88/88 tests pass.

## Deviations and resource state

Preserved deviations are the historical Debian infrastructure repair, the
construction-time ALFWorld overlap with two exclusive exact semantic reruns,
the typed validation dtype abort and evaluator-only repair, the no-task initial
server entrypoint failure, and the typed auto-remove shutdown recovery. None
used standard outcomes to change method behavior.

Both WebShop containers are removed, ports 58173/58174 released, and the H100
was empty when explicitly handed to the ALFWorld task. WebShop has no remaining
GPU work.

## Claim boundary and next step

VERIFIED: all identities, frozen boundaries, 600 outcomes, hashes,
determinism, split denial, and shutdown recorded above.

INFERENCE: the point estimate is compatible with noise and lacks a
memory-specific separation; classify inconclusive/null.

UNVERIFIED/NOT CLAIMED: Test-500, `[200,500)` outcomes, cross-method
superiority, raw-data redistribution rights, or generalization beyond the
fixed standard-200 population.

Next adapter step: implement a different method's lifecycle against the frozen
Harness WebShop lock, keep its run/data/server namespaces independent, freeze
before outcomes, and evaluate exactly `[0,200)`. Do not alter RCMF or use this
result for retuning.
