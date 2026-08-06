# Current State

Last updated: 2026-08-05.

## VERIFIED

- Local repository path: `C:\gbz\RCMF_codex`.
- Lambda project path: `/lambda/nfs/rcmf-persist/project`.
- Lambda persistent root: `/lambda/nfs/rcmf-persist`.
- Lambda verified virtual environment: `/home/ubuntu/venvs/rcmf-py311`.
- Local pre-workflow commit: `11571b0`.
- Lambda pre-workflow commit: `11571b0`.
- Lambda branch at audit time: `master`.
- Lambda GPU at audit time: one NVIDIA H100 80GB, 0 MiB used.
- Lambda persistent filesystem mounted at `/lambda/nfs/rcmf-persist`, 3.0P
  total, about 28G used.
- No real training/evaluation process was running during the 2026-08-04
  workflow audit.
- GitHub CLI `gh` is not installed on the local Windows host.
- GitHub SSH key file `C:\Users\Admin\.ssh\github_rcmf` exists.
- GitHub SSH key fingerprint:
  `SHA256:OWb0aCR7HIqa8luPJSQM/f9M9r4pWp7klDTBr79goiQ`.
- The user verified interactive GitHub SSH authentication as `BenjaminGump`.
- After the key was loaded into Windows ssh-agent, Codex verified GitHub SSH
  authentication and pushed `workflow/research-loop`.
- Next-iteration local tests passed on 2026-08-04:
  `python -m pytest -q` -> `43 passed`.
- Lambda next-iteration validation passed at commit `9fb0817`:
  `python -m pytest -q` -> `43 passed`.
- Raw-text teacher pilot source validation passed at commit `e295a2b`:
  local `python -m pytest -q` -> `47 passed`; Lambda
  `python -m pytest -q` -> `47 passed`.
- Raw-text teacher audit3B source validation passed at commit `9640634`:
  local `python -m pytest -q` -> `48 passed`; Lambda
  `python -m pytest -q` -> `48 passed`.
- Next-iteration active AppWorld configs use `injector.type=additive_token`,
  provisional default `position=last_user_k`, and `num_tokens=4`; `first_k`
  and `last_prompt_k` remain available for later ablation, and old
  `additive_prefix` remains only as a compatibility alias.
- The full-size AppWorld training path now uses record-level Qwen-hidden memory
  representations: multi-chunk records are token-weighted into one
  representation and compiled once.
- The full-bank support path excludes current task, episode, replay, and
  lineage keys. The CLI-compatible mode name `all_except_current_task` now has
  this stricter behavior.

## Baseline

- Corrected bare Qwen3-8B AppWorld full baseline:
  `53/168 = 31.55%`.
- Corrected bare Qwen3-8B first-10 baseline:
  `3/10 = 30%`.
- First-10 baseline successes:
  `325d6ec_1`, `325d6ec_3`, `29a7b7e_1`.

## Current RCMF Best-Known Results

- Filtered full-demo train data:
  `/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`.
- Filtered data counts: 638 decision examples and 46 memory records.
- Context-length check after filtering: 0 over-limit examples; max total length
  35,615 tokens.
- Current best fixed first-10 RCMF result:
  semantic-retrieval final checkpoint, `4/10 = 40%`.
- Semantic-retrieval first-10 successes:
  `325d6ec_1`, `325d6ec_2`, `325d6ec_3`, `29a7b7e_1`.
- Semantic-retrieval partial full result was stopped early:
  `7/37 = 18.9%`.
- Paired first-37 comparison against the locked bare-Qwen run:
  baseline `10/37`, RCMF `7/37`, retained `5`, lost `5`, gained `2`.
- Lambda tokenizer-only memory chunk audit for the filtered train memory bank:
  46 records, 46 chunks, 0 multi-chunk records, max record length 35,566 tokens,
  chunk limit 40,960.
- Lambda diagnostics on the legacy semantic-retrieval checkpoint found strong
  read collapse: memory_z pairwise cosine mean `0.999994`, memory_z mean
  direction norm `0.999997`, address top1 max load fraction `0.448276`.

## Primary Raw-Text Teacher Pilot

VERIFIED:

- Milestone 3 was completed without launching full student training or a full
  AppWorld evaluation.
