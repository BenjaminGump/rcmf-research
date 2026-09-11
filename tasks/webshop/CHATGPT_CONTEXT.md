# WebShop ChatGPT Context

This is a bootstrap cache, not primary evidence. Verify GitHub and the sealed
artifacts before continuing.

## Current state

WebShop end-to-end adaptation is terminal-complete. The fixed AgentBench-FC
`webshop-std` population `[0,200)` ran under all three frozen conditions for
600/600 complete trajectories. Tasks `[200,500)` were not run. The final
classification is `RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT` because
RCMF-C improves mean reward over B0 by only `0.0016667` with a paired interval
crossing zero and has the same `44/200` exact-success count as matched shuffle.

Primary report:
`research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.md`.

Machine-readable evidence:

- `research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.json`
- `research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_ANALYSIS.json`
- `research/audits/RCMF_WEBSHOP_STANDARD200_FINAL_AUDIT.json`
- `research/audits/RCMF_WEBSHOP_SERVER_SHUTDOWN.json`

## Exact identities

- Harness: `dataset/webshop-e2e-v3`, source `9e1f3963...`, records
  `2088d0d7...`.
- RCMF method: source `be711705...`, archive
  `archive/rcmf-webshop-method-v1-be71170`.
- Evaluator: source `77508bd8...`, archive
  `archive/rcmf-webshop-evaluator-v2-77508bd`.
- Method package: `b54ef1c0119a95983ec19d068543ded14036dd5704ee9a200930d361deb6f4df`.
- AgentBench: `d1e4a10d...`; Princeton WebShop: `64fa2a5c...`.
- Qwen3-8B snapshot: `b968826d...`.
- Lambda run root:
  `/lambda/nfs/rcmf-persist/project/runs/webshop/webshop_rcmf_v1_d7853aba-7e27-4bb9-9f79-04447416ca3e`.

## Continuation boundary

Do not change or rerun RCMF based on standard outcomes. Do not extend to tasks
`[200,500)` or start BFCL. For another method, reuse the frozen Harness
benchmark lock, prompt, tasks, runtime, continuous reward evaluator, and exact
1.0 success definition; construct/freeze that method independently before
opening standard outcomes.

No server remains running and WebShop owns no GPU process.

Last verified UTC: `2026-09-11T02:31:57Z`.
