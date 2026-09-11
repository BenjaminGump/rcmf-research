# RCMF × AgentBench-FC WebShop Standard-200 Final Result

Status: `TERMINAL_COMPLETE`

Scientific decision: `RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT`

Last verified: `2026-09-11T02:31:57Z`

## Outcome

The frozen RCMF WebShop method completed all 600 required formal trajectories:
200 `B0`, 200 `RCMF-C`, and 200 matched-shuffle `RCMF-S`. The evaluated
population is exactly AgentBench-FC `webshop-std` indices `[0,200)`. No task in
the prohibited `[200,500)` extension was executed.

| Condition | Mean raw reward | Bootstrap mean 95% CI | Median | Exact 1.0 |
| --- | ---: | ---: | ---: | ---: |
| B0 | 0.580250 | [0.538000, 0.621500] | 0.666667 | 39/200 (19.5%) |
| RCMF-C | 0.581917 | [0.538000, 0.626002] | 0.666667 | 44/200 (22.0%) |
| RCMF-S | 0.580958 | [0.536749, 0.625626] | 0.666667 | 44/200 (22.0%) |

`RCMF-C - B0` has mean paired reward difference `+0.0016667`, median `0`,
bootstrap 95% CI `[-0.0297500, 0.0315854]`, 29 gains, 22 losses, and 149 ties.
Binary exact-success discordance is 10 correct-only versus 5 bare-only tasks;
the exact two-sided McNemar p-value is `0.3017578125`.

`RCMF-C - RCMF-S` has mean paired reward difference `+0.0009583`, median `0`,
bootstrap 95% CI `[-0.0175000, 0.0187094]`, 7 gains, 7 losses, and 186 ties.
Both conditions have 44 exact successes; binary discordance is 2 versus 2 and
the exact two-sided McNemar p-value is `1.0`.

The point estimates are slightly positive, but both paired confidence
intervals include zero and the correct field does not separate from its
matched shuffled control. The result therefore does not demonstrate a reliable
or memory-specific RCMF benefit on this benchmark. This is a completed
scientific result, not a benchmark-readiness smoke and not a Test-500 claim.

The complete reward distributions and gain/loss task IDs are in
`research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_ANALYSIS.json`.

## Frozen identities

- Harness branch/source/records: `dataset/webshop-e2e-v3` /
  `9e1f3963d42de871c23a1d0d87c1299e8387a420` /
  `2088d0d77b9a32a76a8d1823cb199dd13d0207af`.
- Harness runtime archive: `archive/webshop-agentbench-fc-runtime-v1-b5bd698`.
- Construction source/archive: `084566c56a4fb4f5457cd73719ed351bf8f3a594` /
  `archive/rcmf-webshop-construction-v1-084566c`.
- Frozen method source/archive: `be711705b5824d6b61ca05a9ea0b19be8882ec8e` /
  `archive/rcmf-webshop-method-v1-be71170`.
- Evaluator-only repair source/archive: `77508bd8aba67576038ecdc513c0e91fd825a4a9` /
  `archive/rcmf-webshop-evaluator-v2-77508bd`.
- Frozen method package SHA256:
  `b54ef1c0119a95983ec19d068543ded14036dd5704ee9a200930d361deb6f4df`.
- Standard evaluation lock: file SHA256
  `0c2a5fafd5571db0f6cea5cc972724f523140359738dea3e00de11a5eec4cc18`,
  logical SHA256
  `8cbd88c43bb0dd9b01d81c972d9e57d74d8cae9679de0a342d60599cc24b4458`.

The method, ordered standard task IDs, B0/RCMF-C/RCMF-S definitions, Qwen
snapshot, generation settings, runtime, prompt, task catalog, and validation
decision were frozen together before any standard-200 outcome was inspected.

## Runtime, data, prompt, and evaluator

All authoritative execution ran on Lambda Cloud Ubuntu x86_64 with an H100
80GB. No Windows Docker execution contributed evidence.

- AgentBench: `d1e4a10db08c87075c78972e48ecc182be03e2d5`, Apache-2.0.
- Princeton WebShop: `64fa2a5c15c7daa698b9ac93f5bb5437b634c9bd`, MIT.
- Immutable data image:
  `longinyu/agentbench-webshop@sha256:e8bd3b120fe57653ecd902a6629d11a7d68f738e15f56a7e0aefdc1139f38b09`.
- Derived worker image:
  `sha256:af342a9b210bc8bde14eccb3c858beefc79de7203b63d3286beb327b099d0cb9`.
- Products: 5,479,720,229 bytes, 1,181,436 rows, SHA256
  `2ef591d65df3af89e972ab72468eb82cbf124d876552d9f3678667edd620a6c8`.
- Instructions: 186,295,270 bytes, 1,181,436 entries, SHA256
  `1d36af476bdb8f82a5da62bd8acdabe54cd8de2fa84010d37da5c4890feb447e`.
- Human-instruction metadata: 5,137,548 bytes, 10,136 entries, SHA256
  `cf78667548a71786e1d9049c24b802e48e1084ad4bb021cae56ce1f6d96954a3`.
- Lucene index: 19 files, 3,601,981,280 bytes, logical manifest SHA256
  `4357bdc2b93dd12036600f375aa15ec1fce65f20b7ffef797c65d8b35cc752ae`.
- Prompt profile: `agentbench_fc_webshop_v1`, programmatically extracted from
  the pinned AgentBench source with `content_changed=false`; system SHA256
  `4d2c361799681a200b69c21229a3ea07a79b7c1e74fd0cb308defd0c85d3ce11`,
  tools SHA256
  `be6b939ad34d95f55759f679df37f167bda2585d8eea128bea6f2e6a0e7af7c3`.