- Pilot artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_pilot_20260805_001`.
- Source commit used by the teacher cache:
  `e295a2bd449f38f87e4ad8d945e73aa55d0e5ef7`.
- Teacher cache version: `raw_text_memory_teacher_labels_v1`.
- Teacher memory renderer version:
  `teacher_only_raw_memory_section_v1`.
- Teacher model/checkpoint identity:
  `frozen_hf_pretrained:Qwen/Qwen3-8B`.
- The teacher used deterministic target scoring only: L0 is the mean
  target-token NLL under the unchanged full-demo prompt, and Lj_text is the
  same target loss after inserting one legal cross-task raw MemoryRecord into a
  teacher-only memory section.
- The teacher did not use compiled RCMF memory, external APIs, action
  generation, or student training.
- Formal leakage exclusion was preserved: for every decision state, normal
  teacher candidates exclude same task, episode, replay, and lineage memory
  records.
- Deterministic pilot size: 24 selected decision states stratified over
  task/app diversity, early/middle/later steps, and short/medium/long prompts.
- Proposed candidate pairs: 96. Unique scored or preflighted pairs including
  audit rows: 260.
- Scored rows: 250. Over-context rows skipped after preflight: 10.
- No prompt or raw memory text was truncated.
- Utility counts: positive 71, neutral 11, negative 168.
- Utility distribution: mean `-0.008413`, std `0.313950`, min `-1.060414`,
  p05 `-0.339788`, p25 `-0.152369`, median `-0.047554`,
  p75 `0.016794`, p95 `0.660061`, max `1.452909`.
- Utility correlations: vs raw memory tokens `0.075251`; vs combined context
  tokens `0.081745`.
- All-memory audit subset: 4 states, candidate recall of the
  highest-utility legal memory was `0/4 = 0.0`.
- Runtime: `498.29` seconds on one Lambda H100. Estimated full-dataset scoring
  cost at this measured rate: candidate proposal path about `1.77` GPU hours;
  all-legal-memory scan about `16.25` GPU hours.
- Minimal no-training additive-token smoke passed for `first_k`,
  `last_prompt_k`, and `last_user_k` with K=4. All three had zero embedding
  delta under zero memory and identical target loss to the no-memory base loss.

## Milestone 3B Expanded All-Legal Teacher Audit

VERIFIED:

- Milestone 3B was completed without launching full student training, full
  AppWorld evaluation, or full 638-state teacher-cache generation.
- Audit artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001`.
- Source commit used by the audit:
  `964063416a2fc3c48bf04bb11db7354fac96028c`.
- Audit cache version: `raw_text_memory_teacher_audit3b_v1`.
- Existing pilot selection was reused exactly: 24 states from
  `raw_text_pilot_20260805_001/pilot_states.json`.
- Legal pair count across the 24 states: 1,080.
- Scored rows: 1,052. Over-context rows recorded and masked: 28. No
  truncation was performed.
- Cached rows reused from Milestone 3: 260. Newly scored rows: 802.
- Utility counts: positive 364, neutral 122, negative 566.
- Utility distribution: mean `0.047545`, std `0.350456`, min `-1.243614`,
  p05 `-0.358663`, p25 `-0.119575`, median `-0.018214`,
  p75 `0.088473`, p95 `0.847706`, max `1.620315`.
- Utility correlations: vs raw memory tokens `0.086112`; vs combined context
  tokens `0.006909`.
- Existing proposal recall@1/2/4/8 was `1/24 = 0.041667` for all four K
  values.
- Proposal regret: mean `0.275401`, median `0.104668`, max `1.108213`.
- Proposal positive utility mass coverage: `0.107657`.
- Restricted to states with best legal utility at least `0.05`, `0.10`, and
  `0.25`, recall@4 was respectively `1/17`, `1/16`, and `1/13`; mean regret
  was respectively `0.363960`, `0.381725`, and `0.441131`.
- Candidate-source ablations: cosine_top2 and same_app each hit the best legal
  memory in `1/24` states with positive-mass coverage `0.069089`; random
  low-similarity hit `0/24` with positive-mass coverage `0.038568`.
