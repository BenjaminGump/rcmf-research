# RCMF V3 - Component-Validated Trajectory-Memory Field (Pre-Transition)

## Identity

- Official version name:
  `RCMF V3 - Component-Validated Trajectory-Memory Field (Pre-Transition)`.
- Source-state commit:
  `97ca723ad66597d2afcbbce1eb5466eb34c009f6`.
- Authoritative annotated tag:
  `rcmf-v3-component-validated-pre-transition`.
- Archive branch: `archive/rcmf-v3-component-validated`.
- Freeze date: `2026-08-14`.
- Development successor branch:
  `research/v4-decision-transition-memory`.
- Successor status: V4 candidate only; no V4 tag or validation claim.

The annotated tag identifies the documentation-only freeze commit. The
source-state commit above identifies the exact implementation and research
record state before this manifest was added, avoiding a self-referential
manifest SHA.

## Memory Unit

The primary V3 memory unit is one complete successful AppWorld trajectory,
stored as one `MemoryRecord`. It contains the task goal, complete ordered
decision history, actions, and observations. One record is compiled as one
logical write to the memory field. Parent records remain human-readable ledger
objects with task, episode, replay, and lineage identity.

V3 does not split a trajectory into independently addressable decision
transitions.

## Architecture Summary

- Frozen Qwen3-8B supplies canonical hidden representations and all
  teacher-forced action scoring.
- Canonical AppWorld full-demo rendering preserves the three demonstrations,
  task query, exact trajectory history, and target-only loss masking.
- Strict leakage rules exclude same task, episode, replay, and lineage.
- Raw-text memory teacher utility is
  `L0 - L(raw-memory teacher)` with no prompt, target, or memory truncation.
- The successful Stage-B selector uses a signed continuous residual field:
  `score(s,i) = mu_i + temperature * dot(q_s,k_i)/sqrt(rank)`.
- Reversible field algebra stores additive per-record `DeltaV` and `DeltaG`
  writes and supports exact add, remove, and replace operations.
- V3's behavioral channel uses K=4 additive perturbations at `last_user_k`.
- The validated behavioral objective is sequence-level teacher utility with a
  small sparse-KL auxiliary.
- A no-bias rank-128 linear decoder is the strongest supported shared decoder
  family. Its oracle capacity evidence is strong, although the EXP-016C
  held-out inversion plateau gate was not formally completed.
- The attempted content path maps one whole trajectory to one static program
  vector. That path was not validated.

## Frozen Configurations

The following checked-in configurations define the principal V3 research
surface and are frozen by the authoritative tag:

- `configs/model/qwen3_8b.yaml`;
- `configs/benchmark/appworld_rcmf_full_prompt.yaml`;
- `configs/benchmark/appworld_rcmf_full_prompt_last_user_k.yaml`;
- `configs/benchmark/stage_b_4c_signed_field.yaml`;
- `configs/benchmark/stage_b_5c_selector_repair.yaml`;
- `configs/benchmark/stage_c1_signed_program.yaml`;
- `configs/benchmark/stage_c_pair_grounding_5d.yaml`;
- `configs/benchmark/stage_c_oracle_capacity_5e.yaml`;
- `configs/benchmark/stage_c_oracle_convergence_5fa.yaml`;
- `configs/benchmark/stage_c_oracle_convergence_5fb.yaml`;
- `configs/benchmark/stage_c_oracle_decoder_5fc.yaml`.

Later V4-candidate work may add new files but must not rewrite the V3 tag or
archive branch.

## Verified Components

### Data and teacher

- The filtered official successful-trajectory dataset contains 638 decision
  states and 46 trajectory records after the disclosed exclusion of malformed
  episode `2a163ab_3`.
- The complete raw-text teacher preflight contains 28,710 exact legal pairs:
  27,054 scoreable and 1,656 over context. Over-context rows remain missing;
  none are silently truncated or assigned neutral utility.
- The teacher cache validates target hashes, memory hashes, model identity,
  renderer identity, leakage exclusions, finite losses, and deterministic
  reproducibility.

### Selector and field

- Hard-top-k addressing has a verified disjoint-support zero-gradient dead
  zone.
- Dense nonnegative softmax addressing collapses toward the global prior.
- The rank-128 signed two-tower residual selector passes the Stage-4C
  continuity and task-grouped cross-validation gates and degrades under state
  shuffling.
- Signed `DeltaV/DeltaG` algebra passes exact field-read, add/remove, replace,
  arbitrary-order, and restoration tests.

### Behavioral channel

- Corrected direct DeltaE optimization establishes that K=4 `last_user_k`
  input-embedding injection can reproduce sequence utility under ratio 1.0.
