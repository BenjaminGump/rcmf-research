# EXP-035A One-Demo Selector x Writer/Reader Component-Swap Attribution

## Result

EXP-035A completed the frozen evaluation-only diagnostic on the eight immutable
heldout-train tasks. All 64 preregistered trajectories completed with no
infrastructure exception, optimizer step, parameter update, official-dev
access, first37 access, or test-split access.

The final diagnostic decision is **`INCONCLUSIVE`**. The interaction contrast
is positive and leave-one-task-out stable, but the native old/old cell has no
aggregate matched specificity (`OO-C = 5/8`, `OO-S = 5/8`). Therefore the
preregistered requirement that native OO specificity be retained and then lost
under a cross swap is not met. Selector and writer/reader marginal contrasts
also reverse across the other component.

This is heldout diagnostic evidence, not a causal or generalization claim.

## Frozen Contract

- Branch: `research/v5-rcmf-one-demo-component-swap`
- Starting commit: `4f5f1d3a74f196581fc570afc5f8eca75e663f4b`
- Run UUID: `rcmf_one_demo_component_swap_12a_20260831_001`
- Global/evaluation/analysis seed: `25101`
- Prompt: `full_demo_first_only`
- Initial-message SHA256: `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`
- Retained-demo SHA256: `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- AppWorld: legacy `0.1.0`
- Field: 401 model-training memories, shape `A=[960,8,256]`, `B=[8,256]`
- No runtime retrieval, top-k, FAISS, per-memory score, raw-memory prompt, or gate.

Old selector:

- Artifact: `runs/stage_c/signature_balanced_field_7c_20260818_001/selector/ensemble_scores.pt`
- Ensemble SHA256: `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Source commit: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`

Fresh selector:

- Artifact: `runs/stage_c/rcmf_one_demo_selector_retrain_11c_20260830_001/selector/ensemble_scores.pt`
- Ensemble SHA256: `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300`
- Member SHAs: `b129135b...`, `c1e02a98...`, `3d19352b...`

Old writer/reader:

- Artifact: `runs/stage_c/rcmf_joint_full_bank_9a_20260826_001/joint_training/checkpoints/epoch_02.pt`
- Checkpoint SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`

Fresh writer/reader:

- Artifact: `runs/stage_c/rcmf_one_demo_selector_retrain_11c_20260830_001/joint_training/checkpoints/epoch_01.pt`
- Checkpoint SHA256: `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764`
- The forbidden epoch-2 checkpoint was not used.

The complete package identities, parameter counts, member hashes, learned
submodule hashes, architecture dimensions, and Lambda paths are in
`research/results/exp035a_rcmf_one_demo_component_swap/manifests/component_package_manifest.json`.

## Leakage-Safe Manifest

Heldout task order:

1. `76f2c72_1`
2. `76f2c72_2`
3. `7d7fbf6_1`
4. `b7a9ee9_2`
5. `c901732_1`
6. `c901732_3`
7. `e7a10f8_1`
8. `e7a10f8_3`

Task-list SHA256: `2f6cd62aeb004156b3d7e418aa27d98586a0e83afa82c894b345f509a9d718e8`.

The field uses 401 memories only. The memory-ID list SHA256 is
`309bc61c8a7f9debc7569b05db552b03544d6522ed77d07c67ba715d526289a8`.
The historical EXP-031A shuffle manifest SHA256 is
`4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`;
its 401-memory canonical mapping SHA256 is
`9bef7ed7dcbdf720a5a8d50cf996c35e3ddf8afed236ef716a492503457d3ea4`.
The old and fresh historical heldout mappings match exactly.

## Preflight

- Old and fresh native 499-memory rebuild maximum errors were `3.81e-6` and
  `3.34e-6`, below `1e-5`.
- Direct selector score versus decomposed field score maximum error was
  `2.86e-6` for both selectors.
- All four 401-memory fields were finite, nonzero, and shape-compatible.
- Remove/restore maximum error was at most `2.38e-7`.
- Field artifact SHAs: OO `ee3049f5...`, OF `b25b6b5b...`, FO `9ec7bdd1...`,
  FF `87d41ad4...`.