- Deterministic reproducibility check passed for fixed positive, neutral, and
  negative pairs: repeated L0, Lj_text, and utility differences were all `0.0`
  under tolerance `1e-5`.
- Representative prompt inspection checked 3 high-positive and 3 high-negative
  rows. Obvious leakage, delimiter, section-order, target-hash, and
  memory-hash issue count: 0.
- Full 638-state all-legal token preflight completed: exact legal pairs
  28,710; scoreable pairs 27,054; over-context pairs 1,656
  (`5.768%`); preflight runtime `1,077.62` seconds.
- H100 scoring estimate for a complete all-legal teacher cache, using the
  audit3B measured scoring speed, is `40,731.11` seconds or `11.31` H100
  hours.
- Recommendation recorded by the audit: option A, generate the complete
  all-legal teacher cache after user and ChatGPT review. This recommendation is
  based on reproducibility, prompt-inspection health, utility signal, cost, and
  the fact that all-legal scoring removes the candidate-recall bottleneck; it
  is not based only on candidate recall.

## Milestone 3C Complete All-Legal Raw-Text Teacher Cache

VERIFIED:

- Milestone 3C was completed without launching RCMF student training or a full
  AppWorld evaluation.
- Source implementation commit used on Lambda:
  `80bebb05d97ec7d156b87850a7f1fd2811874d8a`.
- Lambda artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- Cache version: `raw_text_memory_teacher_full_cache_v1`.
- Scoring definition:
  `frozen_qwen_full_demo_raw_memory_mean_target_nll_v1`.
- Source rows were reused only after validating model identity, renderer
  version, target hash, memory hash, and scoring definition. Reuse sources:
  `raw_text_pilot_20260805_001` and `raw_text_audit3b_20260805_001`.
- Cache reuse validation saw 1,340 candidate cached rows, 1,080 unique
  compatible pairs, 260 duplicate compatible rows, 0 duplicate inconsistent
  rows, and 0 rejected rows.
- Exact final counts matched the Milestone 3C preflight contract: 638 states,
  46 memory records, 28,710 legal pairs, 27,054 scoreable pairs, and 1,656
  over-context pairs.
- New scoring work: 26,002 newly scored pairs and 1,628 newly generated
  over-context rows. Failed pairs: 0. Retried pairs: 0.
- Over-context rows were recorded with null utility and
  `valid_for_loss=false`; no prompts, raw memories, or targets were truncated.
- Validation passed: no duplicate state-memory keys, no missing or unexpected
  legal keys, no illegal leakage pairs, finite losses for all scored rows, and
  correct null/masked fields for over-context rows.
- Deterministic reproducibility check passed for fixed positive, neutral, and
  negative rows: repeated L0, Lj_text, and utility differences were all `0.0`
  under tolerance `1e-5`.
- Representative inspection selected 30 rows across highest-positive,
  highest-negative, high-overlap-low-or-negative, and anomalous groups. Obvious
  issue count: 0.
- Runtime: `36,949.37` seconds, or `10.26` actual H100 hours on one Lambda
  H100.
- Utility counts on scoreable rows: positive 13,426, neutral 4,861, negative
  8,767. Proportions: positive `0.496267`, neutral `0.179678`, negative
  `0.324056`.
- Utility distribution: mean `0.085425`, std `0.335507`, min `-1.996295`,
  p05 `-0.352794`, p25 `-0.056140`, median `0.009323`, p75 `0.179088`,
  p95 `0.750906`, max `2.333721`.
- Missingness: 113 states had no positive valid memory and 94 states had no
  negative valid memory. State valid-memory counts had min/median/max
  `0/44/45`; state over-context memory counts had min/median/max `0/1/45`.
- Missingness was concentrated in long memories: long-memory rows had 1,178
  over-context pairs, medium 311, and short 167. One memory
  `076f5673-6565-5f20-aada-6f16a0f8d4b0` was over-context for all 602 legal
  states and valid for 0 states.
- Top over-context states were late `afc0fce_1` steps with 45 over-context
  memories and 0 valid memories.
- Overlap diagnostics: utility correlations were `0.133437` with shared API
  count, `0.158156` with shared code-token count, `0.138547` with code-token
  Jaccard, and `0.074342` with exact normalized target substring in memory.
