# EXP-035A Structured Handoff

## Identity

- Experiment: `EXP-035A One-Demo Selector x Writer/Reader Component-Swap Attribution`
- Branch: `research/v5-rcmf-one-demo-component-swap`
- Starting SHA: `4f5f1d3a74f196581fc570afc5f8eca75e663f4b`
- Execution record commit: `bbc7f776c057ed91425f0ba01141c1ab541ee2ac`
- Run UUID: `rcmf_one_demo_component_swap_12a_20260831_001`
- Seed: `25101`
- Decision: `INCONCLUSIVE`

## What Ran

- Four frozen package cells: OO, OF, FO, FF.
- Two matched bindings per cell: correct and common 401-memory key-payload shuffle.
- Eight immutable heldout-train tasks.
- Exactly 64 complete formal trajectories, sequential/counterbalanced, one fresh world each.
- No optimizer step, update, training, calibration, scale, adapter, gate, retrieval, raw-memory prompt, dev, first37, or test task.

## Immutable Packages

- Old selector: `runs/stage_c/signature_balanced_field_7c_20260818_001/selector/ensemble_scores.pt`, SHA `c7ca61bb...956611f`.
- Fresh selector: `runs/stage_c/rcmf_one_demo_selector_retrain_11c_20260830_001/selector/ensemble_scores.pt`, SHA `c6e4e2dd...d6300`.
- Old WR: `runs/stage_c/rcmf_joint_full_bank_9a_20260826_001/joint_training/checkpoints/epoch_02.pt`, SHA `d11e9d8e...41a5f1`.
- Fresh WR: `runs/stage_c/rcmf_one_demo_selector_retrain_11c_20260830_001/joint_training/checkpoints/epoch_01.pt`, SHA `357491a6...d764`.
- Full exact identities: `research/results/exp035a_rcmf_one_demo_component_swap/manifests/component_package_manifest.json`.

## Frozen Tasks And Field

- Tasks: `76f2c72_1`, `76f2c72_2`, `7d7fbf6_1`, `b7a9ee9_2`, `c901732_1`, `c901732_3`, `e7a10f8_1`, `e7a10f8_3`.
- Task-list SHA: `2f6cd62a...d718e8`.
- 401-memory IDs SHA: `309bc61c...289a8`.
- Common shuffle manifest SHA: `4e5a4d85...2170`; 401 mapping SHA `9bef7ed7...d3ea4`.
- Fields: OO `ee3049f5...346d`, OF `b25b6b5b...bb1`, FO `9ec7bdd1...b7a`, FF `87d41ad4...fe5`.

## Results

| Cell | Correct | Shuffle | Delta | 95% bootstrap | McNemar p | LOO |
|---|---:|---:|---:|---:|---:|---:|
| OO | 5/8 | 5/8 | 0.000 | [-0.375, 0.375] | 1.00 | [-0.143, 0.143] |
| OF | 4/8 | 6/8 | -0.250 | [-0.625, 0.000] | 0.50 | [-0.286, -0.143] |
| FO | 2/8 | 5/8 | -0.375 | [-0.750, -0.125] | 0.25 | [-0.429, -0.286] |
| FF | 3/8 | 4/8 | -0.125 | [-0.500, 0.250] | 1.00 | [-0.286, 0.000] |

- `M_selector = 0.125`, CI `[-0.3125, 0.5625]`, LOO `[0, 0.286]`, direction-sensitive.
- `M_WR = 0.000`, CI `[-0.375, 0.375]`, LOO `[-0.143, 0.143]`, direction-sensitive.
- Interaction `I = 0.500`, CI `[0.125, 1.000]`, LOO `[0.286, 0.571]`, direction-stable.
- The interaction does not authorize co-adaptation because native `Delta_OO = 0`.

Per-task 8-condition outcomes are in `research/results/exp035a_rcmf_one_demo_component_swap/per_task.jsonl` and the full report.

## Mechanisms

VERIFIED:

- Documentation-call C-S gaps: OO `-32`, OF `+1`, FO `+31`, FF `+106`.
- Invalid API steps: 66 total, including 51 in FO-C.
- Wrong-app-family steps: 0. Premature completion: 0. Infrastructure exceptions: 0.
- Only `76f2c72_1` has positive specificity unique to old selector, old WR, and OO.

INFERENCE:

- Fresh-selector correct conditions exhibit a documentation-attractor-like trace pattern.
- That mechanism pattern does not resolve the mixed task-success marginals.

UNVERIFIED:

- Fine-grained procedural-family and global-bookkeeping causal explanations.

## Attempts And Runtime

- Append-only ledger: 7 unique IDs / 14 events; 5 completed, 2 failed, 0 open.
- Failed: `exp035a-preflight-001` (SHA typo, before science), `exp035a-diagnostics-001` (BF16 mismatch, before complete row).
- Completed: `exp035a-preflight-002`, `exp035a-diagnostics-002`, `exp035a-smoke-002`, `exp035a-run-001`, `exp035a-finalize-001`.
- One smoke invocation was rejected before ledger start due manifest/execution HEAD identity mismatch; no model/world ran.
- Smoke: `1633.017 s`. Formal: `7557.638 s`. Ledger phase wall total: `2.5683 h`.
- Determinism passed for OO-C, OO-S, FF-C, FF-S; one evaluation seed only.

## Verification

- Focused: local/Lambda 11 passed.
- Full: local 749 passed + 1 skipped; Lambda 750 passed.
- Execution SHA: `c21c0e3f...69d6`.
- Analysis SHA: `7c43a3b5...3dd9`.
- Audit index SHA: `97996af2...71cc`.
- Audit: 78 indexed files, zero hash mismatch, zero raw JWT, zero registered sensitive-observation leak.

## Artifacts

- Full report: `research/results/EXP_035A_RCMF_ONE_DEMO_COMPONENT_SWAP.md`
- Machine results: `research/results/exp035a_rcmf_one_demo_component_swap/`
- Git-safe traces: `research/audits/rcmf_one_demo_component_swap_12a_20260831_001/`
- Lambda raw/tensors: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_one_demo_component_swap_12a_20260831_001`

## Stop

No follow-up is authorized. Do not start prompt transport, EXP-035B, selector or WR retraining, calibration, scale tuning, an adapter, official dev, first37, or any test split. Await user and ChatGPT review.
