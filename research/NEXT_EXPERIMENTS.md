# Next Experiments

The current priority is correctness and observability before any expensive full
GPU run. The next iteration should first prove that representation rendering,
full legal memory-bank construction, record-level memory writes, and
additive-token injection behave as intended.

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

- Stop after teacher-forced diagnostic metrics. Do not start Stage C2,
  full-bank aggregation, or AppWorld generation/evaluation.

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
