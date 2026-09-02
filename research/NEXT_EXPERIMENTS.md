# Next Experiments

## EXP-036C Review Stop

EXP-036C completed all 840 frozen trajectories. BEST-C is `48/168`, compared
with bare `44/168` and matched shuffle `42/168`; FULL1D-C is `40/168`, compared
with FULL1D-S `48/168`. BEST's absolute and specificity point estimates are
positive, leave-one-task-out direction-stable, and spread across task families,
but their paired confidence intervals include zero.

No next experiment is authorized. The next action is user/ChatGPT review of
the full per-task audit and paper-claim boundary. Do not start retraining,
calibration, another prompt or field, portability evaluation, another AppWorld
split, or paper automation automatically.

## EXP-036B Runtime Authorization Review Stop

EXP-036B has a valid deterministic harness and a frozen 168-task,
five-condition formal manifest, but formal evaluation remains `0/840`.
Expected total runtime is `32.8593h`; the preregistered conservative estimate
is `50.3493h`, above the approved `42h` cap.

No next experiment is authorized. A future review may explicitly authorize
the existing frozen manifest above 42 hours. It must not change the
determinism mode, task list, conditions, prompt, fields, checkpoints, shuffle,
generation settings, logs, efficiency grid, or reversibility coverage, and it
must not silently reduce work to fit the expired authorization.

## EXP-036A Reproducibility Review Stop

EXP-036A generated no formal performance result. It stopped because the
complete-path fresh-world smoke was not exactly deterministic: B0 and
FULL1D-S produced equal-set observations with different textual element order,
which later changed prompts and tokens.

No next experiment is authorized. A future reviewed protocol would first need
to decide whether to pin process hash state or canonicalize environment
observations, then refreeze and rerun the complete smoke before any formal
Test-Normal work. Do not make that change or launch formal, efficiency,
reversibility, portability, retraining, or paper automation automatically.

## EXP-035A Review Stop

EXP-035A is complete and reaches `INCONCLUSIVE`. The LOO-stable interaction
does not satisfy the co-adaptation branch because the native OO cell has zero
aggregate correct-minus-shuffle specificity. Selector and writer/reader
marginal directions reverse across the other component.

No next experiment is authorized. Review the full eight-task trace evidence,
especially the fresh-selector documentation-call concentration, before any
new proposal. Do not start prompt transport, EXP-035B, retraining, calibration,
scale changes, adapters, official dev, first37, or test-split work automatically.

## EXP-034A Review Stop

EXP-034A is complete. New N1 is `16/57`, above bare D0 `12/57`, but tied with matched-shuffle N2 `16/57` and below old D1 `17/57`. No follow-on experiment is authorized in this task.

Before any future work, the user and ChatGPT should review the full task-level audit, especially the missing matched-shuffle specificity, retained 5/11 old gains, recovered 3/6 old losses, increased N1 loops, and confidence intervals containing zero. Do not start retraining, calibration, another prompt, first37, test tasks, or architecture work automatically.
The current priority is an immediate submission-scope review after EXP-027B.
Matched-harness bare Qwen reaches `8/37`; automatic raw transition memory
reaches `5/37`; and the corrected generic PairMLP compiler remains
behaviorally indistinguishable from both state and transition shuffles.
Behavioral `p(s,m_transition)` and downstream RCMF stages remain blocked.

## Next Reviewed Option: AppWorld-Structured Compiler

Goal:

- Decide whether one final single-seed compiler rescue is scientifically and
  deadline-appropriate.
- If approved, add only compact deployment-available AppWorld procedural
  features to the compiler while keeping Qwen, selector, carrier, prompt,
  clean corpus, and one-step audit fixed.

Requirements:

- Preregister the exact structured features and prove they use no future
  action, observation, teacher utility, or behavioral outcome.
- Keep the six observation-excluded transition views and fixed deep-residual
  carrier as the primary neural path.
- Use the same A-only split and direct raw-policy plus mismatch objective.
- Use one seed `25101`; no rank, architecture, or carrier sweep.
- Require a clear P1 advantage over both state and transition shuffles before
  any full-bank or end-to-end work.

Stop condition:

- Do not start this option without separate review.
- If it fails memory-specific one-step behavior, end the compiled-program route
  for the submission.
- Do not train a memory-reader adapter first, start `p(s,m_transition)`, Stage
  C2, full-bank integration, end-to-end RCMF, full AppWorld evaluation, or V4
  tagging.

## Historical: Matched AppWorld 0.1.0 Replay Recovery

Goal:

- Create an isolated, version- and data-hash-pinned AppWorld 0.1.0 environment
  matching the nine immutable official successful trajectories.
- Re-run only the existing 45-state exact replay validator under the unchanged
  action alignment and observation normalization.

Requirements:

- Preserve the EXP-024A `_001` artifact and append-only ledger unchanged.
- Do not replace the current verified AppWorld 0.2.0.dev0 environment in place.
- Record package commit, code/data release hashes, task/database hashes, replay
  output hashes, and environment isolation identity.
- Diagnose any remaining observation differences without relaxing comparison
  semantics or removing states.

Stop condition:

- Do not resume EXP-024A Qwen generation unless all 45 states pass exact replay.
- If matched AppWorld 0.1.0 still fails, stop and investigate dataset snapshot,
  random-state, time, or API rendering contracts before any behavioral work.

## EXP-024A Signature-Balanced Causal Audit (Stopped)

- Preflight fixed 323 conditions over 45 states and 499 transitions.
- Replay passed `0/45`; Qwen generations and H100 hours are zero.
- Decision branch: `appworld_one_step_replay_invalid`.
- See
  `research/results/stage_c_procedural_causal_audit_6h_20260817_001.md`.

## EXP-022 Procedural/Outcome Supervision Audit (Completed)

- Stopped at the preregistered coverage gate: 12/18 held-out states had a
  legal Tier-3/4 candidate, below the required 70%.
- No field model, AppWorld replay, or one-step generation ran.
- See `research/results/stage_c_procedural_outcome_6f_20260816_001.md`.

## EXP-001 Correctness Smoke Before Full Training

Goal:

- Run the next-iteration RCMF pipeline on a tiny smoke configuration after
  syncing to Lambda.
- Verify that state representations use the same full-demo message renderer and
  Qwen chat template as evaluation.
- Verify that each MemoryRecord compiles into one write after any token-weighted
  chunk aggregation.
- Verify additive-token injection audits selected prompt tokens and never
  injects target tokens during training.

Measure:

- local and Lambda tests;
- `memory_record_chunk_audit.json`;
- `memory_injection_diagnostics_v2` JSON/Markdown;
- a one-step or few-step smoke train run without automatic truncation or
  downsampling.

Stop condition:

- Do not start a full-size training run until smoke and diagnostics complete
  without correctness failures.

## EXP-002 Primary Text-Memory Teacher Pilot

Goal:

- Completed on 2026-08-05 as
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001`.
- The pilot built a raw-text teacher that uses frozen Qwen3-8B scoring over raw
  memory text, not compiled leave-one-out RCMF memory.

Measure:

- 24 selected states, 250 scored rows, 10 over-context rows skipped after
  preflight, no truncation.
- Positive/neutral/negative utility counts: 71/11/168.
- Candidate recall on the 4-state all-memory audit subset: 0/4.
- Projected full-dataset cost: about 1.77 GPU hours for candidate scoring and
  16.25 GPU hours for all-legal-memory scoring at the measured pilot rate.

Stop condition:

- Met. Stop before full student training. The next action is review, not a
  training launch.

## EXP-006 Teacher Candidate Recall Fix

Goal:

- Status after Milestone 3B: the candidate proposal remains weak, but the
  audit recommendation is not to repair retrieval before every other step. The
  expanded audit scanned all legal memories for all 24 pilot states and found
  exact proposal recall@1/2/4/8 of `1/24`, mean regret `0.275401`, and positive
  mass coverage `0.107657`.

Candidates:

- Add a cheaper first-pass target-aware scorer that still uses frozen Qwen
  representations and never uses target loss as a selector for non-audit
  training labels.
- Increase candidate count and compare marginal recall/cost.
- Add app/entity/tool-call overlap features extracted from raw MemoryRecord
  text.

Stop condition:

- Treat this as an alternative if the user and ChatGPT reject all-legal cache
  generation due to cost or label quality. Do not start full RCMF student
  training until after review.

## EXP-007 Complete All-Legal Teacher Cache

Goal:

- Completed on 2026-08-05 as
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- The cache scored every scoreable legal state-memory pair for all 638
  decision states, reusing compatible pilot/audit3B rows and masking
  over-context pairs without truncation.

Current evidence:

- Cache version: `raw_text_memory_teacher_full_cache_v1`.
- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- Exact final counts: 638 states, 46 memory records, 28,710 legal pairs,
  27,054 scoreable pairs, and 1,656 over-context masked pairs.
- Reused compatible cached pairs: 1,080. Newly scored pairs: 26,002.
- Validation passed and reproducibility repeats for positive, neutral, and
  negative pairs had all L0/Lj/utility diffs equal to `0.0`.
- Representative inspection selected 30 rows and found 0 obvious issues.
- Runtime: `10.26` actual H100 hours.
- Utility counts positive/neutral/negative: 13,426 / 4,861 / 8,767.
- The 24-state audit was not fully representative of the complete cache:
  full positive/neutral/negative proportions were
  `0.496267/0.179678/0.324056` versus audit3B
  `0.346008/0.115970/0.538023`.

Stop condition:

- Met. Stop before student training or full AppWorld evaluation. The next
  action is review of teacher-label quality, missingness, overlap diagnostics,
  and student-label construction policy.

## EXP-008 Review-Gated Student Label Compiler

Goal:

- Completed on 2026-08-06 as
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`.
- The compiler transformed the complete raw-text teacher cache into a
  Stage-B addressing-label dataset using the task-grouped split manifest.
