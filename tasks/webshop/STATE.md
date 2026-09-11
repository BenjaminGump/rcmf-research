# WebShop State

- Status: `TERMINAL_COMPLETE`.
- Scientific decision: `RCMF_WEBSHOP_STANDARD200_INCONCLUSIVE_NULL_RESULT`.
- Formal benchmark: AgentBench-FC `webshop-std`, exact indices `[0,200)`, 200
  tasks per condition.
- Formal execution: 600/600 trajectories complete with zero typed errors under
  `B0`, `RCMF-C`, and `RCMF-S`.
- Excluded range: `[200,500)` was not executed and remains out of scope.
- Harness branch/source/records: `dataset/webshop-e2e-v3` /
  `9e1f3963d42de871c23a1d0d87c1299e8387a420` /
  `2088d0d77b9a32a76a8d1823cb199dd13d0207af`.
- Harness runtime archive: `archive/webshop-agentbench-fc-runtime-v1-b5bd698`.
- RCMF construction source/archive: `084566c56a4fb4f5457cd73719ed351bf8f3a594` /
  `archive/rcmf-webshop-construction-v1-084566c`.
- RCMF method source/archive: `be711705b5824d6b61ca05a9ea0b19be8882ec8e` /
  `archive/rcmf-webshop-method-v1-be71170`.
- Evaluator source/archive: `77508bd8aba67576038ecdc513c0e91fd825a4a9` /
  `archive/rcmf-webshop-evaluator-v2-77508bd`.
- Method package SHA256:
  `b54ef1c0119a95983ec19d068543ded14036dd5704ee9a200930d361deb6f4df`.
- Construction: 302 replay-validated exact-success `AGENT_GENERATED` train
  trajectories, 1,205 transitions, corpus SHA256
  `b5f1252f9823dca36ef3ee2fd3f0c5278c5ca527016abee182844bd094ff958e`.
- Frozen field: `A[960,8,256]`, `B[8,256]`; no raw-memory prompt, runtime
  memory scan, per-memory scoring, nearest-neighbor retrieval, or top-k.
- Qwen: frozen `Qwen/Qwen3-8B` snapshot
  `b968826d9c46dd6066d109eabc6255188de91218`.
- Validation: fixed `[500,550)` three-condition rerun passed the directional
  mechanism gate before standard outcomes were opened.
- Formal mean raw reward: B0 `0.580250`, RCMF-C `0.581917`, RCMF-S
  `0.580958`.
- Formal exact-1.0 success: B0 `39/200`, RCMF-C `44/200`, RCMF-S `44/200`.
- Paired RCMF-C minus B0: `+0.0016667`, bootstrap 95% CI
  `[-0.0297500, 0.0315854]`.
- Paired RCMF-C minus RCMF-S: `+0.0009583`, bootstrap 95% CI
  `[-0.0175000, 0.0187094]`.
- Interpretation: no reliable or memory-specific benefit was demonstrated.
- Determinism: 6/6 fixed post-evaluation fresh-session semantic replays exact;
  excluded from scientific counts.
- Final audit: `PASS`, logical SHA256
  `00ef319b37d46923a4fb72749a5ae3acfd988e74d1fb230f637f473515a816a2`.
- Lambda run root:
  `/lambda/nfs/rcmf-persist/project/runs/webshop/webshop_rcmf_v1_d7853aba-7e27-4bb9-9f79-04447416ca3e`.
- Servers are removed, ports 58173/58174 are released, and the H100 was empty
  when explicitly handed back to ALFWorld.
- Authoritative report:
  `research/results/RCMF_WEBSHOP_AGENTBENCH_FC_STANDARD200_FINAL.md`.
- Next step: a different memory method may implement its own adapter against
  the frozen benchmark identities; do not retune RCMF from standard outcomes or
  run `[200,500)` without a new explicit milestone.
- Last verified UTC: `2026-09-11T02:31:57Z`.