- Exact normalized target substring present in raw memory had higher mean
  utility (`0.125319`) than absent (`0.069831`), but both strata contained
  negative rows.
- Full cache utility distribution differed from the 24-state audit:
  positive/neutral/negative proportions were `0.496267/0.179678/0.324056`
  versus audit3B `0.346008/0.115970/0.538023`; mean utility was higher by
  `0.037879`. The 24-state audit was not fully representative by the recorded
  thresholds.
- A deterministic future student split manifest was created with seed `13`,
  task-grouped split, 46 tasks total, 37 train tasks, 9 validation tasks, 499
  train states, and 139 validation states. No student training was launched.
- Lambda post-run status: no tmux server running and GPU reported `0 MiB / 0%`.

## Milestone 4 Stage-B Student Labels and Addressing-Only Pilot

VERIFIED:

- Milestone 4 was completed without launching full-bank end-to-end RCMF
  training, program-head training, additive-token injector training, Qwen action
  loss, or AppWorld agent evaluation.
- Final source commit used for the pilot:
  `9f84b77dfb2e42ef3ec32a51567f376379ee352a`.
- Stage-B label artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`.
- Addressing-only pilot artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_only_pilot_20260806_003`.
- Teacher cache source:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`,
  version `raw_text_memory_teacher_full_cache_v1`.
- The existing task-grouped split manifest was used: seed 13, 37 train tasks
  with 499 train states, and 9 validation tasks with 139 validation states.
- Strict inductive memory split was enforced. The Stage-B memory bank contains
  train-task memories only. Validation-task memories were excluded and never
  used in training or validation scoring.
- Special memory `076f5673-6565-5f20-aada-6f16a0f8d4b0` belongs to train task
  `afc0fce_1`, had zero valid Stage-B train labels, was kept in the ledger
  with `eligible_for_stage_b=false`, and was removed from the effective
  addressing bank.
- Effective Stage-B memory bank size: 36.
- Excluded memories: 10 total, consisting of 9 validation-task memories and the
  special zero-valid train memory.
- Stage-B labels used only `valid_for_loss=true` teacher rows. Over-context
  rows remained missing/masked and were not converted to zero, neutral, or
  negative labels.
- Stage-B label validation passed with error count 0. Missing legal teacher
  pair count after the own-task masking fix: 0.
- Label counts:
  train states 499, valid rows 16,786, positive/neutral/negative
  8,230/3,067/5,489, strong positive 6,599, strong negative 4,386,
  no-positive states 83, all-missing states 8.
- Validation label counts:
  states 139, valid rows 4,930, positive/neutral/negative
  2,412/850/1,668, strong positive 1,882, strong negative 1,307,
  no-positive states 24, all-missing states 0.
- The 8 all-missing Stage-B training states are late `afc0fce_1` steps 29-36:
  lines 85-92 in the filtered decision-example JSONL.
- Threshold coverage was reported for 0.01, 0.05, and 0.10 without selecting
  thresholds from validation labels.
- Tiny overfit test on 8 train states improved best validation-as-overfit
  NDCG@4 to `0.616348`, confirming that gradients can move the addressing
  path on a tiny subset.
- Final three-seed addressing-only pilot used real multi-state batches,
  task-balanced batching, frozen program head, no injector, and no Qwen action
  loss.
- Best-checkpoint validation aggregate over seeds 1/2/3:
  learned NDCG@1/4/8 `0.371993/0.386161/0.413739`;
  best-memory recall@1/4/8 `0.074341/0.175060/0.256595`;
  positive mass coverage@1/4/8 `0.050589/0.147272/0.244730`;
  MRR `0.165514`; positive-vs-negative pairwise accuracy `0.184593`.
- Baselines on the same validation rows/bank:
  global train-utility NDCG@4 `0.453376`, rho-only NDCG@4 `0.370048`,
  frozen-Qwen cosine NDCG@4 `0.366233`, deterministic random NDCG@4
  `0.366264`.
- Shuffled validation-state representations matched learned performance:
  shuffled NDCG@4 `0.386161` and positive mass coverage@4 `0.147272`.
- Geometry diagnostics show severe natural collapse:
  state-address pairwise cosine mean `0.996045`, state centered effective rank
  `2.269852`, state top-1 basis load fraction `1.0`; alpha pairwise cosine
  mean `0.997041`, alpha centered effective rank `2.427664`, alpha top-1 load
  fraction `1.0`; mean correct-vs-shuffled score absolute delta `0.000113`.
- Program head stayed frozen: program-head max absolute delta was `0.0` for all
  three seeds.
- Scientific gate failed. The state-conditioned model did not beat the global
  train-utility baseline on NDCG@4, and shuffled-state performance was not
  materially worse than correct-state performance.
- Lambda post-pilot status: no active Stage-B tmux/process and GPU reported
  `0 MiB / 0%`.

## Milestone 4B State-Conditioned Addressing Diagnosis

VERIFIED:

- Milestone 4B completed without launching Stage C, program-head training,
  additive-token injector construction/training, Qwen action loss, full RCMF
  end-to-end training, or AppWorld agent evaluation.
- Final source commit used for the corrected 4B run:
  `e61981fdd10514ba3250f32176f45ea21c2d0661`.
- Corrected 4B artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Inputs were the existing Stage-B labels
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/student_labels_20260806_002`,
  existing cached Qwen state/memory representations, and existing Stage-B
  checkpoints from `addressing_only_pilot_20260806_003`.