- Over-context rows remained missing/masked. The special memory
  `076f5673-6565-5f20-aada-6f16a0f8d4b0` was kept in the ledger but excluded
  from Stage B because it had zero valid train labels.

Measure:

- Effective memory bank size: 36.
- Train labels: 499 states, 16,786 valid rows, 8 all-missing states, 83
  no-positive states.
- Validation labels: 139 states, 4,930 valid rows, 0 all-missing states, 24
  no-positive states.
- Validation passed with error count 0.

Stop condition:

- Met.

## EXP-009 Stage-B Addressing-Only Failure Analysis

Goal:

- Completed on 2026-08-06 as
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Diagnosed why the first addressing-only pilot failed the scientific gate even
  though the tiny overfit test had a live gradient path.
- Do not move to program-head or injector training until this failure is
  understood and a reviewed Stage-B repair passes the gate.

Current evidence:

- Artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003`.
- Learned three-seed mean NDCG@4: `0.386161`.
- Global train-utility baseline NDCG@4: `0.453376`.
- Shuffled-state NDCG@4: `0.386161`.
- State-address pairwise cosine mean: `0.996045`.
- State top-1 basis load fraction: `1.0`.
- Alpha pairwise cosine mean: `0.997041`.
- Alpha top-1 basis load fraction: `1.0`.
- Scientific gate: failed.
- Milestone 4B forensic result:
  hard-top-k disjoint-support zero-gradient trapping affected seeds 1 and 3;
  seed 2 escaped full disjoint support but still collapsed to shared basis.
- State-only residual head succeeded:
  NDCG@4 `0.571722`, positive mass@4 `0.214541`,
  correct-minus-shuffled NDCG@4 `0.189911`.
- Signed two-tower residual scorer succeeded:
  NDCG@4 `0.547162`, positive mass@4 `0.204190`,
  correct-minus-shuffled NDCG@4 `0.144968`.
- Dense separate-head and dense shared-head RCMF address variants failed:
  both matched the global prior NDCG@4 `0.453376` with correct-minus-shuffled
  NDCG@4 `0.0`.
- Decision-tree branch: `dense_rcmf_address_failed`.

Candidate repairs:

- Design an RCMF-compatible signed residual-address model:
  frozen global prior `mu_i`, signed state-memory residual interaction, and a
  separate activation gate.
- Keep hard top-k disabled until a continuous dense/signed design passes the
  validation gate. Consider dense warm-up followed by sparsity annealing as a
  later ablation, not the next default.
- Preserve the signed two-tower residual scorer as the diagnostic target to
  match before adding memory program or injector training.

Stop condition:

- Met for the diagnostic milestone. Stage C remains blocked.

## EXP-010 RCMF-Compatible Signed Residual Address Redesign

Goal:

- Convert the successful signed two-tower diagnostic signal into an
  RCMF-compatible addressing mechanism without hard top-k.
- Keep the frozen train-derived global prior `mu_i` explicit, and train only a
  signed residual interaction plus a separate activation gate.
- Do not train program head or injector until this redesigned Stage-B gate
  passes.

Required evidence:

- Beat global prior and dense failed controls on held-out-task NDCG@4,
  positive mass@4, MRR, pairwise accuracy, and Spearman.
- Show correct-minus-shuffled NDCG@4 and positive mass@4 remain materially
  positive.
- Show interaction contribution variance is nontrivial instead of collapsing
  to near-zero as in `addressing_4b_20260806_002`.
- Preserve a memory-bank interpretation and report address geometry, support
  usage entropy, and gate behavior on no-positive states.

Stop condition:

- Completed by Milestone 4C artifact
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002`.
- The redesigned signed Stage-B address model passed both continuity and
  five-fold task-grouped CV gates.
- Next milestone may discuss program-head distillation, but the additive
  injector and Qwen action loss should remain disabled until signed-program
  behavior is diagnosed.

## EXP-011 Signed-Program Distillation Pilot

Goal:

- Completed on 2026-08-06 as
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002`.
- Stage C1 added content-derived program-vector learning and a minimal
  K=4 `last_user_k` additive-token behavioral decoder on top of the frozen
  Milestone-4C signed residual selector.
- Qwen3-8B and the signed selector stayed frozen; no AppWorld generation or
  full evaluation was run.

Evidence:

- Response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/response_cache_20260806_001`.
- Response cache validation passed: 638 states, 523 positive-teacher, 107
  baseline-teacher, 8 all-missing.
- Field algebra/reversibility, zero-delta equivalence, and tiny overfit passed.
- Three-seed validation target NLL was `0.196607/0.012709`; sparse teacher KL
  was `0.125854/0.011371`; `L0 - student` was `0.335801/0.012709`.
- Correct-minus-control target-NLL deltas: zero/bare field `-0.335801`,
  fixed-random program `-0.310052`, shuffled program `-0.092314`,
  shuffled state `-0.053176`, global-prior-only `-0.021917`, free-ID program
  `+0.007893`.
- Program centered effective rank was `10.774106/1.763191`; z centered
  effective rank was `3.572149/0.234162`.
- No-positive validation degradation was `0.028565`, above the allowed `0.02`.
- The original leave-one-out audit is invalid for that metric and has been
  superseded by EXP-012. It failed to remove the audited validation memory
  because `validation_full_bank=True` rebuilt an all-true include mask.

Stop condition:

- Met. Stage C1 stopped after the diagnostic gate.
- Scientific gate failed with branch
  `signed_program_channel_not_behaviorally_useful_or_content_not_distinct`.
- Do not launch Stage C2, AppWorld generation/evaluation, Qwen action loss, or
  joint selector/program/injector training until the failure is reviewed.

## EXP-012 Stage-C Program-Channel Failure Diagnosis

Goal:

- Completed on 2026-08-07 as
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001`.
- Correct the Stage-C1 leave-one-out mask bug and diagnose memory-specific
  causality using existing Stage-C1 checkpoints only.

Evidence:

- Source commit: `f998a45`.
- Response cache revalidation passed: 638 states, 0 errors.
- Runtime: `5,757.08` seconds, about `1.60` H100 hours.
- The old Stage-C1 leave-one-out metric is invalid because validation full-bank
  mask construction ignored the mutated `legal_effective_mask`.
- Corrected teacher-best LOO effect over 115 positive validation states and
  three seeds: mean `0.002334`, CI `[0.000444, 0.004588]`.
- Selector alignment for raw-teacher-best memories was weak:
  Recall@1/4/8 `0.113043/0.313043/0.466667`, median rank `10`, p75 rank `20`,
  and negative signed-score fraction `0.243478`.
- Teacher-best contribution was small: mean `3.50%` of summed contribution
  norm, median contribution rank `13`.
- In the 32-state all-memory compiled LOO subset, compiled effect versus raw
  teacher utility had Pearson `-0.006813` and Spearman `-0.010966`.
- Content minus free-ID target NLL was statistically positive overall:
  CI `[0.000152, 0.015463]`, meaning free-ID was lower NLL on average; the
  sparse-KL CI included zero.
- Injector scale `0.25` fixed no-positive degradation but gave much worse
  target NLL than scale `1.0`; it is not enough by itself.

Stop condition:

- Met. Decision branch: `selector_teacher_alignment_issue`.
- Do not start AppWorld agent evaluation or Stage C2.

## EXP-013 Selector-Teacher Alignment Repair

Goal:

- Completed on 2026-08-07 as
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001`.
- Repair the mismatch between the Stage-4C signed selector and raw-text
  teacher-best utility labels before another program-channel run.