- Stage-5F-B u128 achieves utility Spearman `0.979465`, sign agreement
  `0.992806`, and sequence Huber `0.034512`.
- EXP-016C global uncentered rank-128 projection passes on both u112 and u128;
  rank-192 reconstruction is exact.
- EXP-016C frozen-linear held-out inversion is positive in all three folds and
  pools to Spearman/Huber `0.988537/0.027538` on u112 and
  `0.994685/0.015615` on u128.

These are component and oracle validations. They do not prove a deployable
content-derived memory program.

## Failed Or Unverified Components

- A content-derived static program for one whole trajectory did not establish
  memory-specific causality and was matched or exceeded by free-ID controls.
- Corrected Stage-C1 leave-one-out effects were nonzero but small; selector-top
  removal affected behavior more than raw-teacher-best removal.
- Selector repair improved teacher-best alignment but missed its predefined
  top-utility gate.
- Pair-level trajectory-program grounding failed to show that a content
  compiler generalizes to unseen memory records.
- Stage-5E's original direct-capacity failure was underoptimized at two updates
  per pair; its sparse-objective mismatch finding remains valid.
- EXP-016C did not satisfy its formal shared-decoder gate because no inversion
  path reached the corrected plateau in all three folds, despite strong
  numerical capacity evidence.
- The trajectory-level content-derived static program was not validated.
- The complete trajectory-memory field was not retrained successfully end to
  end after these component diagnostics.
- No new complete AppWorld agent evaluation was run after the V2 partial
  first-37 result.

## Historical AppWorld Results

- V1: no meaningful complete AppWorld score established.
- V2a low-disturbance step-100 first-10: `3/10`.
- V2a low-disturbance final first-10: `2/10`.
- V2b semantic-retrieval first-10: `4/10`.
- V2b semantic-retrieval partial first-37: `7/37`.
- Locked bare-Qwen first-37: `10/37`.
- Corrected bare-Qwen full run: `53/168 = 31.5476%`.

The fixed first-10 and first-37 results are diagnostic slices. V3 adds no new
full generated AppWorld result and must not inherit an end-to-end success claim
from V2.

## Major Findings By Component

### Teacher

Frozen local Qwen raw-text scoring provides nondegenerate positive and
negative utility labels under strict leakage and no-truncation rules. The
complete cache is the authoritative V3 supervision source.

### Selector

Signed continuous state-memory interaction is necessary. It succeeds where
hard sparse and dense nonnegative address parameterizations fail, but exact
raw-teacher-best alignment remains imperfect.

### Program

Pair-specific behavioral controls are learnable, but one static
content-derived vector per whole trajectory does not preserve the observed
state-specific raw-memory effect.

### Injector

K=4 `last_user_k` input-embedding perturbations have adequate direct-oracle
sequence-utility capacity at ratio 1.0. The injection site is not disproven.

### Decoder

The direct DeltaE manifold has high tensor effective rank, yet a global
rank-128 projection preserves behavior. A shared no-bias linear decoder with
free held-out pair latents has strong capacity evidence. Its formal
convergence gate remains open.

## Major Milestones And Artifacts

- `raw_text_full_cache_20260805_001`:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- `signed_field_4c_20260806_002`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002`.
- `signed_program_c1_20260806_002`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002`.
- `stage_c1_5b_diagnostics_20260807_001`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001`.
- `selector_repair_5c_20260807_001`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001`.
- `pair_grounding_5d_20260807_001`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001`.
- `oracle_capacity_5e_20260808_001`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001`.
- `oracle_convergence_5fa_20260808_001`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001`.
- `oracle_convergence_5fb_20260809_001`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001`.
- `oracle_decoder_5fc_20260810_003`:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_decoder_5fc_20260810_003`.

Git-safe reports are under `research/results/`, and structured milestone
handoffs are under `research/handoffs/`.

## Reason For Decision-Transition Memory

V3 establishes that the frozen model, signed selector, reversible field,
K=4 injection site, and a rank-128 decoder each carry useful signal. The
remaining failure is concentrated in the assumption that an entire successful
trajectory can be compiled into one state-independent static program.

A complete action-observation transition is the next scientifically motivated
atomic unit because it preserves a precise pre-action state, action, and
resulting observation while avoiding the unrelated or contradictory steps
inside a whole trajectory. Transition decomposition may also make the formerly
overlong train record usable without truncation. The V4-candidate pilot changes
memory granularity, not the fixed-cost reversible-field principle: parent
trajectories remain ledger units, and deleting one parent subtracts all of its
child transition deltas.

## Explicit Nonclaim

RCMF V3 has no validated end-to-end trajectory-memory success claim. It must
not be labeled `final`, `successful`, or `working`. The tag records a
component-validated pre-transition research baseline, not a production model
or a completed AppWorld agent.