- Interface: text observations, required function calls
  `search_action(keywords)` and `click_action(value)`, maximum 20 rounds.
- Reward: pinned Princeton continuous raw reward through the AgentBench-FC
  wrapper; full success is exact raw reward `1.0` only.

AgentBench and Princeton licenses cover repository code. No separate right to
redistribute Amazon-derived product data is claimed, and no raw corpus or index
is committed.

## Split and trajectory lineage

The frozen task manifest has 11,700 rows: standard `[0,200)`, validation
`[500,1500)`, and train `[1500,12000)`. The method used validation `[500,550)`
and processed construction `[1500,3000)`. The out-of-scope `[200,500)` range
was denied by the server and never executed.

The upstream instruction corpus contains 41 exact text hashes that cross split
names. They were not filtered using outcomes. Stable task IDs, evaluator-goal
hashes, index-based lineage keys, and split populations remain distinct with
zero task-ID or lineage-key overlap. This duplicate-text fact is a documented
benchmark property, not silently removed leakage.

The admitted construction class is `AGENT_GENERATED`: 302 complete,
exact-reward-1.0, replay-validated train trajectories produced 1,205 complete
transition memories (321 search, 884 click). Corpus SHA256 is
`b5f1252f9823dca36ef3ee2fd3f0c5278c5ca527016abee182844bd094ff958e`.
No official-human, IL/finalized, evaluation-derived, or oracle trajectory was
mixed into this ledger.

## RCMF mechanism and training

Frozen Qwen3-8B snapshot
`b968826d9c46dd6066d109eabc6255188de91218` generated structured
representations. The authoritative raw transition ledger remains primary.
Selector training used three seeds and produced heldout recall-at-1 values
`0.816`, `0.816`, and `0.824`. Writer/reader training completed the fixed two
epochs and all 768 units; the terminal checkpoint SHA256 is
`55ae2cd69d072f5e065fb3da27a40773848b568fb54e8cbd7de61c3ed5e85636`.

The deployed state is fixed at `A[960,8,256]` and `B[8,256]` for 1,205
memories. Add/remove/restore maximum absolute error is
`1.1920928955078125e-07`. Qwen, selector, writer, and reader are frozen. The
runtime performs no top-k, nearest-neighbor search, per-memory scoring, bank
scan, selected-memory access, or raw-memory prompt insertion.

## Validation, deviations, and determinism

The first validation attempt is preserved as a typed engineering abort:
RCMF-C produced 50/50 `RuntimeError` rows before any environment step or token
generation because the frozen FP32 reader was not cast to Qwen's BF16 inference
dtype. No reward signal was used to repair it. Source `77508bd...` fixes only
that evaluator dtype boundary and leaves the frozen method package, tasks,
conditions, and generation settings unchanged. All three validation conditions
were then rerun from the start. RCMF-C exceeded B0 by `0.0398667` and RCMF-S by
`0.006`, satisfying the preregistered directional `PROCEED` rule before
standard outcomes were opened.

An accidental concurrent ALFWorld process overlapped part of construction.
Fixed preselected construction tasks 2000 and 2200 were rerun exclusively and
matched their original semantic sequences exactly. The overlap is therefore
recorded as a wall-time/resource-isolation deviation, not waived evidence.

After formal evaluation, fixed sorted task IDs 00000 and 00001 were replayed
under all three conditions in fresh sessions. All 6/6 action, observation,
reward, token-ID, query, and slot-hash semantic sequences matched exactly. These
reruns were not added to the scientific population.

A first standard server launch used the image's default AgentRL entrypoint and
exited with code 2 before any reset or task. It was removed and replaced by an
explicit Python entrypoint. Final shutdown hit a benign auto-remove/double-rm
case for the train/validation container; the typed recovery removed the
remaining standard container. Ports 58173/58174 are released and the H100 was
empty at handoff.

## Operational totals and validation

Across the 600 formal trajectories: 2,613 environment steps, 679 searches,
1,934 clicks, 56 invalid actions, 21 environment no-ops, 26 max-round
terminations, 6,005,823 prompt tokens, 68,605 generated tokens, and 8,957,014
representation tokens. Summed condition wall/H100 time is 3,063.37 seconds /
0.85094 H100-hours. Peak allocated GPU memory was 33,680,460,288 bytes.

The evaluator source passed 33 focused WebShop/reader tests and the local full
suite passed 1,113 tests with 3 skips; Ruff passed. The Harness suite passed
88/88. Final content audit recomputed every one of the 600 task hashes and all
summary/lock/analysis links; logical audit SHA256 is
`00ef319b37d46923a4fb72749a5ae3acfd988e74d1fb230f637f473515a816a2`.

## Claim boundary

VERIFIED: runtime/data/task/prompt identities; exact split populations; frozen
method and controls; 302 admitted training trajectories; 600/600 complete
formal trajectories; reward summaries; paired analyses; determinism; no
standard pre-freeze access; no `[200,500)` execution; no oracle; clean server
shutdown.

INFERENCE: the tiny positive correct-field point estimate is compatible with
noise because its paired interval crosses zero and the shuffle control matches
exact success. The appropriate scientific reading is inconclusive/null, not a
positive RCMF result.

UNVERIFIED/NOT CLAIMED: full original WebShop Test-500 performance; raw data
redistribution rights; generalization beyond this one fixed 200-task population;
cross-method superiority; any result for tasks `[200,500)`.

## Next adapter step

For the next memory method, implement only its method-specific lifecycle
adapter against the frozen Harness WebShop benchmark lock, reuse the exact
task/prompt/runtime/evaluator identities, freeze its conditions before opening
standard outcomes, and evaluate exactly `[0,200)`. Do not change RCMF, retune
from this result, or automatically run `[200,500)`.

No oracle trajectory was generated. No final Test-500 result was produced.