Evidence:

- Source commit: `5e5c74c`.
- The ablation set included reproduced Stage-4C original loss, all-pair gap
  ranking, top-listwise utility distillation, sign calibration, and near-best
  multi-positive variants.
- CV selected `C_top_listwise_temp0p03`.
- 5-fold CV selected-config metrics:
  Recall@4 `0.387077`, Recall@8 `0.616991`, NDCG@4 improvement over global
  prior `0.083399`, correct-minus-shuffled NDCG@4 `0.131188`,
  utility-score Spearman `0.119677`, teacher-best negative-score fraction
  `0.203096`.
- Continuity-set metrics:
  Recall@1/2/4/8 `0.179710/0.266667/0.359420/0.582609`, median rank `7`,
  negative-score fraction `0.176812`, Spearman `0.174524`, NDCG@4 `0.581587`,
  correct-minus-shuffled NDCG@4 `0.206391`.
- Geometry did not collapse: interaction variance `1.506124`, q effective rank
  `36.530959`, k effective rank `17.653024`.
- Eval-only old Stage-C1 projection:
  teacher-best LOO effect mean `0.010726`, selector-top LOO effect mean
  `0.027432`, raw utility versus analytic delta-z Spearman `0.047117`.

Stop condition:

- Met. The selector was not repaired enough to unlock Stage C.
- Decision branch: `selector_capacity_or_representation_tradeoff`.
- Do not proceed to Stage C2 or a repeated full-bank Stage-C1 run.

## EXP-014 Pair-Level / Single-Memory Behavioral Grounding

Goal:

- Completed on 2026-08-08 as
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001`.
- Directly supervise the behavioral effect of individual memories before
  another full-bank Stage-C1 run.

Evidence:

- Source commit: `f8cc37547ec6c3e404f84c726efa01e4c8ccb9f9`.
- Pair cache validation passed: `1,728` selected legal pairs, `1,152` train,
  `576` state-held-out validation, with complete positive/neutral/negative/
  random category coverage and no missing category slots.
- The primary model bypassed the selector intentionally:
  `z(s,i)=p_i`; no selector score, no gate, no empirical `mu_i`, no full-bank
  aggregation.
- Train-only perturbation smoke selected ratio target `1.0`; validation
  content delta-ratio mean was `1.054877`.
- Zero-program equivalence and tiny overfit passed.
- State-held-out content metrics: target NLL `0.665915`, sparse KL
  `0.318875`, behavioral-delta Huber `2.207829`, u_text/u_program Spearman
  `-0.293472`, sign agreement `0.403382`.
- Content did not show meaningful memory-specific advantage:
  content-minus-shuffled-program target NLL `-0.000166` and
  content-minus-memory-swap target NLL `-0.000081`.
- Memory-held-out 5-fold CV failed: content Spearman mean `-0.189175`, with
  `0/5` positive-Spearman folds.
- Content program geometry was highly aligned: pairwise cosine mean `0.998634`.

Stop condition:

- Met. Decision branch: `program_injector_behavioral_channel_insufficient`.
- Pair-level memory grounding did not pass.
- Do not start Stage C2, full-bank Stage-C1 retraining, joint selector/program
  training, Qwen action loss, or AppWorld agent evaluation from this result.

## EXP-015 Oracle Pair-Latent Injector Capacity Diagnostic

Goal:

- Isolate whether the Stage-5D failure is caused by the additive-token/injector
  behavioral channel itself or by the memory-content program compiler.

Candidates:

- Train a free per-pair latent vector `z_{s,i}` or oracle per-pair small table
  against the same pair-response cache and behavioral-delta target.
- Keep Qwen frozen and continue teacher-forced target scoring only.
- Do not use selector scores, gates, empirical `mu_i`, or full-bank
  aggregation.
- Compare oracle per-pair `z`, free per-memory `z`, content-derived program,
  random, mean, and zero controls under the same perturbation-ratio budget.
- Measure whether the injector can reconstruct target-position
  raw-memory-teacher deltas when representation/compiler generalization is
  removed from the problem.

Required evidence:

- Oracle per-pair `z` should significantly beat zero/random/mean controls on
  behavioral-delta Huber and sparse teacher KL.
- If oracle per-pair `z` fails, redesign the injector or target-distribution
  loss before any further program-compiler work.
- If oracle per-pair `z` succeeds but free per-memory/content programs fail,
  focus the next repair on memory representation and program compiler capacity.

Stop condition:

- Met. The milestone stopped after teacher-forced diagnostic metrics. Stage C2,
  full-bank aggregation, and AppWorld generation/evaluation were not started.

Result:

- Decision branch: `direct_delta_fails`.
- This direct-channel capacity interpretation is superseded by EXP-016A/B:
  the Stage-5E result used only two updates per pair. Its sparse-objective
  mismatch finding remains valid.
- Best K=4 direct DeltaE oracle failed the capacity gate despite weak positive
  utility correlation: Spearman `0.641904`, sign agreement `0.776978`,
  target-token delta correlation `0.369083`, mean perturbation ratio
  `0.488439`.
- Optional K=8 direct DeltaE did not repair the failure.
- Frozen-injector pair-z inversion failed.
- Free per-memory z was not scientifically successful despite weak positive
  rank/sign values, because it badly worsened target NLL, sparse KL, and
  target-delta error versus zero control.
- Target-token delta supervision is much better aligned with raw utility than
  the old sparse behavioral-delta Huber objective, but it is not sufficient by
  itself to make the current last-user additive-token channel pass.

## EXP-016A Convergence-Corrected Direct Oracle - Completed

Goal:

- Reassess the K=4 `last_user_k` direct input-embedding oracle after correcting
  Stage 5E's two-update underoptimization and adding a sequence-level utility
  objective.

Result:

- Completed at
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001`.
- The 192-pair ratio-1.0 oracle reached Spearman `0.976238`, sign agreement
  `1.0`, sequence Huber `0.054151`, target-delta correlation `0.820359`, and
  mean perturbation ratio `0.973289` after exactly 64 updates per pair.
- It reduced sequence Huber by `89.4905%` versus zero and beat matched random
  DeltaE decisively.
- It did not reach the documented plateau: Huber improved another `22.5965%`
  from u48 to u64.
- Decision branch: `oracle_not_converged_extend_updates`.
- The previous Stage-5E direct-capacity failure is superseded. The Stage-5E
  sparse-objective mismatch remains verified.

## EXP-016B Direct-Oracle Convergence Extension - Completed

Goal:

- Establish a documented plateau for the selected K=4 input-embedding direct
  oracle before changing injection sites or testing a 128D decoder.

Inputs:

- Resume the ratio-1.0 checkpoint:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001/confirmation/ratio_1.0/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u064.pt`.
- Reuse the same fixed 192 pair IDs and the same validated Stage-5D cache.
- Keep `sequence_utility_plus_sparse_kl`, K=4, `last_user_k`, ratio 1.0, and
  frozen Qwen unchanged.

Required evidence:

- Preserve exact per-pair counters and optimizer-state resume.
- Evaluate u80/u96/u112/u128 and continue in fixed 16-update increments if the
  two-part plateau condition is not met.
- Pre-register a train-only learning-rate stabilization rule before the run;
  do not select it from the final 192-pair metrics after the fact.
- Continue reporting Spearman, Pearson, sign agreement, sequence utility
  MAE/MSE/Huber, category means, target NLL, perturbation ratio, target-token
  delta correlation, sparse KL, gradient norm, boundary fraction, zero, and
  matched-random controls.
- Confirm that the ratio-1.0 utility gate still passes after convergence.

Decision:

- If the plateau gate passes, record
  `input_embedding_channel_capacity_passed_after_convergence` and stop for
  user/ChatGPT review before a new 128D injector/decoder capacity milestone.
- If metrics are still improving, extend updates again. Do not redesign the
  injection site.
- Only if a clear plateau fails the utility gate should a later-layer residual
  site comparison become eligible.

Stop condition:

- Stop after direct-oracle convergence and gate evaluation. Do not start
  pair-z/injector training, a new injection site, memory compiler work,
  Stage C2, full-bank training, AppWorld generation/evaluation, or Qwen
  fine-tuning in EXP-016B.

Result:

- Completed at
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001`.
- Resume integrity passed for the exact 192 ordered pairs, 192 Adam states,
  and min/max/mean source counters `64/64/64.0`; u64 metric reproduction had
  maximum absolute difference `0.0`.