- Runtime for the corrected 4B run was `122.98` seconds. Lambda tests passed
  with `60 passed`.
- Hard-top-k disjoint-support dead zone was directly verified: constructed
  state support `[0,1,2,3]` and memory support `[60,61,62,63]` produced
  `q=0.0`, state gradient norm `0.0`, and memory gradient norm `0.0`.
- The overlapping-support control produced `q=0.596093`, state gradient norm
  `0.127603`, and memory gradient norm `0.128555`.
- Existing best Stage-B checkpoints:
  seed 1 and seed 3 had zero support overlap for all `5,004` validation
  state-memory pairs, raw dot products all `0.0`, and gradient norms `0.0`
  for state projector, state address head, memory projector, alpha head, and
  rho head on the representative batch.
- Existing best Stage-B checkpoint seed 2 had one active support overlap for
  all `5,004` validation state-memory pairs, raw dot mean `0.005924`, and
  nonzero representative-batch gradient norms, but still had state top-1 load
  `1.0` and alpha top-1 load `1.0`.
- Forensics conclusion: hard-top-k disjoint-support zero-gradient trapping
  affected a subset of seeds, and all best checkpoints showed shared-basis
  collapse. Rho/global-prior domination was not the verified primary cause.
- Teacher utility decomposition over train labels:
  memory main-effect variance explained `0.017852`, train residual variance
  `0.109839`, train memory-mean variance `0.002016`, train state-mean variance
  `0.057149`, residual effective rank `26.145799`, and utility effective rank
  `26.145799`.
- Train residual distribution had mean approximately `0.0`, std `0.331419`,
  min `-1.765584`, max `2.245731`. Validation residual distribution had mean
  `-0.029875`, std `0.324027`, min `-1.863085`, max `1.523608`.
- Diagnostic scorer ladder, validation three-seed mean/std:
  global prior NDCG@4 `0.453376/0.304515`, positive mass@4
  `0.141993/0.128717`;
  state-only residual head NDCG@4 `0.571722/0.015435`, positive mass@4
  `0.214541/0.006305`, correct-minus-shuffled NDCG@4
  `0.189911/0.024032`;
  signed two-tower residual scorer NDCG@4 `0.547162/0.026890`, positive
  mass@4 `0.204190/0.003365`, correct-minus-shuffled NDCG@4
  `0.144968/0.042046`.
- Current hard-top-k control reproduced the failed Stage-B result:
  NDCG@4 `0.386161/0.042185`, positive mass@4 `0.147272/0.010647`, and
  correct-minus-shuffled NDCG@4 `0.0`.
- Dense separate-head and dense shared-head address variants both collapsed to
  the frozen global prior: NDCG@4 `0.453376`, positive mass@4 `0.141993`, and
  correct-minus-shuffled NDCG@4 `0.0`.
