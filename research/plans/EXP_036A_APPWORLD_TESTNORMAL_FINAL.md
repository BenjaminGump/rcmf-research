# EXP-036A AppWorld Test-Normal Final Evaluation

## Purpose

EXP-036A is a frozen evaluation-only study of the final one-demo RCMF systems
on the complete legacy AppWorld 0.1.0 `test_normal` split. It also measures
deployment efficiency, fixed-size field scaling, and numerical reversibility.
It does not train or select a model.

## Frozen Scientific Conditions

The ordered 168-task manifest is crossed with exactly five conditions:

- `B0`: one-demo bare Qwen with no field or reader hooks.
- `BEST-C`: old selector, EXP-031A epoch-2 writer/reader, correct 499-memory field.
- `BEST-S`: the same BEST package with its frozen key-payload shuffle.
- `FULL1D-C`: fresh selector, EXP-034B epoch-1 writer/reader, correct field.
- `FULL1D-S`: the same FULL1D package with its matched frozen shuffle.

`BEST` is the primary method and `FULL1D` is a secondary ablation. The shared
bare condition is run once. Every task-condition starts in a fresh world under
`full_demo_first_only`, seed `25101`, greedy generation, and the unchanged
AppWorld evaluator. No test outcome can alter the manifest or package identity.

## Frozen Identities

- Starting commit: `4aa0fd89b2b63d5a9bcbf1e6b18395e0a14e847b`.
- Working branch: `research/v5-rcmf-appworld-testnormal-final`.
- Run UUID: `rcmf_appworld_testnormal_final_13a_20260831_001` unless an
  immutable preexisting run requires a unique suffix.
- BEST selector SHA256: `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`.
- BEST writer/reader SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`.
- BEST field SHA256: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`.
- FULL1D selector SHA256: `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300`.
- FULL1D writer/reader SHA256: `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764`.
- FULL1D field SHA256: `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169`.
- Shared shuffle-manifest SHA256: `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`.
- One-demo initial-message SHA256: `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`.
- Retained-demo SHA256: `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`.

## Staged Execution

1. Verify all local, GitHub, Lambda, artifact, environment, split, prompt, and
   leakage identities; freeze package, task, condition, and audit manifests.
2. Implement one shared five-condition runtime by extending verified one-demo
   and component-package paths without changing their default behavior.
3. Pass focused and complete local/Lambda tests, then run a two-task five-cell
   smoke and deterministic repeat on one task.
4. Record measured expected and conservative runtime for all 840 trajectories,
   Phase B efficiency work, and Phase C reversibility. Launch only if the
   conservative total is at most 42 hours.
5. Complete the full manifest without success-based stopping, preserving
   atomic outputs, heartbeat, append-only attempts, exact resume identity, and
   Git-safe reconstructible audits.
6. After formal rows are sealed, benchmark compilation, read scaling, TTFT,
   active serving state, and all-499-memory remove/restore numerical behavior.
7. Analyze paired task effects with 100,000 task bootstrap replicates, exact
   McNemar tests, leave-one-task-out sensitivity, and family concentration.

## Primary Comparisons

- `BEST-C - B0`: absolute primary-method effect.
- `BEST-C - BEST-S`: primary memory-specific effect.
- `FULL1D-C - B0`: secondary absolute effect.
- `FULL1D-C - FULL1D-S`: secondary memory-specific effect.
- `BEST-C - FULL1D-C`: frozen-system comparison.

All 168 per-task outcomes and paired transitions are reported. No arbitrary
task-count threshold or p-value alone defines success or failure.

## Complexity And Storage Boundary

Field read output shape and runtime complexity must remain independent of bank
size. Deployment compilation is measured separately from read time. Active
serving tensors and fixed model parameters are reported separately from the
authoritative raw-memory ledger, per-record provenance, and reversible
contribution archive; total archival storage is not claimed constant.

## Stop Contract

The experiment ends after the five-condition full evaluation, required audit,
efficiency/scaling benchmark, numerical reversibility analysis, Git-safe
records, and structured handoff. Q90, scales, gates, retrieval, prompt changes,
training, additional benchmarks, and follow-up studies require new approval.