- The fixed continuation reached u80/u96/u112/u128 with exact equal updates
  per pair. At u128, utility Spearman was `0.979465`, sign agreement
  `0.992806`, sequence Huber `0.034512`, and mean/max perturbation ratio was
  `0.975180/1.0000001`.
- u128 sequence Huber was `93.3019%` below zero. All eight utility-capacity
  checks passed.
- The registered plateau predicate passed at u128, but the curve was
  nonmonotonic: u128 Huber was `16.8911%` worse than the numerically best u112.
  The exact supplied predicate treats negative improvement as `<1%`; this
  caveat must remain attached to the formal result.
- Decision branch:
  `input_embedding_channel_capacity_passed_after_convergence`.
- No pair-z, injector-decoder, compiler, selector, Stage C2, AppWorld, or
  end-to-end work was started.

## EXP-016C 128D Shared-Decoder Capacity - Completed

Result:

- Global rank-128 DeltaE projection passed on both u112 and u128; rank 192
  exactly reproduced the source behavior.
- Linear and MLP decoders fit all train-fold DeltaE tensors nearly exactly.
- Frozen-linear held-out inversion was strong in every fold and pooled to
  u112/u128 Spearman `0.988537 / 0.994685` with sequence Huber
  `0.027538 / 0.015615`.
- Every numerical frozen-linear gate check passed, but no path reached the
  corrected plateau in all three folds. Formal branch:
  `shared_decoder_optimization_or_generalization_failure`.
- No compiler, selector, Stage C2, AppWorld, or end-to-end work was started.

## EXP-016D Frozen-Linear Inversion Convergence Extension - Not Launched

Status:

- EXP-016D was not launched before the V3 freeze or during EXP-017.
- Keep the proposal as a V3 diagnostic option, but it is not the immediate
  V4-candidate priority after the completed transition pilot.

Goal:

- Determine whether the existing frozen-linear held-out pair-latent paths
  reach the corrected plateau when resumed beyond u128, without changing the
  decoder manifold or scientific target.

Required constraints:

- Do not start until the user and ChatGPT approve a detailed milestone
  contract.
- Resume the exact EXP-016C frozen-linear per-fold checkpoints; preserve pair
  IDs/order, decoder hashes, z values, optimizer state, objective, ratio 1.0,
  K=4, and `last_user_k`.
- Keep Qwen frozen and use teacher-forced scoring only.
- Use fixed update intervals and the corrected plateau rule: absolute Huber
  change `<1%`, absolute Spearman change `<0.01`, and current Huber no worse
  than `1.02` times best-so-far.
- Do not resume frozen MLP or joint MLP in the primary extension unless a
  separately specified diagnostic requires it.
- Do not train a memory compiler, selector, full-bank model, Stage C2 model,
  or AppWorld agent.

Decision evidence:

- If frozen linear reaches plateau and retains the existing utility thresholds
  in all three folds, record the shared-128D linear decoder capacity gate as
  passed and stop for review before compiler design.
- If it plateaus below the gate, record a genuine held-out decoder
  generalization failure.
- If it remains materially improving at a preregistered hard cap, stop for
  review rather than redesigning the decoder or injection site automatically.

## EXP-018 State-Conditioned Transition Program Pilot - Stopped At Gate

Outcome:

- Parts A-D completed at source commit
  `0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c`.
- The immutable EXP-017 cache and a deterministic 29/8 parent-grouped
  transition split were validated. The A/B/C/D scoreable pair counts are
  2,667/904/752/256.
- The concat-MLP upper bound failed on double-held-out D: Spearman 0.059482,
  sign agreement 0.758170, and Huber 0.126287. It was worse than state-only
  and barely changed under transition shuffling.
- Signed bilinear also failed on D: Spearman 0.111083, sign agreement 0.575163,
  and Huber 0.062106.
- Branch: `state_transition_representations_insufficient`.
- Per the preregistered decision tree, no Qwen behavioral program training,
  trajectory control, selector, injector, full bank, Stage C2, AppWorld run,
  or V4 tag was started.

## EXP-019 Representation Interaction Repair - Completed, Gate Failed

Outcome:

- The immutable EXP-018 cells remained A/B/C/D = `2,667/904/752/256`.
- Main effects explain `44.91%` of cell-A utility variance; nontrivial residual
  variance and effective rank remain.
- Residual/listwise objective repair on the original vectors failed.
- Span-aware multi-view frozen-Qwen models and deterministic structured
  features failed the double-held-out interaction gate.
- The best field-compatible model, the multi-view low-rank tensor, reached D
  NDCG@4 `0.554092`, below transition-only `0.566808`, and was insufficiently
  sensitive to state shuffle.
- The prompt-only frozen-Qwen cross-encoder fit A and C but failed B and D. On
  D it reached NDCG@4 `0.379564`, while transition shuffle reached `0.495154`.
- Its 12-query-task learning curve remained slightly rising and unstable.
- Branch: `query_task_coverage_insufficient`.
- The representation gate was not repaired; behavioral
  `p(s,m_transition)` remains blocked.

No behavioral program, injector, selector, production field, Qwen behavioral
backpropagation, Stage C2, AppWorld evaluation, end-to-end run, demo change,
or V4 tag was started.

## EXP-020 All-Task Query Coverage - Completed, Gate Failed

Outcome:

- The final manifest covers all 37 train tasks and all 9 heldout tasks with 92
  states. The unchanged transition panel yields 13,320 legal, 13,128
  scoreable, and 192 masked over-context pairs.
- Prompt cross-encoder D NDCG@4 rises
  `.360914 -> .461709 -> .523176` across LC12/LC24/LC37, so the curve is
  materially increasing rather than saturated.
- The LC37 cross-encoder still fails: per-state/residual Spearman
  `.117526/.063559`, state/transition-shuffle drops `.017769/.062644`, a
  transition-shuffle CI spanning zero, and only 5/9 positive heldout tasks.
- The low-rank field retains 71.22% of the cross-encoder gain and has larger
  shuffle drops, but per-state Spearman is `.134551`, only 4/9 tasks are
  positive, and the transition-shuffle CI also spans zero.
- The optional 638-state action-intent probe succeeds with correct-state mean
  `.875899` versus shuffled `.420863`. Decision intent is present even though
  pairwise transition utility does not generalize.
- Primary branch: `query_task_coverage_still_data_limited`.
- Final diagnostic branch:
  `state_intent_available_but_memory_utility_target_not_generalizing`.
- The representation gate is not repaired; behavioral
  `p(s,m_transition)` remains blocked.

## Proposed Post-EXP-020 Utility-Target Audit - Review Required

Goal:

- Determine whether a state action-intent-conditioned or relative/pairwise
  memory-use label generalizes better than the current absolute raw-text
  target-NLL utility, before spending compute on any behavioral program.

Required constraints:

- Obtain a new user/ChatGPT milestone contract before implementation.
- Reuse the immutable EXP-020 query, transition, parent, leakage, and
  representation artifacts wherever the candidate target permits.
- Define every candidate label before inspecting the 9 heldout tasks. Estimate
  all main effects, normalizers, and thresholds from train cells only.
- Preserve the current raw-transition target as a locked comparator; do not
  rewrite or relabel the EXP-017/020 caches in place.
- Test state and transition shuffle sensitivity, per-state ranking, heldout
  task consistency, and paired confidence intervals. Pooled sign accuracy is
  insufficient.
- Do not start behavioral `p(s,m_transition)`, an injector, a selector, Stage
  C2, AppWorld generation, or end-to-end RCMF in the target-audit milestone.

Candidate questions:

- Does predicting relative utility within the same state remove transition
  popularity and prompt-difficulty main effects?
- Does conditioning comparisons on predicted target app/API/action type make
  helpful-transition ranking more transferable across tasks?