- Dense address interaction contribution was effectively zero:
  state-interaction variance was about `3.47e-11` for dense separate-head seed
  1 and `1.37e-10` for dense shared-head seed 1; all dense best epochs were
  epoch 1.
- Decision-tree branch reached:
  `dense_rcmf_address_failed`. Because the state-only and signed two-tower
  diagnostics succeeded while dense RCMF-compatible addressing failed, the
  current address parameterization is the bottleneck. Stage C remains blocked.
- Lambda post-4B status: no active 4B process, no tmux server, and GPU reported
  `0 MiB / 0%`.

## INFERENCES

- The semantic-retrieval auxiliary loss improves the fixed first-10 slice but
  does not yet generalize to the broader AppWorld test distribution.
- The remaining problem is likely not the AppWorld prompt or execution loop,
  because the memory-scale-zero control reproduces the bare first-10 baseline.
- The learned memory read still appears too global/state-insensitive, even
  though semantic retrieval improves variation compared with the low-injector
  run.
- The semantic-retrieval candidate does not beat bare Qwen on the paired
  first-37 slice, so further full-size runs should wait for smoke diagnostics.
- The diagnostics support the state-insensitive memory-read hypothesis for the
  legacy semantic-retrieval checkpoint.
- Expanded audit3B supports keeping the local-Qwen raw-text teacher path alive,
  but the existing candidate proposal should not be used as the sole memory
  selector for labels.
- Milestone 3C strengthens the case that local-Qwen raw-text labels are a real
  signal source, because the complete distribution has nearly half positive
  rows and representative inspection found no obvious prompt/leakage defects.
- The 24-state audit was directionally useful for feasibility and debugging,
  but not representative enough for final label-distribution conclusions.
- Over-context missingness may matter for student-label construction because
  some late states and one very long memory are entirely unavailable under the
  no-truncation teacher contract.
- Milestone 4 suggests the current addressing-only objective/model still learns
  mostly state-insensitive memory ordering. The global utility baseline remains
  stronger than the learned state-conditioned model on held-out tasks.
- The tiny overfit result means the implementation has a live gradient path,
  so the pilot failure is more likely an objective/architecture/generalization
  issue than a completely disconnected training graph.
- Milestone 4B shows that the frozen Qwen state representations and teacher
  residual labels contain held-out-task state-conditioned signal, because both
  state-only and signed two-tower diagnostic residual scorers beat the global
  prior and degrade under state shuffling.
- The immediate bottleneck is not the raw teacher cache or validation split; it
  is the current RCMF addressing parameterization and its tendency toward
  dead/discrete or effectively constant dense interactions.

## GitHub Status

- User-provided repository SSH URL:
  `git@github.com:BenjaminGump/rcmf-research.git`.
- Local `origin` remote is configured as
  `git@github.com:BenjaminGump/rcmf-research.git`.
- GitHub branch `workflow/research-loop` has been pushed and configured as the
  upstream branch.
- Codex push workaround: run Git with
  `GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes'` so
  Git uses the same Windows OpenSSH agent path as the successful `ssh` command.

## UNVERIFIED

- GitHub repository visibility: not confirmed by the user yet.
- Whether the complete all-legal raw-text teacher cache improves a student
  model; no student training has been launched with these labels.
- Whether the new record-level full-bank training improves AppWorld
  performance; no new full training run has started for this iteration.
- Whether anti-collapse regularization, a different addressing parameterization,
  or a stronger state-memory contrastive objective can make Stage-B
  state-conditioned ranking beat global/rho-only baselines.
- Whether an RCMF-compatible signed residual-address design can preserve the
  successful two-tower signal while retaining a memory-bank interpretation.

## Immediate Workflow Status

- Working branch: `workflow/research-loop`.
- Latest pushed and Lambda-synced source commit before final records:
  `e61981f`.
- Lambda cannot currently pull GitHub directly because the instance has no
  GitHub private key/deploy key; sync used a local git bundle after pushing to
  GitHub.
- Lambda post-cache status: no tmux server running and GPU memory/utilization
  reported `0 MiB / 0%`.
- Do not launch program-head training, additive-token injector training,
  full-bank end-to-end RCMF training, or AppWorld agent evaluation until the
  user and ChatGPT review the Milestone 4B bottleneck diagnosis.