- No-generation audit: 98 frozen state rows, selector-score Spearman mean
  `0.873211`, top-1 overlap `0.561224`, top-4 overlap `3.091837/4`, top-8
  overlap `6.612245/8`, old/fresh payload cosine mean `0.636804`.
- Smoke used sorted first task `76f2c72_1`; all eight conditions completed.
- OO-C, OO-S, FF-C, and FF-S deterministic repeats matched prompts, token IDs,
  emitted code, observations, step counts, and outcome exactly.
- Smoke wall time: `1633.017 s` (`0.4536 h`). Formal run wall time:
  `7557.638 s` (`2.0993 h`). Total append-ledger phase wall time: `2.5683 h`.

## Success Matrix

`1` means authoritative AppWorld task success.

| Task | OO-C | OO-S | OF-C | OF-S | FO-C | FO-S | FF-C | FF-S |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `76f2c72_1` | 1 | 0 | 0 | 0 | 0 | 1 | 0 | 0 |
| `76f2c72_2` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `7d7fbf6_1` | 0 | 1 | 1 | 1 | 0 | 0 | 1 | 0 |
| `b7a9ee9_2` | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 |
| `c901732_1` | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 1 |
| `c901732_3` | 1 | 1 | 1 | 1 | 0 | 1 | 0 | 1 |
| `e7a10f8_1` | 1 | 1 | 0 | 1 | 1 | 1 | 0 | 1 |
| `e7a10f8_3` | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 1 |
| **Total** | **5** | **5** | **4** | **6** | **2** | **5** | **3** | **4** |

## Paired Cell Statistics

| Cell | Correct / 8 | Shuffle / 8 | Delta | Bootstrap 95% CI | McNemar p | LOO range |
|---|---:|---:|---:|---:|---:|---:|
| OO | 5 | 5 | 0.000 | [-0.375, 0.375] | 1.00 | [-0.143, 0.143] |
| OF | 4 | 6 | -0.250 | [-0.625, 0.000] | 0.50 | [-0.286, -0.143] |
| FO | 2 | 5 | -0.375 | [-0.750, -0.125] | 0.25 | [-0.429, -0.286] |
| FF | 3 | 4 | -0.125 | [-0.500, 0.250] | 1.00 | [-0.286, 0.000] |

Discordant task IDs:

- OO correct-only: `76f2c72_1`; shuffle-only: `7d7fbf6_1`.
- OF correct-only: none; shuffle-only: `b7a9ee9_2`, `e7a10f8_1`.
- FO correct-only: none; shuffle-only: `76f2c72_1`, `c901732_1`, `c901732_3`.
- FF correct-only: `7d7fbf6_1`; shuffle-only: `c901732_3`, `e7a10f8_1`.

The bootstrap used 100,000 task resamples with seed `25101`; every resample
retained all eight matched conditions for the sampled task.

## Component Contrasts

| Contrast | Estimate | Bootstrap 95% CI | LOO range | Direction changes after one deletion |
|---|---:|---:|---:|---:|
| Selector with old WR: OO - FO | 0.375 | [-0.250, 1.000] | [0.143, 0.571] | no |
| Selector with fresh WR: OF - FF | -0.125 | [-0.500, 0.250] | [-0.286, 0.000] | yes |
| `M_selector` | 0.125 | [-0.3125, 0.5625] | [0.000, 0.286] | yes |
| WR with old selector: OO - OF | 0.250 | [-0.250, 0.750] | [0.143, 0.429] | no |
| WR with fresh selector: FO - FF | -0.250 | [-0.625, 0.250] | [-0.429, -0.143] | no |
| `M_WR` | 0.000 | [-0.375, 0.375] | [-0.143, 0.143] | yes |
| Interaction `I` | 0.500 | [0.125, 1.000] | [0.286, 0.571] | no |

The interaction is stable, but it is not sufficient for the co-adaptation
branch because `Delta_OO = 0`: the required native specificity anchor is
absent. The two selector contrasts and the two writer/reader contrasts also
point in opposite directions.

## Trajectory Diagnostics