- Are raw-memory NLL gains too noisy or semantically indirect to serve as a
  deployable interaction label despite being valid teacher measurements?
- Would additional source tasks or task augmentation improve the target after
  the target itself is shown to be learnable? Do not assume more states from
  the same 37 tasks are sufficient.

## EXP-003 Trace-Level First-37 Diagnosis

Goal:

- Compare bare Qwen, semantic-retrieval final checkpoint, and memory-scale-zero
  behavior on the retained/gained/lost first-37 task groups.

Measure:

- exact model input, model output, and AppWorld observation for representative
  retained, gained, and lost tasks;
- tool-error loops, no-code failures, premature complete_task, and wrong-app
  actions.

Stop condition:

- Produce a failure taxonomy and at least two falsifiable next hypotheses.

## EXP-004 Memory Scale Sweep

Goal:

- Check whether semantic retrieval benefits are robust to memory perturbation
  magnitude.

Candidates:

- scale 0.0 control;
- scale 0.25;
- scale 0.5;
- scale 1.0.

Report success-set deltas, not only aggregate score.

## EXP-005 Retrieval Collapse Diagnostics

Goal:

- Decide whether architecture work should target state addressing, memory
  compiler geometry, injector gating, or loss weighting.

Measure:

- memory_z mean/std/min/max;
- state address norms;
- state-to-support distribution entropy;
- teacher/student retrieval KL;
- per-task relation between retrieval concentration and success/failure.

## Standing Rule

Before training on any new prepared AppWorld data or subset, run the context
length preflight. If over-limit examples exist, stop and ask the user before
filtering.

## EXP-023 Full-Transition Coverage Preflight - Completed, Diversity Gate Failed

Outcome:

- The full 499-transition bank nominally repairs procedural coverage: strict B
  and deployment E each cover 17/18 held-out states at Tier 3/4.
- The old 148 panel was globally insufficient rather than merely split
  limited; none of its six B gaps was repaired by D alone.
- Full context preflight found 44,910 legal, 43,415 scoreable, and 1,495
  over-context pairs with no truncation and no high-tier-only missing state.
- Coverage is not sufficiently diverse under the fixed audit rule: only 10/18
  B states have at least two high-tier signatures and parents; seven have only
  one signature and six are covered only by API-documentation transitions.
- Branch: `nominal_procedural_coverage_lacks_diversity`.

## Proposed EXP-024 Diversity-Aware Procedural Field And One-Step Audit - Review Required

Before launch:

- Obtain a user/ChatGPT decision on how repeated canonical signatures and
  API-documentation records count. Candidate policies may include immutable
  bank records with equivalence-class weighting or an explicitly expanded
  source-trajectory bank; do not choose using held-out labels and do not
  silently deduplicate.
- Preserve all 499 transitions, exact leakage rules, no-truncation behavior,
  92 queries, 45 one-step query states, fixed tiers, and the existing parent
  split unless a new reviewed contract explicitly changes them.
- Keep deployment-space E and strict parent-generalization B/D as separate
  reported gates.
- Required projected compute is `6.702/13.837/27.599` best/expected/
  conservative H100 hours. Because expected compute exceeds the 12-hour review
  threshold, obtain explicit approval before launching the full unchanged run.
- Optional cross-encoder expansion adds 13.415 expected H100 hours and must be
  separately justified. Required/with-optional storage is 2.77/3.96 GiB.
- Continue using one run UUID, append-only attempts, heartbeat, atomic pair and
  condition outputs, optimizer checkpoints, and hash-validated resume.

Until this review is complete, do not train the procedural field, run Qwen
generation, create AppWorld replay instances, train behavioral
`p(s,m_transition)`, or start Stage C2/end-to-end RCMF.

## Proposed Post-EXP-024R Historical Replay Contract Audit - Review Required

Goal:

- Determine whether historical execution time/randomness and one source-query
  identity inconsistency explain the remaining exact AppWorld 0.1.0 replay
  failure.

Required design:

- Preserve the 0.1.0 capsule, immutable 45 states, recorded actions, and locked
  observation normalization.
- Recover source experiment timestamps, frozen-clock behavior, authentication
  token issuance semantics, and any historical task/database snapshots from
  official artifacts before rerunning anything.
- Audit task instruction and supervisor identity consistency for all 45 states,
  with raw personal values retained only in Lambda-private artifacts.
- Rerun the unchanged 13-state sentinel first. Require 13/13 complete replay
  before launching the full 45-state validation.
- If a semantic JWT comparison is scientifically desired, preregister it as a
  separate secondary metric; do not rewrite the locked exact comparator.

Blocked until review:

- EXP-024A generation and condition execution;
- procedural field or behavioral `p(s,m_transition)` training;
- Qwen forward/generation, Stage C2, end-to-end RCMF, or a V4 tag.

## Proposed Post-EXP-024R2 Source Snapshot Provenance Recovery - Review Required

Goal:

- Resolve the five `b0a8eae_2` source-query/task-supervisor identity
  mismatches without editing immutable identities, replacing states, or
  lowering the 45/45 gate.

Required design:

- Search immutable historical experiment bundles, task archives, manifests,
  and database snapshots for the exact source supervisor-query hash.
- Establish whether the raw successful trajectory was generated from a
  missing task snapshot or whether its prompt query is identity-inconsistent
  with the official task used for execution.
- Keep all raw identity values Lambda-private and commit only redacted hashes
  and provenance.
- Do not regenerate trajectories, substitute another supervisor, edit the
  source query, or silently drop the five states.
- If and only if all 45 identities are resolved, rerun the fixed 13-state
  sentinel twice in fresh AppWorld 0.1.0 worlds under semantic v2. Require
  13/13 twice before running full 45-state semantic replay.

Blocked until that review and gate pass:

- EXP-024A Qwen generation and memory-condition execution;
- procedural field or behavioral `p(s,m_transition)` training;
- injector, selector, Stage C2, end-to-end RCMF, or a V4 tag.

## Proposed Post-EXP-024R3 Corpus Identity Reconciliation - Review Required

Goal:

- Resolve the dataset-building provenance of both `b0a8eae_2` and
  `b0a8eae_3`, then determine whether a provenance-valid reconstruction is
  scientifically and operationally possible.

Required design:

- Trace both supervisor headers through raw experiment export, trajectory
  ingestion, task joins, and decision-example rendering. Test explicitly for
  a systematic task-ID/index join error.
- Enumerate every artifact derived from train-side `b0a8eae_3`, including
  Stage-B labels, teacher-source memories, transition manifests/caches, and
  downstream trained checkpoints.
- Decide whether remediation requires rebuilding affected source records,
  labels, transition caches, and downstream experiments. Preserve every old
  artifact and publish a provenance map rather than overwriting data.
- Revisit the 45-state versus whole-task quarantine only after all mismatch
  tasks are resolved or formally excluded under a separately reviewed corpus
  contract.
- Recompute EXP-018 through EXP-023 sensitivity only on a genuinely
  provenance-valid corpus. Do not use a `b0a8eae_2`-only deletion.

Blocked until that review:

- EXP-024A Qwen generation and memory-condition execution;
- AppWorld semantic replay and any 40-state causal-audit continuation;
- procedural field or behavioral `p(s,m_transition)` training;
- injector, selector, Stage C2, end-to-end RCMF, or a V4 tag.

## Proposed EXP-025B Minimum Rebuild With Replay Preflight - Review Required

Goal:

- Convert the validated EXP-025A structural corpus candidate into a formally
  replay-valid, minimally recomputed V4 input lineage.

Mandatory step zero:

- Preregister a schema-limited semantic rule for AppWorld 0.1.0 login calls
  whose entire observation is a JWT. It may ignore only the already verified
  temporal `exp` value and consequent signature after stable claims match.
- Rerun the fixed sentinel twice and all 45 states. Require complete semantic
  replay before any Qwen or model work.

Minimum rebuild after replay passes:

1. Regenerate `35` state, `2` memory, and `17` transition representations.
2. Recompute `3,658` invalid Qwen-scored rows; reuse hash-valid unaffected
   rows.
3. Rebuild Stage-B labels and all structural manifests.
4. Retrain only checkpoints required by the current V4 hypothesis; do not
   automatically rerun every historical V3 experiment.
5. Rerun procedural coverage on the reconciled lineage.
6. Resume the one-step causal audit only after all preceding gates pass.

Estimated rebuild cost is `1.126/1.407/2.110` best/expected/conservative H100
hours, `1.126/1.688/2.814` wall hours, and about `759 MB` of new storage.

