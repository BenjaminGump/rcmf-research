# WebShop Adaptation Brief

## Result

RCMF is fully adapted and evaluated on the frozen AgentBench-FC WebShop
standard-200 benchmark. All 600 formal trajectories completed. RCMF-C mean raw
reward is `0.581917`, versus B0 `0.580250` and matched shuffle `0.580958`.
The paired differences are small with confidence intervals crossing zero, and
RCMF-C/RCMF-S both have `44/200` exact successes. Classification:
`RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT`.

This is not a Test-500 result. `[200,500)` was not executed.

## Frozen boundary

- Harness source/records: `9e1f3963d42de871c23a1d0d87c1299e8387a420` /
  `2088d0d77b9a32a76a8d1823cb199dd13d0207af`.
- Method source: `be711705b5824d6b61ca05a9ea0b19be8882ec8e`.
- Evaluator source: `77508bd8aba67576038ecdc513c0e91fd825a4a9`.
- Method package:
  `b54ef1c0119a95983ec19d068543ded14036dd5704ee9a200930d361deb6f4df`.
- Construction corpus:
  `b5f1252f9823dca36ef3ee2fd3f0c5278c5ca527016abee182844bd094ff958e`.
- Final evaluation lock logical SHA256:
  `8cbd88c43bb0dd9b01d81c972d9e57d74d8cae9679de0a342d60599cc24b4458`.
- Final analysis logical SHA256:
  `94911cfa7abe47d8a36609f6b29aa001ca9ecc5ff9af194a661e86c5b372de9b`.
- Final audit logical SHA256:
  `00ef319b37d46923a4fb72749a5ae3acfd988e74d1fb230f637f473515a816a2`.

## Method contract

The raw complete transition ledger is authoritative. Every admitted memory is
independently compiled and reversibly addable/removable. The whole-bank field
has fixed shapes `A[960,8,256]` and `B[8,256]`. Qwen, selector, writer, and
reader are frozen. Deployment uses no raw memory text, per-memory scoring,
nearest-neighbor retrieval, bank scan, or top-k.

## Next method adapter

Start only from the frozen Harness WebShop benchmark interface. Bind a new
method's lifecycle to the exact benchmark lock and keep its construction data,
run root, caches, server namespace, and condition freeze independent. Use the
same AgentBench prompt/tools, task IDs `[0,200)`, continuous raw reward, exact
1.0 full-success rule, and paired analysis. Freeze before standard outcomes.
Do not modify RCMF or use this result to tune a new method.

See `research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.md`.