| Condition | Success | Mean steps | Repeated-action events | Context ends | Docs steps | Invalid-API steps | Mean residual norm | Attention entropy |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| OO-C | 5 | 23.250 | 36 | 0 | 33 | 5 | 17.403 | 1.688 |
| OO-S | 5 | 27.250 | 50 | 0 | 65 | 4 | 19.822 | 1.677 |
| OF-C | 4 | 18.125 | 38 | 1 | 38 | 0 | 27.184 | 1.686 |
| OF-S | 6 | 24.375 | 38 | 0 | 37 | 1 | 23.682 | 1.643 |
| FO-C | 2 | 27.750 | 71 | 0 | 108 | 51 | 21.849 | 1.688 |
| FO-S | 5 | 22.625 | 21 | 0 | 77 | 3 | 19.849 | 1.681 |
| FF-C | 3 | 25.000 | 36 | 0 | 138 | 1 | 37.271 | 1.623 |
| FF-S | 4 | 27.000 | 68 | 0 | 32 | 1 | 26.317 | 1.633 |

VERIFIED:

- Fresh-selector correct-minus-shuffle documentation-call gaps are `+31`
  (FO) and `+106` (FF), versus `-32` (OO) and `+1` (OF).
- There are 66 emitted invalid-API steps, of which 51 occur in FO-C.
- Wrong-app-family steps are `0`; premature-completion events are `0`;
  infrastructure exceptions are `0`.
- `76f2c72_1` is the only task with positive specificity unique to the old
  selector, unique to the old writer/reader, and unique to OO.
- No task has more repeated-action events in correct than shuffle for all four
  cells.

INFERENCE:

- The documentation-call concentration is consistent with a fresh-selector
  documentation-attractor mechanism, but does not establish it causally.
- The trace pattern gives some selector-side mechanism support, while the
  success contrasts remain mixed and cannot isolate selector geometry.

UNVERIFIED:

- A general procedural-family failure beyond the directly observed invalid
  APIs, and a global bookkeeping failure, are not established by these eight
  trajectories.

## Decision And Scope Stop

Reached decision: **`INCONCLUSIVE`**.

No prompt transport, retraining, calibration, scale adjustment, adapter,
official dev, first37, `test_normal`, `test_challenge`, EXP-035B, or other
follow-up was started. Further work requires explicit user approval.

## Verification And Artifacts

- Focused final tests: local and Lambda `11 passed`.
- Full final tests: local `749 passed, 1 skipped`; Lambda `750 passed`.
- Formal execution SHA256: `c21c0e3f7b93e828e022c6c35fea05e43778c4f3da5630da6ece89a8b2cc69d6`.
- Analysis SHA256: `7c43a3b583871678c2607d0c4311f8386c470e2c72126713a0ecf7a0f3db3dd9`.
- Audit-index SHA256: `97996af2a1a76c9539481de90373f4ccdd270467ce5c4cae2aa63bb4d0a371cc`.
- Audit scan: 78 files, 0 hash mismatches, 0 raw JWT matches, 0 registered
  sensitive-observation leaks.
- Git-safe audit: `research/audits/rcmf_one_demo_component_swap_12a_20260831_001/`.
- Lambda raw root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_one_demo_component_swap_12a_20260831_001`.
- Execution record commit: `bbc7f776c057ed91425f0ba01141c1ab541ee2ac`.

## Implementation Deviations

- Preflight attempt 001 failed before science because a transcribed old-selector
  SHA had an extra character; the immutable hash was corrected and strict
  validation then passed.
- Diagnostic attempt 001 failed on a BF16/FP32 mismatch before completing a
  state row; the runner was aligned to the historical BF16 autocast semantics.
- A smoke invocation was rejected before attempt-ledger creation because the
  manifest source HEAD and execution HEAD were conflated; the runner gained
  separate immutable manifest/execution identities. No model/world ran.
- The first automatic decision implementation treated stable interaction alone
  as co-adaptation. Before final reporting, a tested preregistered guard required
  positive native OO specificity; the unchanged data then correctly produced
  `INCONCLUSIVE`.
- The Windows sandboxed `apply_patch` helper returned `helper_unknown_error`;
  edits used the Codex apply-patch backend and were verified by full suites.
- A read-only SSH watcher disconnected at 57/64; tmux, heartbeat, and the
  scientific process continued uninterrupted to 64/64.