Blocked pending review and replay pass:

- Qwen representation or teacher-score recomputation;
- any selector, program, injector, field, or Qwen training;
- EXP-024A generation, AppWorld condition execution, Stage C2, end-to-end RCMF,
  or a V4 tag.

## Proposed EXP-025C Signature-Balanced Field Prediction - Review Required

Goal:

- Test whether a deployable field can select behaviorally helpful raw
  transitions on the replay-validated reconciled corpus without collapsing to
  procedural signature frequency or API-documentation prompting.

Required design:

- Keep the EXP-025B `499`-transition clean bank, `150` signature classes,
  frozen representations, AppWorld 0.1.0 bridge, and three demonstrations.
- Train a field-compatible predictor with inverse signature-class-frequency
  weights and explicit API-documentation stratification.
- Report strict train-parent B separately from full deployment-space E; do not
  use deployment coverage to conceal parent-generalization failure.
- Compare correct content with signature-only, popularity, hard-negative,
  unrelated, state-shuffle, transition-shuffle, and alternate-exemplar
  controls.
- After the field gate is frozen, run a deployable top-transition one-step audit
  on the same clean 45-state contract and quantify retained oracle gain.

Hard boundary:

- EXP-025C must not automatically train behavioral p(s,m_transition), the
  additive injector, the signed selector, Stage C2, or end-to-end RCMF.
- Do not rerun old V3 checkpoint families or create/move a V4 tag.
- Preserve the EXP-025B oracle result as a causal upper bound, not a deployable
  result.

## Proposed EXP-025C-R Context-Feasibility Amendment - Review Required

Goal:

- Resume the already trained EXP-025C selector audit without changing field
  scores, seeds, candidate banks, or the 45 audit states.

Blocking fact:

- One F5 predicted-intent raw selection for `2a163ab_1` step 13 has no legal
  same-class prompt under the `40,960` context limit. The singleton prompt is
  `41,134` tokens. F1 and F3 are feasible for all states.

The review must preregister exactly one policy before any generation:

1. Mark the F5 contrast missing for that state and define paired denominators
   prospectively.
2. Use the next score-ranked context-feasible predicted-intent class, chosen
   without behavioral outputs, and label this as a changed baseline contract.
3. Validate a larger frozen-Qwen context contract separately before using it.

Do not truncate, silently drop the state, substitute based on outcomes, or
retrain the selector. After a policy is approved, rerun only condition
preflight, lifecycle smoke, and the frozen F1-F5 behavioral phase. Program,
injector, p(s,m_transition), Stage C2, end-to-end RCMF, and V4 tagging remain
blocked until behavioral retention is actually measured.

## EXP-025C-R Completed - Next Milestone Requires Review

EXP-025C-R used the approved missing-row policy and completed `224/224`
executable conditions while retaining one explicit, non-imputed F5 missing
slot. Both strict-B and deployment-E behavioral claims pass. Deployment-E F3
retains `0.75/1.0909/0.8462` of the oracle exact-API/action-signature/semantic-
successor gains, beats its signature-only card, and is positive on `8/9`
tasks. The F3-F5 complete-case successor contrast and one-row sensitivity are
also positive.

Recommended separately reviewed EXP-025D goal:

- Test state-conditioned transition-program distillation using the frozen
  replay-validated clean corpus, frozen signature-balanced selector, and clean
  one-step behavioral target.
- Preserve strict-B parent generalization and deployment-E full-bank behavior
  as separate claims.
- Preregister the program latent, frozen decoder/injection contract, training
  objective, controls, optimization schedule, and causal evaluation before any
  run.
- Treat the validated selector as a fixed upstream component; do not silently
  retune it from program outcomes.
- Require raw-content and signature-only controls so program behavior cannot
  collapse to procedural metadata.

Still blocked pending that review:

- behavioral `p(s,m_transition)` or program-compiler training;
- additive-token injector changes, Stage C2, end-to-end RCMF, full AppWorld
  evaluation, or an RCMF V4 tag.

## EXP-025D Runtime Approval Required

The CPU/tokenizer-only preflight is complete. The full unchanged design is
projected at `144.13/201.72/518.42` best/expected/conservative H100 hours,
with `145.13/203.72/522.42` wall hours including final validation/reporting and
`21.46 GB` projected storage.

Exact frozen scope:

- logical A/B/C/D/E pairs `640/139/112/112/139`;
- scoreable A/B/C/D/E pairs `607/135/112/112/135`;
- decoder `192` calibration plus `64` grouped-heldout pairs;
- `970` new sparse-teacher rows;
- expected decoder/program updates `16,384/352,704`;
- all seven fixed program architectures, three conditional final seeds, and
  all controls/held-out cells retained.

Next action is explicit user approval to resume the same EXP-025D run UUID.
After approval, execute the full prespecified experiment unchanged. Before
approval, do not launch Qwen scoring/training or a duplicate run. Full-bank
training, Stage C2, end-to-end RCMF, full AppWorld evaluation, and V4 tagging
remain out of scope.

## EXP-025D-Fast Completed - Representation Repair Review Required

The bounded fast pilot reached `state_transition_representations_insufficient`.
Canonical pair effects are stable, but PairMLP cannot predict them from the
current independent frozen multiview representations. Do not resume the old
201.72-hour EXP-025D design and do not begin full-bank integration.

Recommended 3-5 day critical path for a separately reviewed milestone:

1. Audit canonical-target effective rank, normalization, grouped split
   difficulty, and nearest-neighbor structure on the same 224 pairs.
2. Run one bounded pair-aware frozen-Qwen information upper bound against the
   same targets, with no broad architecture or layer sweep.
3. If it passes, distill one observation-excluded, field-compatible richer
   representation and rerun PairMLP/factorized tensor gates.
4. Only after tensor passage, run the existing B/C/D/E teacher-forced harness.
5. Only after teacher-forced passage, run the existing H1-H4 same-world
   one-step audit and decide whether full-bank integration is justified.

Still blocked: p(s,m_transition), compiler/injector, full bank, Stage C2,
end-to-end RCMF, full AppWorld evaluation, and V4 tagging.

## EXP-025D-Direct Completed - Immediate Scope Review Required

The direct PairMLP upper bound passes, but the r16 field-compatible model fails
held-out B/E Huber and the E memory-swap gate. Do not run H1-H4 or begin
full-bank integration from this checkpoint.

Recommended next review, not yet authorized:

1. Use the existing saved predictions to diagnose static/conditional
   cancellation, ratio saturation, decoder calibration drift, and why rank
   correlation remains positive while Huber degrades on B/E.
2. Decide whether one narrowly preregistered factorization/calibration repair
   can fit the submission critical path. Do not reopen an architecture sweep
   or multi-seed search.
3. If no low-risk repair is justified, freeze the validated clean selector and
   raw-transition one-step causal result as the positive contribution, and
   report compiled-program amortization as a bounded negative result.
4. Only a separately reviewed factorized teacher-forced pass may unlock the
   existing H1-H4 one-step harness.

Still blocked: compiled p(s,m_transition), full bank, program compiler,
injector training, Stage C2, end-to-end RCMF, full AppWorld evaluation, and an
RCMF V4 tag.

## EXP-025D-G2 Completed - Behavioral Retention Review Required

The exact r16 continuation resolves convergence of the teacher-forced utility
objective: u48 passes A/B/E with B/E Spearman `0.5516/0.5738` and Huber
reduction `29.70%/30.64%`. The compiled one-step intervention does not pass.
It retains only `27.27%` of raw-transition semantic-successor gain, has
negative action-signature retention, fails to beat shuffled transition, and
is positive on `3/9` tasks.

Recommended deadline action:

1. Freeze the clean selector and raw-transition one-step causal result as the
   validated positive V4-candidate contribution.
2. Treat r16 compiled-program behavioral retention as a bounded negative
   result; do not spend the submission window on u64 or r64 by default.
3. Review paper scope and method claims before authorizing another component
   experiment.
4. If compilation remains essential, preregister a narrow objective that
   directly preserves action/signature/successor behavior rather than scalar
   teacher utility alone. This must be a separate milestone.

Still blocked: full-bank program integration, p(s,m_transition), compiler or
injector training, Stage C2, end-to-end RCMF, full AppWorld evaluation, and an
RCMF V4 tag.

## EXP-025D-G3 Completed - Submission Scope Lock Required

Both the existing Direct PairMLP and the conditional raw-memory token-policy
PairMLP fail the one-step behavioral-retention gate. Do not run r64, resume
r16, or begin another representation study on the submission critical path.

Recommended next review:

1. Freeze the signature-balanced selector plus selected raw-transition
   one-step causal result as the validated positive V4-candidate contribution.
2. Record compiled-program behavioral transfer as a bounded negative result:
   scalar utility and token-policy teacher forcing both improve, but neither
   preserves enough pair-specific generated behavior.
3. Lock paper claims, tables, and ablations around clean provenance, selector
   generalization, raw-content causality, and the compiled-program negative
   result.
4. Only if a trajectory-level result is essential to the submission, specify
   a separate minimal selector-plus-raw-transition partial end-to-end audit.
   Do not silently substitute this for a validated compiled program.

Still blocked: r64 or another compiled program, full-bank compilation,
p(s,m_transition), compiler/injector training, Stage C2, end-to-end RCMF, full
AppWorld evaluation, and an RCMF V4 tag.

## EXP-026A Completed - Submission Pivot Required

The free per-pair direct embedding oracle fails the locked behavioral channel
gate for K4, K8, and K16. This is a stronger stop than the prior amortized-
program failures because it removes decoder, latent, PairMLP, and factorization
capacity from the diagnosis.

Recommended submission action:

1. Stop r16/r64/PairMLP/program and full-bank compilation work for the ICLR
   critical path.
2. Lock the positive contribution around the identity-reconciled corpus,
   signature-balanced selector, and causally validated selected raw transition.
3. Report the compiled additive-channel result as a bounded negative: K4 has a
   measurable effect but retains only `50.00%/63.64%` of raw signature/
   successor gain and misses the preregistered gate.
4. If a trajectory-level result is essential, separately preregister a minimal
   selector-plus-raw-transition partial end-to-end audit. Do not represent it
   as a compiled-program result and do not start it automatically.
5. Move immediately to paper claims, tables, ablations, limitations, and the
   September submission schedule rather than another component search.

Still blocked: widened PairMLP, r64, another compiled program, full-bank
compilation, p(s,m_transition), Stage C2, end-to-end RCMF, full AppWorld
evaluation, and an RCMF V4 tag.

## EXP-026B Completed - Deep-Residual Compiler Review

The free deep-residual oracle passes the carrier gate with `100%/100%` raw
action-signature/semantic-successor gain retention and strong shuffled-control
separation. This reopens exactly one compiled-program question; it does not
validate a deployable program.

Recommended separately reviewed milestone:

1. Freeze layers `[7,14,21,28]`, the last four user positions, selector,
   Qwen, clean corpus, prompt, demonstrations, and seed `25101`.
2. Train one observation-excluded PairMLP from existing state/transition
   representations to a 256D latent and one shared decoder to the fixed
   `4 x 4 x 4096` residual carrier.
3. Use the existing raw-memory token-policy teacher and direct one-step audit;
   do not add another carrier, layer sweep, position sweep, or architecture
   family.
4. Require teacher-forced transfer, correct-versus-shuffled specificity, and
   one-step raw-gain retention before any full-bank integration.
5. If the compiler fails, freeze compiled-program work for submission. If it
   passes, return for review before full-bank integration.

Still blocked: p(s,m_transition), a production full bank, Stage C2,
end-to-end RCMF, full AppWorld evaluation, and an RCMF V4 tag.

## EXP-027A Completed - Submission Architecture Review Required

The first-37 selector-plus-raw-memory upper bound is clearly weak (`5/37`
versus bare `10/37`). The PairMLP deep-residual compiler fits teacher-forced
utility but is not memory-specific in generated behavior and reaches
`deep_residual_amortization_failed`.

Recommended next review:

1. Freeze the present compiler and conditional factorized Phase D. Do not run
   another carrier, controller-rank sweep, or full-bank integration.
2. Lock submission claims around clean provenance, signature-balanced
   selection, the positive one-step raw-content causal result, and the bounded
   negative trajectory/amortization results.
3. If exactly one additional mechanism is essential, explicitly choose between
   an AppWorld-enhanced structured compiler and a fixed trained memory-reader
   adapter. Preregister it as a new milestone with one seed and a direct
   behavioral gate.
4. Otherwise move immediately to paper tables, claims, limitations, and the
   ICLR critical path.

Still blocked: factorized Phase D, full-bank compilation,
p(s,m_transition), Stage C2, end-to-end compiled RCMF, full AppWorld
evaluation, and an RCMF V4 tag.

## EXP-028A Completed - Gate Domain Review Required

The train-side causal memory-use gate passes, and the AppWorld-structured
compiler has a small `PARTIAL_POSITIVE` one-step effect. It does not yet
support an end-to-end memory claim: both gated raw and gated compiled first37
score `8/37` because the locked gate activates on zero test-normal turns.

Recommended 48-hour action:

1. Freeze the gate, compiler, selector, prompt, carrier, and all first37
   outcomes.
2. Compare train-validation and first37 distributions of gate probability,
   intent confidence, selector margin, stage compatibility, and context
   overhead using deployment-available features only.
3. Do not use first37 success labels to recalibrate or choose a threshold.
4. Decide whether the submission reports the validated train-side causal gate
   plus partial one-step compiler effect, or treats compiled end-to-end memory
   as a bounded negative result.
5. Do not start another compiler architecture or a full bank before this
   claim/scope review.

Still blocked: full-bank integration, p(s,m_transition), another compiler,
Qwen training, Stage C2, end-to-end RCMF, full AppWorld evaluation, and an
RCMF V4 tag.

## EXP-028B Completed - Stop Structured Compiler

The gate-distribution audit finds broad deployment shift, but the decisive
ungated end-to-end control is negative: U1 correct compilation scores `0/37`,
below U2 transition shuffle `2/37` and U0 matched bare `8/37`. EXP-028B reaches
`structured_compiler_live_specificity_failed`.

Recommended next 48-hour action:

1. Freeze the structured compiler, gate, selector, prompt, carrier, and all
   first37 outcomes.
2. Lock submission claims around clean provenance, replay validity,
   signature-balanced selection, oracle raw-transition one-step benefit, free
   carrier capacity, and the negative live amortization result.
3. Make one explicit scope decision: run a separately preregistered, tightly
   bounded fixed memory-reader adapter study, or stop architecture work and
   focus on paper tables, claims, limitations, and reproducibility.
4. Treat all AppWorld 0.1.0 `test_normal` tasks as exposed development data;
   no honest untouched fresh-37 subset remains.

Still blocked: gate recalibration, another structured compiler, full-bank
integration, p(s,m_transition), Qwen training, Stage C2, end-to-end RCMF, full
AppWorld evaluation, and an RCMF V4 tag.

## EXP-029A Completed - Stop Neural Compiled Memory

The last bounded reader rescue passes every implementation invariant but fails
the heldout live behavioral gate at u1, u2, and u4. Correct, transition-shuffle,
state-shuffle, and zero programs all produce the same action-signature and
semantic-successor rates. EXP-029A reaches `fixed_memory_reader_failed`.

Recommended next 48-hour action:

1. Freeze EXP-029A and all earlier compiler/carrier artifacts; run no new
   reader, adapter, rank, objective, or full-bank experiment.
2. Lock the submission around the identity-reconciled corpus, exact semantic
   replay, signature-balanced selector, raw-transition one-step causal result,
   and validated free deep-residual carrier.
3. Present failed generic, structured, and fixed-reader amortization as a
   bounded negative result, carefully separated from the positive raw-memory
   and free-carrier findings.
4. Finish tables, ablation summaries, limitations, artifact links, and the
   paper narrative. Treat every AppWorld test-normal task as exposed.

Still blocked: neural compiled-memory architecture work, another reader,
full-bank integration, p(s,m_transition), Qwen training, Stage C2, end-to-end
RCMF, full AppWorld evaluation, and an RCMF V4 tag.

## EXP-030A Completed - Stop Before Reversible Field

The published-style single-memory cross-attention reader passes its complete
implementation contract but fails the mandatory heldout policy gate at every
checkpoint. The best correct-memory KL is `0.839403`, worse than zero memory
at `0.583907`, despite beating transition and state shuffles. EXP-030A reaches
`published_cross_attention_reader_failed_on_appworld`.

Recommended next 48-hour action:

1. Freeze EXP-030A and all prior compiler/reader/carrier artifacts.
2. Do not spend the deadline window on another reader, curriculum, seed,
   whole-bank field, or end-to-end compiled-memory experiment.
3. Lock the submission contribution around clean provenance, semantic replay,
   signature-balanced automatic selection, raw-transition one-step causality,
   and the validated free-carrier result.
4. Present generic, structured, fixed-reader, and cross-attention
   amortization as bounded negative evidence with exact scope limitations.
5. Complete tables, ablations, artifact links, reproducibility, limitations,
   and paper writing.

Still blocked: reversible-field construction, another neural reader,
full-bank integration, p(s,m_transition), Qwen training, Stage C2, end-to-end
RCMF, full AppWorld evaluation, and an RCMF V4 tag.

## EXP-031A Completed - Review Live-Specificity Boundary

The full reversible RCMF field is now tested directly. It is STRONG on
heldout-train one-step behavior and correct-vs-shuffled memory-specific on the
exposed first37, but it ties bare Qwen at `8/37`. The exact result is
`rcmf_full_field_live_memory_specific_signal`, not preliminary positive.

Recommended next 48-hour action:

1. Freeze the epoch-2 checkpoint, 499-memory deployment field, selector, Qwen,
   prompts, seed, first37 results, and detailed audit.
2. Review and explicitly resolve the preregistration's omitted D1==D0/D1>D2
   decision boundary; lock the claim as live memory specificity without an
   absolute task-success gain.
3. Run only non-behavior-changing write/read complexity benchmarks and produce
   audit-derived divergence, failure, and per-task tables.
4. Integrate the verified source provenance, heldout result, instant-write
   evidence, first37 comparison, limitations, and GitHub audit into the paper.
5. Require a new reviewed contract before any broader evaluation or
   portability run; do not start another component study or create a V5 tag.

Still blocked pending review: a full statistical AppWorld claim, portability,
new compiler/reader work, Stage C2, end-to-end expansion beyond the exposed
development run, and V5 tagging.

## EXP-031B Active - Stage 8A Cached Diagnostics

The immutable 14-task gain/loss audit and exact replay manifest are complete.
No calibration-candidate outcome has been inspected.

Next actions:

1. Commit and push the Git-safe audit report and machine-readable record.
2. Run G100, bare, field-shape, reversibility, shuffle, leakage, and exact
   deterministic-equivalence gates before any candidate comparison.
3. Derive and lock C50/C75/C90 and Q50/Q75/Q90 only from the unlabeled 98-state
   heldout distributions.
4. Run all preregistered candidates on cached 98-state and 14-critical-state
   diagnostics, retaining every per-state and per-task result.
5. Advance only candidates that preserve the three gain families and both
   retained successes; at most four may reach heldout live generation.

Still blocked: candidate first37 execution, any model retraining, retrieval,
hard gates, prompt/evaluator changes, Stage C2, and a V5 release tag.

## EXP-031B Completed - Freeze Calibration Route

EXP-031B reached `benefit_preserving_calibration_stop_route`. L1 remained memory-specific versus shuffle (`7/37` versus `5/37`) but fell below bare (`8/37`) and preserved only `2/6` original gains, losing the complete exact-set-migration family.

Recommended next 48-hour action:

1. Freeze the immutable EXP-031A checkpoint/field and all EXP-031B candidate, heldout, first37, audit, and provenance artifacts.
2. Do not run Q90 or introduce another scale/cap/confidence formula. The sequential stop condition has fired.
3. Integrate the verified positive and negative boundaries into the manuscript: constant-size reversible field, instant write, heldout specificity, correct-over-shuffle live signal, no absolute task gain, and failed benefit preservation under calibration.
4. Build paper tables directly from the committed machine-readable audit and per-task results; include the exposed single-seed limitation and the proxy-to-trajectory mismatch.
5. Use a separately reviewed contract for any future scientific run. Do not create or move a V5 tag.

Still blocked: further calibration, retrieval, hard gating, model retraining, broader AppWorld evaluation, portability claims, Stage C2, and V5 tagging.

## EXP-031C Completed - Freeze Q90 Calibration Route

EXP-031C reached scientific `STOP_ROUTE`. Q90 remains memory-specific relative to its matched shuffle (`5/37` versus `3/37`) but is below bare/original (`8/37`) and preserves only `3/6` original gains.

Recommended next 48-hour action:

1. Freeze the EXP-031A checkpoint/fields and all EXP-031B/C calibration, heldout, first37, and audit artifacts.
2. Build manuscript tables directly from the committed per-task and per-step records, separating memory specificity from absolute task benefit.
3. State the exposed single-seed limitation and the heldout-to-first37 reversal prominently.
4. Lock claims around reversible constant-size write/read behavior and matched-shuffle specificity; do not claim Q90 benefit preservation or improved full-task success.
5. Require a separately reviewed scientific contract for any future run.

Still blocked: further calibration, model retraining, retrieval, hard gating, broader AppWorld evaluation, portability claims, Stage C2, and V5 tagging.

## EXP-032A Completed - Freeze On-Policy Trajectory Distillation

EXP-032A reached `trajectory_union_distillation_failed_on_heldout`. Neither
reader-only checkpoint nor the single preauthorized writer-last-layer
checkpoint retained the immutable EXP-031A `5/8` heldout correct-field
success set; the writer+reader correct field also lost to shuffle `0/8 vs
2/8`.

Recommended next action:

1. Freeze the EXP-031A/B/C and EXP-032A checkpoints, fields, rollouts, union,
   teacher cache, heldout trajectories, and audits.
2. Do not run EXP-032A first37 because no candidate passed the locked heldout
   gate.
3. Review the complete trajectory divergences and failed retention pattern
   before proposing any new scientific contract.
4. Keep claims separated: the EXP-031A field has live correct-over-shuffle
   specificity, while EXP-032A did not convert its union-training objective
   into heldout task retention.
5. Do not launch a new architecture, gate, retrieval path, post-hoc
   calibration, paper-scope change, or V5 tag under the current task.

Still blocked: N1/N2 first37, broader AppWorld evaluation, architecture search,
retrieval, hard gating, further calibration, Stage C2, and V5 tagging.

## EXP-033A Completed - Stop After One-Demo Dev Evaluation

EXP-033A completed the frozen, evaluation-only 57-task AppWorld dev manifest. D1 correct field is `17/57`, compared with D0 bare `12/57` and D2 matched shuffle `12/57`.

Recommended next review action:

1. Review the committed per-task and per-step records with ChatGPT, using the GitHub report, handoff, paired analysis, and audit index as sources of truth.
2. Treat the positive D1-D0 and D1-D2 point estimates as descriptive evidence; retain the paired confidence intervals, exact McNemar results, and exposed-dev limitation alongside them.
3. Keep the interpretation narrow: frozen EXP-031A was useful and memory-specific under this one-demo deployment prompt in point estimates, but EXP-033A did not compare one demo against three demos.
4. Preserve the frozen checkpoint, fields, prompt manifest, dev manifest, condition manifest, raw Lambda artifacts, and Git-safe audit.
5. Require a separately reviewed contract for any future evaluation or training.

Still blocked under EXP-033A: retraining EXP-031A, training a one-demo model, addressing or field changes, first37, `test_normal`, `test_challenge`, another prompt variant, calibration, retrieval, a hard gate, multi-seed evaluation, or automatic follow-on work.

## EXP-034B Completed - Stop Fresh Selector Route

EXP-034B completed the exact 57-task N1/N2 dev manifest. Fresh-selector correct N1 is `10/57`, below bare D0 `12/57` and matched-shuffle N2 `15/57`.

Recommended next review action:

1. Freeze the fresh selector, selected epoch-1 writer/reader checkpoint, deployment field, complete dev trajectories, and Git-safe audit.
2. Build the paper-facing comparison across EXP-033A, EXP-034A, and EXP-034B, emphasizing that selector and heldout one-step proxies did not predict complete-trajectory direction.
3. Keep the interpretation narrow: EXP-034B rejects this bounded selector-retraining rescue, not every possible memory architecture or benchmark setting.
4. Use the committed per-task comparisons to characterize repeated-action and context-termination regressions without post-hoc model changes.
5. Require a new reviewed scientific contract for any future run.

Still blocked: another selector or architecture search, calibration, retrieval, hard gating, first37, `test_normal`, `test_challenge`, another prompt variant, broader evaluation, Stage C2, and V5 tagging.
