# Current State

## 2026-09-03 EXP-037A Reproducible 3D-Gated Pipeline Preflight

VERIFIED:

- EXP-036C is preserved at
  `53a1c583574d249d63b91a28fbb20ae17a7037b3`; local, GitHub, and Lambda
  started clean and at that exact commit, with no active experiment process.
- Archive branch `archive/exp036c-testnormal-final-53a1c583` and annotated tag
  `exp036c-testnormal-final-verified-53a1c583` preserve the starting state.
- Scientific source commit `02ef94726ea0fe566f7eea4fa137fb91da92977f`
  passed local and Lambda focused/full tests plus the bounded H100 technical
  smoke. All nine machine authorization checks are true.
- The measured smoke completed in `45.994` seconds with about `17.9 GB` peak
  GPU allocation. Expected/conservative total wall time is `47.5/92` hours,
  expected H100-active time is `39` hours, and expected/conservative storage
  is `46/90 GiB`.
- The recommended hard cap is `160` hours under the preregistered formula,
  below the user's explicit `200`-hour authorization.

CURRENT WORK:

- Commit and push the content-addressed Git-safe preflight packet, then write
  the machine-readable runtime authorization and immediately launch the 3D
  parent orchestrator in persistent tmux.
- The event-driven parent owns stage progression. The 20-minute monitor is
  read-only. The 1D arm remains conditional on exact
  `THREE_DEMO_REPRODUCTION_PASS`.

Last updated: 2026-09-03.

## 2026-09-02 EXP-036C Complete Test-Normal Evaluation

VERIFIED:

- The frozen 168-task, five-condition Test-Normal manifest completed all
  `840/840` trajectories with zero infrastructure exceptions, optimizer steps,
  or parameter updates.
- Success counts are `B0 44/168`, `BEST-C 48/168`, `BEST-S 42/168`,
  `FULL1D-C 40/168`, and `FULL1D-S 48/168`.
- BEST-C has positive point estimates versus bare (`+4` tasks) and matched
  shuffle (`+6`), but both paired 95% confidence intervals include zero.
- FULL1D-C is below bare (`-4`) and below its matched shuffle (`-8`); those
  intervals also include zero.
- The compiled field/read shape is independent of memory count, the 499-record
  canonical-cache remove/restore audit passed, and all raw Git-safe traces are
  being published through the EXP-036C audit index.
- A post-run efficiency-provenance audit found that a raw-reencoding cache
  equivalence aggregate had been hard-coded true. The original artifact remains
  immutable; commit `b99a87f` and an append-only correction record report the
  true `0/499` match count. Formal trajectories and frozen fields are unaffected.

CURRENT DECISION:

- EXP-036C is complete and stopped for user/ChatGPT review. Test-Normal is
  partially exposed, so this is a final development-benchmark result rather
  than an untouched-test confirmatory claim.
- No follow-on experiment, retraining, calibration, portability study, or
  paper automation is authorized.

Full report: `research/results/EXP_036C_APPWORLD_TESTNORMAL_FINAL.md`.

Last updated: 2026-09-02.

## 2026-09-01 EXP-036C Authorized Test-Normal Execution Preparing

VERIFIED:

- EXP-036B remains immutable at
  `97a5965fdf1e8cc4992b3df818966736ea0c159e`, with formal Test-Normal still
  `0/840` and `STOPPED_BEFORE_FORMAL`.
- The user authorized a new append-only EXP-036C run with a 200-hour hard
  safety ceiling. The old 42-hour limit is superseded for EXP-036C only.
- EXP-036C changes authorization metadata and the output root only. The frozen
  scientific manifest, five conditions, 168 tasks, BEST/FULL1D roles,
  generation settings, evaluator, and seed remain unchanged.
- Determinism remains `hash_seed_only` with process-start
  `PYTHONHASHSEED=25101`; canonicalization remains disabled.
- Archive branch `archive/exp036b-runtime-gate-stop-97a5965f` and annotated
  tag `exp036b-runtime-gate-stop-verified-97a5965f` preserve the EXP-036B stop
  commit.
- Local, GitHub, and Lambda preflight passed at the exact EXP-036B final SHA;
  both worktrees were clean, NFS was mounted with sufficient space, and the
  H100 was idle.
- The first requested `..._001` run root was preserved after a metadata-only
  smoke-summary field-name mismatch stopped its outer preparation. It produced
  no formal row. The unique formal run UUID is now
  `rcmf_appworld_testnormal_final_13c_20260901_002`.
- The corrected 002 preparation passed 37 focused Lambda tests and froze the
  168-task/840-condition manifest, 200-hour authorization, zero-leakage audit,
  EXP-036B smoke reuse, process hash sentinel, and atomic resume fixture.
- Expected and conservative complete runtimes remain `32.8593h` and
  `50.3493h`; both are below the authorized 200-hour ceiling.

CURRENT WORK:

- Launch the unchanged 840-row formal evaluation in a persistent Lambda
  session from the pushed frozen-manifest commit.
- Run efficiency, scaling, and numerical reversibility only after all formal
  performance rows are complete and sealed.

Last updated: 2026-09-01.

## 2026-08-31 EXP-036B Stopped At Runtime Authorization Gate

VERIFIED:

- EXP-036A remains immutable at
  `69a218a212f05709ca4c278f1ae14a89b44031a4`, with formal Test-Normal still
  `0/840` and `STOPPED_BEFORE_FORMAL`.
- The user approved a new append-only EXP-036B run whose only allowed harness
  repair is deterministic rendering of semantically unordered Python-set
  observations.
- Local, GitHub, and Lambda all started from the exact EXP-036A final commit;
  the frozen BEST/FULL1D packages, fields, shuffle, one-demo prompt, and ordered
  168-task manifest passed identity preflight.
- EXP-036A archive branch `archive/exp036a-determinism-stop-69a218a2` and
  annotated tag `exp036a-determinism-stop-verified-69a218a2` were created at
  the immutable stop commit.
- The exact EXP-036A set-order root cause was verified from raw Lambda rows.
- Process-start `PYTHONHASHSEED=25101` passed the B0 and FULL1D-S
  fresh-process probes. The final mode is `hash_seed_only`; no canonicalizer
  was implemented or enabled.
- The final complete-path smoke ran 15 trajectories in 15 fresh Python
  processes. All five repeated conditions matched exactly across prompts,
  token IDs, responses, code, observations, world state, field/query
  identities, steps, and outcome.
- Final local tests passed `778` with `2` skipped; final Lambda tests passed
  `780`.
- Expected complete runtime was `32.8593h`, but the conservative estimate was
  `50.3493h`, above the approved `42h` cap.
- Formal Test-Normal remains `0/840`; performance, efficiency/scaling, TTFT,
  serving-state, and reversibility results are `NOT_RUN`.
- Full report:
  `research/results/EXP_036B_APPWORLD_TESTNORMAL_FINAL.md`.

CURRENT BLOCK:

- Do not launch the frozen 840-row manifest without explicit approval for a
  conservative runtime above 42 hours.
- Do not reduce tasks, conditions, max steps, logging, efficiency coverage, or
  reversibility coverage to fit the old cap.
- No follow-on experiment is authorized.

Last updated: 2026-08-31.

## 2026-08-31 EXP-036A Stopped Before Formal Evaluation

VERIFIED:

- All frozen BEST/FULL1D package, field, shuffle, prompt, and ordered 168-task
  Test-Normal identities passed preflight under seed `25101`.
- The complete-path smoke completed 15 trajectories and 624 steps on the first
  two frozen Test-Normal tasks.
- Fresh-world repeats passed for BEST-C, BEST-S, and FULL1D-C but failed exact
  determinism for B0 at step 18 and FULL1D-S at step 19.
- At both first divergences, model output and executed code matched while the
  environment observation differed only in Python set representation order.
- The preregistered contract required exact observation, prompt, and token
  equality. EXP-036A therefore stopped before formal generation.
- Formal Test-Normal is `0/840` and `NOT_RUN`; efficiency/scaling and numerical
  reversibility are also `NOT_RUN`.
- No optimizer, backward pass, model change, field change, prompt change, or
  follow-up experiment occurred.

CURRENT BLOCK:

- Do not rerun under a new seed, add observation normalization, pin a new hash
  seed, or launch any EXP-036A formal phase without a new user-reviewed
  reproducibility protocol.
- Full report:
  `research/results/EXP_036A_APPWORLD_TESTNORMAL_FINAL.md`.

Last updated: 2026-08-31.

## 2026-08-31 EXP-035A Completed Diagnostic State

VERIFIED:

- EXP-034B completed and reached `STOP`: fresh-selector correct field was
  `10/57`, below bare `12/57` and matched shuffle `15/57` on official dev.
- The user has now approved EXP-035A, an evaluation-only four-cell swap of
  frozen old/fresh selector and frozen old/fresh writer/reader packages.
- EXP-035A starts from `4f5f1d3a74f196581fc570afc5f8eca75e663f4b`
  and uses only the immutable eight heldout-train tasks with leakage-safe
  401-memory fields.
- All 64 frozen component/binding trajectories completed with zero
  infrastructure exception, optimizer step, or parameter update.
- Correct/shuffle successes are OO `5/5`, OF `4/6`, FO `2/5`, and FF `3/4`.
- `M_selector = 0.125`, `M_WR = 0.000`, and interaction `I = 0.500`.
- The interaction is LOO-stable, but native OO has `Delta_OO = 0`; selector
  and writer/reader marginal directions are mixed.
- Final decision: `INCONCLUSIVE`.

VERIFIED MECHANISM EVIDENCE:

- Fresh-selector correct conditions have documentation-call C-S gaps of `+31`
  (FO) and `+106` (FF); FO-C contains 51 invalid-API steps.
- No wrong-app-family step or premature completion was observed.
- These counts are trace facts; the documentation-attractor explanation is an
  inference and does not overcome the mixed success marginals.

CURRENT BLOCK:

- Do not begin EXP-035B or any optimization after this diagnostic. Stop after
  the four-cell complete-trajectory report and user review. The report is
  `research/results/EXP_035A_RCMF_ONE_DEMO_COMPONENT_SWAP.md`.

Last updated: 2026-08-31.

## 2026-08-30 EXP-034A Current State

VERIFIED:

- One-demo-consistent retraining completed with the exact EXP-031A architecture, 29/8 split, 366/98 fixed states, and seed `25101`.
- The original heldout-only rule selected epoch 2 (`078dcd0...`) as `STRONG`; the 499-memory field is `f24b16e4...`.
- Official dev results are D0 `12/57`, old D1 `17/57`, new N1 `16/57`, and new N2 `16/57`.
- N1 is `+4/57` over bare but `0/57` over matched shuffle and `-1/57` versus old D1. All paired 95% intervals include zero.
- The Git-safe audit covers 114 new conditions and 2,801 steps with zero JWT or registered sensitive-observation leaks.

INFERENCE:

- Prompt-consistent retraining may improve absolute performance over bare, but does not validate a memory-specific dev gain because N1 and N2 tie.

CURRENT BLOCK:

- Stop after EXP-034A review. Do not start further retraining, calibration, architecture changes, first37, test-normal, test-challenge, or another prompt variant automatically.
- Full report: `research/results/EXP_034A_RCMF_ONE_DEMO_RETRAIN.md`.
Last updated: 2026-08-30.

## 2026-08-23 EXP-027B Active Submission State

VERIFIED:

- EXP-027B completed on branch
  `research/v4-memory-specific-deep-amortization` with global seed `25101`.
- The exact EXP-027A execution harness gives matched bare Qwen `8/37`, while
  frozen-selector raw transition memory remains `5/37`. The historical
  different-harness bare `10/37` is secondary only.
- The corrected policy objective and direct mismatch gradients produce
  positive teacher-forced state and transition specificity in A validation
  and B/C/D/E. The A-only rule selected u4.
- On the primary 32 one-step states, correct PairMLP P1 improves action
  signature and semantic successor over C0 by `+0.09375` each, but P1 is
  identical to transition-shuffle P2 and state-shuffle P3 on both metrics.
- P1 execution is `0.87500`, `6.25` percentage points below C0, and only
  `4/9` tasks are positive.
- EXP-027B reached `memory_specific_deep_amortization_failed`.
- All 180 one-step conditions completed with zero infrastructure exceptions;
  P0 reproduced C0 on all 45 states.

INFERENCE:

- Policy-space mismatch separation under teacher forcing does not transfer to
  deterministic memory-specific generation for the current generic
  observation-excluded PairMLP compiler.
- The generic PairMLP/program route is not justified for the submission.

UNVERIFIED:

- An AppWorld-structured compiler using deployment-available procedural
  features.

Current block:

- Do not start generic PairMLP, rank sweeps, factorized models, a memory-reader
  adapter, full-bank integration, behavioral `p(s,m_transition)`, Stage C2,
  end-to-end RCMF, full AppWorld evaluation, or V4 tagging.
- The only compiler rescue eligible for separate review is an AppWorld-
  structured compiler using deployment-available procedural features.
- Detailed result:
  `research/results/stage_c_memory_specific_deep_amortization_7g_20260823_001.md`.

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

## Milestone 4C Signed Residual Associative Field

VERIFIED:

- Milestone 4C completed without launching Stage C, program-head training,
  additive-token injector construction/training, Qwen action loss, full RCMF
  end-to-end training, or AppWorld agent evaluation.
- Final source commit used for the corrected 4C run:
  `2fc95e2d41da933810df53e78a0eed62c972ee70`.
- Corrected 4C artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002`.
- Superseded artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_001`.
  It had the same model metrics but an invalid field-algebra pass flag due to
  float32 accumulation/tolerance and a negative AUPRC bug. Both were fixed in
  source commit `2fc95e2`, and `_002` is the formal run.
- Runtime for the corrected 4C run was `907.20` seconds. Local and Lambda
  tests passed with `69 passed`.
- Implemented the signed residual field:
  `q_s = state_query_network(h_s)`, `k_i = memory_key_network(h_i)`,
  and `residual(s,i)=temperature*dot(q_s,k_i)/sqrt(rank)`, with frozen
  train-derived `mu_i` as the explicit global prior.
- The signed residual interaction uses no softmax, top-k, sparsemax, sigmoid,
  ReLU, clamp, or rho multiplication. The activation gate is separate and does
  not multiply ranking scores in this milestone.
- Reference signed two-tower and refactored core signed field reproduced
  exactly under copied weights: residual, gate, q, and k max absolute errors
  were all `0.0`.
- Continuity split, three-seed mean/std:
  global memory prior NDCG@4 `0.453376/0.304515`, positive mass@4
  `0.141993/0.128717`;
  frozen-Qwen cosine NDCG@4 `0.366233/0.268877`, positive mass@4
  `0.129411/0.151647`;
  state-only residual upper bound NDCG@4 `0.590775/0.016531`, positive mass@4
  `0.216996/0.004691`, correct-minus-shuffled NDCG@4
  `0.197830/0.019606`.
- Exact signed two-tower reference and signed core field rank128 matched:
  NDCG@4 `0.555174/0.018107`, positive mass@4 `0.202908/0.008782`,
  MRR `0.234205/0.011910`, Spearman `0.246267/0.019878`,
  correct-minus-shuffled NDCG@4 `0.162368/0.025262`, and
  correct-minus-shuffled positive mass@4 `0.076659/0.005683`.
- Core rank128 residual diagnostics:
  residual MSE `0.313298`, residual Huber `0.035495`, residual correlation
  `0.279242`, and interaction variance `0.291447`.
- Rank64 remained positive but weaker:
  NDCG@4 `0.537045/0.013213`, positive mass@4 `0.202676/0.007095`,
  correct-minus-shuffled NDCG@4 `0.117082/0.019127`.
- RMS-normalized rank128 variant:
  NDCG@4 `0.570537/0.015114`, positive mass@4 `0.201243/0.002476`,
  correct-minus-shuffled NDCG@4 `0.194207/0.019699`.
- Learned-prior deployability ablation did not fail:
  NDCG@4 `0.573485/0.018479`, positive mass@4 `0.205699/0.011101`.
  Prior-head train MSEs were `0.00420031`, `0.00462545`, `0.00598329`; train
  correlations were `0.439933`, `0.490678`, `0.544720`.
- Core rank128 gate metrics:
  AUROC `0.851812/0.004888`, AUPRC `0.964167/0.001949`, balanced accuracy
  `0.711715/0.021149`, false activation `0.472222/0.051967`, positive-state
  gate mean `0.908799/0.018320`, and no-positive-state gate mean
  `0.488292/0.043613`.
- Five-fold task-grouped CV over the 37 training tasks passed:
  mean NDCG@4 improvement over fold-specific global prior
  `0.085079/0.065855`, mean correct-minus-shuffled NDCG@4
  `0.102811/0.070323`, positive improvement in `4/5` folds.
- Field algebra and reversibility validation passed at rank `128`, program dim
  `32`, and bank count `36` using float64:
  V identity max error `8.53e-14`, G identity error `0.0`, add/remove norms
  `0.0/0.0`, replace errors `0.0/0.0`, arbitrary add/remove final norms
  `1.05e-13/2.27e-13`.
- Milestone 4C decision branch:
  `signed_core_field_passed_recommend_stage_c_pilot`.
  Stage C is still not launched until user and ChatGPT review.
- Lambda post-4C status: no active 4C process, no tmux server, and GPU reported
  `0 MiB / 0%`.

## Milestone 5 / Stage C1 Signed Program Distillation

VERIFIED:

- Milestone 5 / Stage C1 completed without AppWorld environment interaction,
  generated ReAct trajectories, full `test_normal` evaluation, joint selector
  fine-tuning, Qwen fine-tuning, or end-to-end RCMF training.
- Formal Stage-C1 response cache:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/response_cache_20260806_001`.
- Response cache version: `stage_c1_best_raw_memory_response_cache_v1`.
- Response scoring definition:
  `best_raw_memory_or_bare_qwen_target_top64_v1`.
- Response cache model/checkpoint identity:
  `frozen_hf_pretrained:Qwen/Qwen3-8B`.
- Response cache validation passed after the probability-bucket numeric
  tolerance fix: 638 states, error count 0, target NLL tolerance `2e-4`, and
  probability-bucket tolerance `1e-5`.
- Response cache condition counts: 523 positive-teacher states, 107
  baseline-teacher/no-positive states, and 8 all-missing states. By split:
  train 408 positive, 83 baseline, 8 all-missing, 491 valid Stage-C states;
  validation 115 positive, 24 baseline, 0 all-missing, 139 valid Stage-C
  states.
- Response cache size: 115,276,788 bytes. The first scoring pass reached the
  final progress marker at about `0.37` hours and failed only on overly strict
  float probability-bucket validation; the final validation rerun reused the
  638 cached rows and took `11.27` seconds.
- Teacher improvement distribution over valid states: count 630, mean
  `0.405222`, std `0.437226`, median `0.249824`, p75 `0.634917`, p95
  `1.318174`, max `2.333721`.
- Formal Stage-C1 signed-program artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/signed_program_c1_20260806_002`.
- Stage-C1 training source commit:
  `e17002258ddb52bce3fa86117a33ed872df2fa5c`.
- Stage-C1 corrected evaluation source commit:
  `9f16010e7dddbcb99ccf5b404347cadacc44a6c8`.
- Stage-C1 run used the existing 36-memory effective train bank, 491
  train Stage-C rows, and 139 validation Stage-C rows.
- Frozen modules in the primary Stage-C1 run: Qwen3-8B, the Milestone-4C
  signed state query, memory key, signed temperature, empirical train-derived
  `mu_i`, and the activation gate. Trainable modules: the content-derived
  program head and the additive-token injector only.
- The signed program field compiled prior-augmented keys
  `k_bar=[temperature*k/sqrt(rank), mu_i]`, program vectors
  `p_i=rms_normalize(tanh(program_head(h_i)))`, and read
  `z=gate*q_bar^T V/sqrt(q_bar^T G q_bar + eps)` with K=4
  `last_user_k` additive tokens.
- Field algebra and reversibility validation passed:
  full-read max error `0.0`, leave-one-out max error `0.0`,
  V/G read max error `1.11e-15`, add/remove norms `0.0/0.0`,
  replace errors `0.0/0.0`, arbitrary add/remove final norms
  about `2.00e-13/2.17e-13`.
- Zero-delta equivalence passed with max absolute NLL delta `0.0`,
  delta norm `0.0`, and selected token IDs/text
  `[40537, 8017, 6733, 13]` / `[" Spotify", " album", " library", "."]`.
- Tiny overfit passed on 8 positive and 8 no-positive states:
  sparse teacher KL fell from `0.181650` to `0.170965`, target NLL fell from
  `0.414490` to `0.402536`, and gradients reached both the program head and
  injector.
- Full Stage-C1 training runtime: `21,652.25` seconds, about `6.01` H100
  hours. Corrected eval-only control recomputation runtime: `486.21` seconds.
- Three-seed validation mean/std for the correct content-derived field:
  target NLL `0.196607/0.012709`, sparse teacher KL
  `0.125854/0.011371`, `L0 - student` `0.335801/0.012709`,
  improved fraction `0.817746/0.006783`.
- Mandatory control deltas are reported as correct minus control. Negative
  target-NLL deltas mean the correct content-derived field has lower NLL than
  the control. Three-seed mean/std:
  bare/zero field `-0.335801/0.012709`,
  fixed random program `-0.310052/0.014674`,
  shuffled program `-0.092314/0.029326`,
  shuffled state `-0.053176/0.022210`,
  mean program `-0.103837/0.051612`,
  global-prior-only `-0.021917/0.022167`,
  and free-ID program `+0.007893/0.012818`.
- The content-derived program did not beat the free-ID program; the positive
  sign for correct-minus-free-ID means free-ID had lower validation NLL on
  average.
- No-positive validation states were not preserved tightly enough:
  mean degradation relative to bare Qwen was `0.028565`, above the Stage-C1
  threshold `0.02`.
- Program geometry across seeds: centered effective rank mean/std
  `10.774106/1.763191`; program norm mean about `11.3137`; pairwise cosine
  means ranged from `0.556960` to `0.744982`.
- Read vector geometry across seeds: centered effective rank mean/std
  `3.572149/0.234162`; z norm means ranged from `18.620029` to `20.447519`;
  z pairwise cosine means ranged from `0.397926` to `0.621175`.
- Injector deltas were large in the trained runs: mean delta norm ranged from
  `24.831388` to `27.442460`, and mean delta ratio ranged from `7.344189` to
  `8.140082`.
- Stage-4C selector preservation passed exactly for all three seeds: max
  absolute errors for q, k, q_bar, k_bar, scores, gate, and temperature were
  all `0.0`.
- Original Stage-C1 leave-one-out audit is superseded and invalid for that
  metric. It changed `legal_effective_mask`, but `_compute_z()` built
  validation masks with `validation_full_bank=True`, so the requested memory
  was never removed. The original zero-effect leave-one-out numbers should not
  be used.
- Stage-C1 decision branch:
  `signed_program_channel_not_behaviorally_useful_or_content_not_distinct`.
  Stage-C1 did not pass, and Stage C2 is not allowed.
- Lambda post-Stage-C1 status: no active tmux server, no matching Python
  process, and GPU reported `0 MiB / 0%`.

## Milestone 5B Corrected Leave-One-Out Diagnostics

VERIFIED:

- Milestone 5B completed with eval/diagnostic work on existing Stage-C1
  checkpoints only. It did not retrain Stage C1, start Stage C2, fine-tune the
  selector/program head/injector/Qwen, run AppWorld generation/evaluation, or
  regenerate the teacher response cache.
- Source commit:
  `f998a45e2889802d0ba06dd00757461b1ebf16c5`.
- Lambda artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c1/stage_c1_5b_diagnostics_20260807_001`.
- Response cache validation passed again: 638 states, error count 0.
- Runtime: `5,757.08` seconds, about `1.60` H100 hours.
- Unit-test coverage for the mask bug passed locally and on Lambda. Local full
  test suite: `80 passed`. Lambda Stage-C1 tests: `11 passed`.
- The evaluation API now uses normal validation full-bank semantics by default,
  but respects an explicit `include_mask_override` for counterfactual audits.
  Train rows still use their `legal_effective_mask`, preserving own-task
  exclusion.
- Corrected three-seed leave-one-out covered all 115 positive-teacher
  validation states for every seed, 345 state-seed rows total.
- Corrected teacher-best removal effect: mean `0.002334`, std `0.019899`,
  median `0.000359`, p95 `0.017029`, max `0.211404`; bootstrap 95% CI for
  the mean `[0.000444, 0.004588]`.
- Other corrected removal effects:
  neutral mean `0.001784` with CI `[0.000353, 0.003362]`;
  most-negative mean `0.001118` with CI `[-0.000598, 0.002874]`;
  random-valid mean `0.001864` with CI `[0.000115, 0.003814]`;
  selector-top mean `0.006578` with CI `[0.002255, 0.012133]`;
  largest-contribution mean `0.002892` with CI `[-0.002419, 0.008387]`.
- Teacher-best minus selector-top effect was negative on average:
  mean `-0.004244`, CI `[-0.008464, -0.000886]`, so the selector-top memory
  had a larger compiled behavioral effect than the raw-teacher-best memory.
- Teacher-best selector alignment under the frozen Stage-4C signed selector:
  Recall@1 `0.113043`, Recall@4 `0.313043`, Recall@8 `0.466667`; rank
  mean `12.521739`, median `10`, p75 `20`, p95 `32`.
- Fraction of teacher-best memories receiving a negative signed score:
  `0.243478`.
- Raw teacher utility versus signed selector score correlation:
  Pearson `0.168978`, Spearman `0.271534`.
- Teacher-best contribution decomposition:
  fraction of summed contribution norm mean `0.035032`, median `0.032790`;
  fraction of numerator norm mean `0.043474`, median `0.027019`;
  contribution-rank mean `15.521739`, median `13`.
- Across 14,790 valid teacher rows, teacher utility was essentially
  uncorrelated with analytic `||delta_z_i||`: Pearson `0.002932`, Spearman
  `0.024077`. Signed score versus `||delta_z_i||` was also weak:
  Pearson `-0.049890`, Spearman `0.042973`.
- The 32-positive-state all-memory compiled LOO subset produced 3,456
  state-seed-memory rows. Compiled effect distribution had mean `0.000672`,
  std `0.012188`, median `0.000095`, p95 `0.012659`, max `0.198074`.
- In the all-memory subset, compiled effect did not correlate with raw teacher
  utility: Pearson `-0.006813`, Spearman `-0.010966`. It also did not
  correlate meaningfully with signed score or analytic delta-z norm.
- All-memory subset top-k overlaps were weak: effect top-1 matched raw-teacher
  utility top-1 in `0.041667` of state-seed cases; top-4 overlap with raw
  utility top-4 was `0.135417`; top-8 overlap was `0.225260`.
- Paired content-vs-free-ID statistics over all validation state-seed rows:
  content minus free-ID target NLL mean `0.007893`, bootstrap 95% CI
  `[0.000152, 0.015463]`; positive-teacher-only mean `0.009981`, CI
  `[0.001650, 0.018212]`; baseline/no-positive mean `-0.002111`, CI
  `[-0.021579, 0.018152]`.
- Content minus free-ID sparse teacher KL CI included zero overall:
  mean `0.002110`, CI `[-0.004788, 0.008445]`.
- Injector scale sweep without retraining:
  scale `0.0` reproduced bare behavior with no-positive degradation about
  `0.0` and teacher-best LOO `0.0`;
  scale `0.25` had target NLL `0.306223`, positive target NLL `0.366575`,
  no-positive degradation `0.001138`, and teacher-best LOO `0.003694`;
  scale `0.5` had target NLL `0.226846`, no-positive degradation `0.043378`;
  scale `1.0` had target NLL `0.196607`, no-positive degradation `0.028565`,
  and teacher-best LOO `0.002334`.
- Scale `0.25` was the only diagnostic scale satisfying the script's
  candidate rule of no-positive degradation <= `0.02` and teacher-best LOO
  larger than the scale-1.0 mean. It does not preserve most positive-state
  gain relative to scale 1.0, so it is only a clue, not an approved repair.
- Aggregate-read diagnosis on a 16-positive-state subset found that the
  current normalized read had lower target NLL than fixed-denominator,
  unnormalized matched-scale, top-absolute-contribution-only, and
  raw-teacher-best-only diagnostics for all three seeds.
- Milestone 5B decision branch:
  `selector_teacher_alignment_issue`.
- Recommendation: repair selector-teacher alignment before another
  program-channel run. Stage C2 remains disallowed.
- Lambda post-5B status: no tmux server running and GPU reported
  `0 MiB / 0%`.

## Milestone 5C / EXP-013 raw-teacher top-utility selector repair

VERIFIED:

- Milestone 5C completed on Lambda as selector-only Stage-B retraining plus
  eval-only old Stage-C1 projection. It did not train Stage-C program heads,
  train the additive-token injector, use Stage-C1 behavioral checkpoints as
  training targets, fine-tune Qwen, run Qwen forward passes during selector
  training, run AppWorld generation/evaluation, start Stage C2, or start
  end-to-end RCMF training.
- Source commit:
  `5e5c74c43b43dff9a8c2f3d5a054917849b33e29`.
- Lambda artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/selector_repair_5c_20260807_001`.
- Runtime: `3,687.92` seconds, about `1.02` H100 hours.
- Tests passed before the run: local full suite `84 passed`; Lambda targeted
  tests `15 passed`.
- The fixed ablation set was:
  `A_stage4c_original`, `B_gap_all_pairs_gap0p02`,
  `B_gap_all_pairs_gap0p05`, `B_gap_all_pairs_gap0p10`,
  `C_top_listwise_temp0p03`, `C_top_listwise_temp0p05`,
  `C_top_listwise_temp0p10`, `D_gap_top_sign`, and
  `E_gap_top_sign_nearbest`.
- The 5-fold CV selected `C_top_listwise_temp0p03`, but the CV scientific gate
  did not pass.
- CV selected-config metrics:
  Recall@4 `0.387077`, Recall@8 `0.616991`, NDCG@4 `0.523613`,
  NDCG@4 improvement over fold global prior `0.083399`,
  correct-minus-shuffled NDCG@4 `0.131188`, utility-score Spearman
  `0.119677`, teacher-best negative-score fraction `0.203096`, and
  interaction variance `1.526715`.
- The selected config improved Recall@8 materially over the reproduced
  Stage-4C original-loss baseline in CV, but failed the CV gate because
  Recall@4 was below `0.40`, utility-score Spearman did not improve over the
  Stage-4C original baseline, and the teacher-best negative-score fraction
  remained high.
- Continuity-set metrics on the original 9 validation tasks:
  Recall@1/2/4/8 `0.179710/0.266667/0.359420/0.582609`;
  median/p75/p95 teacher-best rank `7/14/30`;
  teacher-best negative-score fraction `0.176812`;
  strong-positive negative-score fraction `0.407899`;
  utility-score Spearman `0.174524`;
  NDCG@4 `0.581587`;
  correct-minus-shuffled NDCG@4 `0.206391`.
- The continuity scientific gate did not pass. It missed the required
  Recall@8 `0.62`, Recall@4 `0.43`, median rank `<= 6`,
  negative-score fraction `<= 0.12`, and Spearman `>= 0.35`, while satisfying
  the NDCG@4 and state-dependence thresholds.
- Geometry did not collapse: interaction variance `1.506124`,
  q centered effective rank `36.530959`, k centered effective rank
  `17.653024`, and correct-vs-shuffled valid interaction delta `1.276319`.
- Eval-only Stage-C1 projection used the old content program/injector
  checkpoints with only the selector payload replaced. It covered all 115
  positive-teacher validation states for seeds 1, 2, and 3, 345 rows total.
- Projection results:
  teacher-best signed-score rank median `7`, mean `10.069565`;
  teacher-best contribution rank median `10`, mean `12.576812`;
  teacher-best LOO effect mean `0.010726`, CI `[0.003277, 0.019094]`;
  selector-top LOO effect mean `0.027432`, CI `[0.017361, 0.039065]`;
  teacher-best minus selector-top LOO mean `-0.016706`.
- Projection raw utility versus analytic delta-z norm remained weak:
  Pearson `0.062450`, Spearman `0.047117`.
- Milestone 5C decision branch:
  `selector_capacity_or_representation_tradeoff`.
- Stage C remains unrepaired. The next program milestone must use explicit
  pair-level or single-memory behavioral grounding rather than immediately
  repeating the original full-field Stage-C1 training.
- Lambda post-5C status: no tmux server running and GPU reported
  `0 MiB / 0%`.

## Milestone 5D Pair-Level / Single-Memory Grounding

VERIFIED:

- Milestone 5D ran as
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/pair_grounding_5d_20260807_001`
  from source commit `f8cc37547ec6c3e404f84c726efa01e4c8ccb9f9`.
- The primary model deliberately bypassed the signed selector:
  `z(s,i)=p_i`, with no selector score, no selector gate, no empirical `mu_i`,
  and no full-bank aggregation. This was a causal-isolation diagnostic for the
  program/injector channel, not a replacement Stage-B field.
- Hard scope was respected: Qwen3-8B was frozen; selector was not trained;
  Stage-C1 full-bank training was not repeated; AppWorld
  generation/evaluation, Stage C2, and end-to-end RCMF training were not run.
- Pair-response cache validation passed. It selected `1,728` legal
  `(state, memory)` pairs from the effective 36 train-memory bank:
  `1,152` train and `576` state-held-out validation pairs. Category coverage
  was balanced at train `288/288/288/288` and validation
  `144/144/144/144` for positive/neutral/negative/random. Missing category
  slot count was `0`.
- The pair cache reused `88` compatible Stage-C1 rows and newly scored `1,640`
  rows. Cache validation reproduced L0/Lj/text utility, checked hashes and
  pair identity, preserved no-truncation and no-leakage rules, and verified
  top-K-plus-other probability normalization.
- Perturbation target `1.0` was selected from train-only smoke. The old
  unrestrained `7-8x` embedding-delta regime did not reappear; content mean
  delta ratio on validation was `1.054877`.
- Zero-program equivalence and tiny overfit both passed.
- State-held-out content metrics: target NLL `0.665915`, sparse teacher KL
  `0.318875`, behavioral-delta Huber `2.207829`, raw utility versus compiled
  utility Spearman `-0.293472`, positive/negative sign agreement `0.403382`,
  and improved fraction `0.447917`.
- Content controls showed no meaningful memory-specific advantage:
  content-minus-shuffled-program target NLL `-0.000166`,
  content-minus-memory-swap target NLL `-0.000081`, and
  content-minus-random-program sparse KL `+0.010575`.
- Memory-held-out 5-fold CV failed to show compiler generalization. Content
  u_text/u_program Spearman mean/std was `-0.189175/0.052868`, and `0/5`
  folds had positive Spearman.
- Content program geometry was highly collapsed across memories:
  centered effective rank `12.712268`, pairwise cosine mean `0.998634`, and
  norm mean `11.313701`.
- Decision branch:
  `program_injector_behavioral_channel_insufficient`.
- Pair-level memory grounding did not pass, and Stage C2 remains blocked.
- Lambda post-5D status: no active `stage5d_exp014` tmux session remained, GPU
  reported `0 MiB / 0%`, and the process is safe to terminate.

## 2026-08-08 Milestone 5E oracle pair-latent injector capacity

VERIFIED:

- Source commit:
  `c786a9735add6de640869f497013014a937b4c0a`.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_capacity_5e_20260808_001`.
- Milestone 5E diagnosed the additive-token injection channel only. Qwen3-8B
  was frozen; no signed selector, selector score/gate, empirical `mu_i`,
  full-bank aggregation, AppWorld generation/evaluation, Stage C2, or
  end-to-end RCMF training was used.
- Stage-5D pair cache validation remained valid for `1,728 / 1,728` pairs.
  Target-token teacher utility identity passed with maximum absolute error
  `1.001358e-06`.
- The direct-oracle validation subset had `192` pairs, balanced as
  positive/neutral/negative/random `48/48/48/48`, and covered all `36`
  effective train memories.
- Best K=4 direct DeltaE run:
  `target_delta_plus_sparse_kl_ratio_0.5`, with u_text/u_direct Spearman
  `0.641904`, sign agreement `0.776978`, target-token delta correlation
  `0.369083`, target-token delta Huber `0.573381`, target NLL `0.748773`,
  sparse KL `0.261206`, and mean perturbation ratio `0.488439`.
- The direct DeltaE gate failed: thresholds were Spearman `>=0.70`, sign
  agreement `>=0.80`, and target-token delta correlation `>=0.80` under
  ratio `<=2.0`.
- Optional K=8 direct DeltaE at ratio `2.0` also failed and did not improve
  the direct channel: Spearman `0.608854`, sign agreement `0.784173`, and
  target-token delta correlation `0.219402`.
- Objective ablation verified that target-token delta supervision is much
  better aligned with raw teacher utility than the old sparse behavioral-delta
  Huber objective: old sparse objective Spearman `-0.092488` versus
  target-token delta Huber Spearman `0.636335`.
- Frozen-injector validation pair-z inversion failed: Spearman `-0.069538`,
  sign agreement `0.467626`, target-token delta Huber `0.614797`, and mean
  perturbation ratio `0.015999`.
- Joint validation pair-z upper bound improved target-delta Huber to
  `0.493115`, but only with mean perturbation ratio `2.370567` and still
  failed the pair-latent gate.
- Free per-memory z produced weak positive Spearman `0.194337` and sign
  agreement `0.570048`, but it badly worsened target NLL `1.548202`, sparse
  KL `1.351795`, and target-token delta Huber `1.460607` versus the zero
  control. It is not a viable Stage-C repair.
- Decision branch: `direct_delta_fails`.
- Identified bottleneck:
  `additive_token_injection_location_bandwidth_or_behavioral_target`.
- Stage C2 remains blocked. The next step should diagnose/redesign injection
  location, decoder mechanics, and utility-aligned target-token objectives
  before returning to memory-content compiler training.
- Lambda post-5E status: the `stage5e_exp015` tmux session ended, no tmux
  server remains, GPU reported `0 MiB / 0%`, and the instance is safe to
  terminate.

## 2026-08-09 Milestone 5F-A convergence-corrected direct oracle

VERIFIED:

- Source commit:
  `451b7a763dd3ca0a08ff7cf430d2d2e5b16396c8`.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fa_20260808_001`.
- The Stage-5D cache revalidated for `1,728 / 1,728` pairs. The target-token
  teacher utility identity passed with maximum absolute error
  `1.001358e-06`.
- Zero DeltaE reproduced bare Qwen with maximum absolute utility error
  `1.192093e-07`.
- The 64-pair pilot was balanced positive/neutral/negative/random
  `16/16/16/16` and covered all `36` effective memories. It compared target
  delta Huber, sequence utility Huber, and sequence utility plus sparse KL.
- The train-only predetermined selection rule chose
  `sequence_utility_plus_sparse_kl` at 64 updates per pair because it reached
  the documented pilot plateau. The 192-pair confirmation did not change the
  selected objective.
- Ratio-0.5 confirmation at exactly 64 updates per pair reached Spearman
  `0.955679`, sign agreement `0.992806`, sequence Huber `0.103908`,
  target-delta correlation `0.818925`, and mean perturbation ratio `0.495321`.
- Ratio-1.0 confirmation at exactly 64 updates per pair reached Spearman
  `0.976238`, Pearson `0.975828`, sign agreement `1.0`, sequence Huber
  `0.054151`, target-delta correlation `0.820359`, sparse KL `0.166327`, and
  mean perturbation ratio `0.973289`.
- Ratio 1.0 reduced sequence Huber by `89.4905%` versus zero. Positive and
  negative utility-category sign rates were both `1.0`; neutral mean absolute
  student utility was `0.000131`.
- Matched-random ratio-1.0 DeltaE had Spearman `-0.001699`, sign agreement
  `0.532374`, and sequence Huber `0.516598`.
- Both ratio confirmations were still improving at u64. Ratio 1.0 improved
  Huber another `22.5965%` from u48 to u64, so it failed only the documented
  plateau check; all seven other direct utility-capacity checks passed.
- Decision branch: `oracle_not_converged_extend_updates`.
- Stage 5E's original direct result is preserved and now recorded as
  `underoptimized_two_update_result`. The Stage-5E direct capacity failure is
  superseded, while its sparse-objective mismatch evidence remains valid.
- Pair-z was not run because the direct gate requires a documented plateau.
- Formal runtime was `83,929.064 s`, approximately `23.3136 H100 hours`.
- Relevant tests passed locally and on Lambda: `35 passed` in each environment.
- Post-run status: no tmux server or active Stage-5F-A process, GPU
  `0 MiB / 0%`, safe to terminate.

## 2026-08-09 Milestone 5F-B direct-oracle convergence extension

VERIFIED:

- Final source commit used by Lambda:
  `02f13ec2bba7600441b565cd97884fc23f9fdbc9`.
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001`.
- The immutable Stage-5F-A ratio-1.0 checkpoint resumed with the exact ordered
  192-pair manifest, DeltaE parameters, 192 nonempty Adam states, learning rate
  `0.05`, and min/max/mean update counts `64/64/64.0`.
- Source checkpoint SHA256 was
  `26993056d9ac06d6fb43316fdd8ce4cc2557497d994a62500dbc6d16193ea840`;
  normalized source DeltaE SHA256 was
  `897db72059a5cb5e8a38beb28b618bc3a7906ce6b973e8d601bd685ce8150424`;
  ordered pair-manifest SHA256 was
  `b4868b7b384c099ed929dc1c8cb4d9db608843bdeab70717aaccfb57848f7c4d`.
- The loaded u64 evaluation reproduced the Stage-5F-A recorded metrics with
  maximum absolute difference `0.0` before any new update.
- A first pre-update launch at commit
  `b0037568a3decb8661c58630f73ad14c1fd539c6` aborted before model loading or
  u65 because two tensor-hash routines framed identical bytes differently.
  Commit `02f13ec...` made runtime hashing reproduce the immutable source-audit
  algorithm and added a regression test. No checkpoint or training state was
  modified by the aborted attempt.
- The formal continuation saved checkpoints at exactly u80, u96, u112, and
  u128. Every checkpoint had identical min/max/mean updates per pair equal to
  its checkpoint number.
- Sequence Huber / Spearman were: u64 `0.054151 / 0.976238`, u80
  `0.065346 / 0.957625`, u96 `0.034719 / 0.982333`, u112
  `0.029525 / 0.984810`, and u128 `0.034512 / 0.979465`.
- At u128, the pre-registered u112-u128 plateau inputs were relative Huber
  improvement `-0.1689106903` and Spearman improvement `-0.0053458074`.
  Both are `<0.01`, so the supplied formal plateau rule passed at the first
  eligible checkpoint. This rule also treats deterioration as less than 1%
  improvement: u128 Huber was `16.8911%` worse than the numerically best u112.
  The formal stop is compliant but is not evidence of monotonic convergence.
- Final u128 metrics were utility Spearman/Pearson
  `0.979465 / 0.982839`, sign agreement `0.992806`, sequence
  MAE/MSE/Huber `0.048244 / 0.019760 / 0.034512`, positive mean student
  utility `+0.667495`, negative mean `-0.753849`, and neutral mean absolute
  utility `0.000135`.
- Final target-token delta correlation/Huber were
  `0.875870 / 0.257348`; sparse teacher KL was `0.096836`; aggregate target
  NLL was `0.753194`. These are secondary fidelity metrics, not gate blockers.
- Mean/max perturbation ratio was `0.975180 / 1.0000001`; the tiny excess is
  within the documented numerical tolerance.
- Zero and matched-random sequence Hubers were `0.515256` and `0.515793`.
  Final Huber was `93.3019%` below zero. Final-minus-zero and
  final-minus-random paired-bootstrap Huber CIs were respectively
  `[-0.546267, -0.414451]` and `[-0.551062, -0.414347]`.
- Final-minus-u64 Huber CI was `[-0.046206, +0.005487]`; the numerical u128
  improvement over u64 is not statistically established by this bootstrap.
- All eight formal utility-capacity checks passed. Decision branch:
  `input_embedding_channel_capacity_passed_after_convergence`.
- Stage-5E's direct-channel failure remains superseded as an underoptimized
  two-update result. Stage-5E's sparse-objective mismatch remains valid.
- The final checkpoint has exactly 128 updates for every pair and is at
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_convergence_5fb_20260809_001/checkpoints/direct_sequence_utility_plus_sparse_kl_ratio1.0_u128.pt`.
- Tests passed locally and on Lambda: `36 passed` in each environment.
  Independent post-run validation found `0` errors.
- Formal runtime was `23,495.135 s`, approximately `6.5264 H100 hours`.
- No pair-z, injector-decoder, compiler, selector, Stage C2, AppWorld
  generation/evaluation, or end-to-end training was run.
- Post-run status: no tmux server or active EXP-016B process, GPU
  `0 MiB / 0%`, safe to terminate.

### EXP-016C shared-decoder capacity audit

- EXP-016C completed at source commit
  `95be149e26598546327c33e8207c1c4f833130aa` in the existing artifact
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/oracle_decoder_5fc_20260810_003`.
- The primary u112 and robustness u128 DeltaE sources both contain exactly 192
  ordered tensors of shape `[4,4096]`; their metric-reproduction maximum
  delta was `0`.
- The deterministic state-grouped manifest contains 57 states and 36 memories.
  Each of three folds has 128 train and 64 held-out pairs, no state leakage,
  complete 36-memory train coverage, and every pair held out exactly once.
- Uncentered effective rank was `179.6761` for u112 and `179.8787` for u128.
  Rank 128 retained `84.5709% / 84.4404%` of squared norm and passed the global
  low-rank behavioral gate on both targets. Rank 192 exactly reproduced both
  source tensors and Qwen behavior.
- Every linear and MLP train-fold tensor reconstruction completed. Linear
  relative Frobenius error ranged from `8.30e-6` to `1.57e-5`; MLP error ranged
  from `5.90e-4` to `1.88e-3`.
- Pooled u112 frozen-linear held-out inversion reached utility Spearman
  `0.988537`, sign agreement `0.992806`, and sequence Huber `0.027538`.
  Pooled u128 reached `0.994685 / 1.000000 / 0.015615`.
- Frozen MLP reached u112/u128 sequence Huber `0.065315 / 0.097231`; joint MLP
  reached `0.034243 / 0.051242`. All trainable paths decisively beat zero and
  matched-random controls.
- Frozen linear was positive in all three folds and passed every numerical
  capacity threshold on both targets. Its u128 Huber was `96.9694%` below
  zero, and its frozen decoder hashes were unchanged during held-out z
  inversion.
- No frozen or joint path reached the corrected documented plateau in all
  three folds. Therefore the formal gate did not pass despite the strong
  numerical capacity evidence. Decision branch:
  `shared_decoder_optimization_or_generalization_failure`.
- Tensor reconstruction is effectively solved. The unresolved gate is
  held-out Qwen inversion convergence/generalization; current evidence does
  not identify global rank 128 as insufficient.
- The fold-2 frozen-MLP paths stopped normally at u64 under the preregistered
  continuation rule because Huber deteriorated beyond the best-value guard.
  Every other path continued to u128, with exact per-pair update counts.
- Related EXP-016C tests passed locally (`54 passed, 1 skipped`) and on Lambda
  CUDA (`55 passed`). Final full suites passed locally (`143 passed, 1
  skipped`) and on Lambda (`144 passed`). Independent post-run audit passed
  with `0` errors.
- Qwen remained frozen. No compiler, selector, full-bank model, Stage C2,
  AppWorld generation/evaluation, or end-to-end RCMF training was run.
- Final status: no tmux server or EXP-016C Python process; GPU `0 MiB / 0%`;
  safe to terminate.

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
- Milestone 4C indicates the immediate Stage-B bottleneck was the old
  nonnegative/top-k/dense-softmax address parameterization rather than the
  absence of trainable state-memory signal.
- The signed continuous residual field is now the best-supported Stage-B
  addressing design for the next milestone, with rank128 retained as the
  conservative default despite rank64 also passing the simple improvement
  check.
- Stage C1 shows that a teacher-forced additive-token/program path can learn
  to reduce validation target NLL relative to bare Qwen, but the behavior is
  not yet credible as memory-content compilation: it fails the free-ID
  comparison, no-positive preservation, and behavioral leave-one-out checks.
- Corrected Stage-C1 leave-one-out effects are nonzero but very small. The
  stronger issue is now selector-teacher mismatch: the raw-teacher-best memory
  often receives low signed-selector rank, and the selector-top memory has
  larger compiled behavioral effect than the raw-teacher-best memory.
- The Stage-C1 program/injector path still does not demonstrate memory-content
  causality, because compiled all-memory effects barely correlate with raw
  teacher utility.
- Milestone 5C shows that top-utility listwise selector repair can improve
  teacher-best Recall@8 and preserve state-dependent NDCG, but this alone does
  not solve raw-utility alignment: Recall@4, signed-score calibration,
  utility-score Spearman, and projection causality remain insufficient.
- Milestone 5F-B formally establishes that K=4 `last_user_k` input-embedding
  perturbations have sufficient direct-oracle sequence-utility capacity under
  a ratio-1.0 budget. The direct-channel failure observed in Stage 5E was due
  to underoptimization and objective mismatch, so current evidence does not
  justify changing the injection site.
- The u112-to-u128 behavior shows that the registered plateau predicate is a
  stopping-rule result, not proof of a monotonic optimum. u112 is numerically
  better on sequence utility, while u128 is the pre-registered gate checkpoint.
- EXP-016C indicates that a shared 128D linear decoder can express the direct
  utility signal after pair-specific held-out inversion: it beats zero/random
  controls in every fold and matches or exceeds direct-oracle utility metrics
  when pooled. Because inversion had not plateaued, this remains capacity
  evidence rather than a completed shared-decoder scientific gate.
- The current no-bias MLP can fit train-fold DeltaE tensors nearly exactly but
  is weaker and less stable than the SVD-initialized linear decoder during
  held-out Qwen inversion. This points to optimization/generalization through
  the decoder manifold, not train-tensor reconstruction, as the immediate
  bottleneck.
- The average target NLL is not the direct-capacity criterion on this balanced
  diagnostic because negative-teacher pairs intentionally supervise harmful
  utility. Sign, sequence-utility error, and matched controls are the relevant
  oracle evidence.

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
- Whether a selector repaired against raw-teacher-best utility can make
  memory-specific leave-one-out effects track teacher utility; Milestone 5C
  improved LOO magnitude but still showed weak utility/effect correlation.
- Whether an additive-token injector can use a signed-program memory field
  without degrading Qwen generated AppWorld action trajectories; Stage C1 used
  teacher-forced scoring only and no AppWorld generation/evaluation.
- Whether frozen-linear held-out z inversion reaches the corrected plateau if
  resumed beyond u128. EXP-016C shows strong 128D shared-decoder capacity but
  did not formally pass because no path reached plateau in all three folds.
- Whether a shared 128D decoder can be mapped from memory content remains
  untested. EXP-016C uses free held-out pair latents and does not train a
  memory compiler.
- Whether a later-layer residual injection site would improve deployable
  performance remains untested, but EXP-016B gives no scientific reason to
  redesign the current input-embedding site before testing the decoder.

## Immediate Workflow Status

- Working branch: `research/v4-decision-transition-memory`.
- EXP-019 final source/audit commit before final records:
  `5ca600bf76fcdb9db5b0278c60a31dc35b6a7128`.
- Lambda cannot currently pull GitHub directly because the instance has no
  GitHub private key/deploy key; sync used a local git bundle after pushing to
  GitHub.
- Lambda post-EXP-019 status: no tmux server running and GPU
  memory/utilization reported `0 MiB / 0%`.
- Do not launch Stage C2, compiler training, selector work, Qwen action loss,
  full-bank end-to-end RCMF training, AppWorld agent evaluation, or an
  injection-site redesign. The next separately reviewed experiment should
  expand query-state teacher coverage, rerun the interaction upper bound and
  field-compatible gate, and stop again for review before any behavioral
  `p(s,m_transition)` training.

### EXP-017 decision-transition memory pilot

- RCMF V3 is frozen at documentation commit
  `2eb1281ff66792aeb082cce39f6a362697f132e6` with annotated tag
  `rcmf-v3-component-validated-pre-transition` and archive branch
  `archive/rcmf-v3-component-validated`. The source state is
  `97ca723ad66597d2afcbbce1eb5466eb34c009f6`.
- V4-candidate work remains isolated on
  `research/v4-decision-transition-memory`; no V4 tag exists and nothing was
  merged into `workflow/research-loop`.
- EXP-017 completed at source commit
  `88f9da7be7bcf6380d9df8ba1ce75b78bc14f9b6` in
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/transition_memory_6a_20260814_001`.
- Exact counts: 37 train parent trajectories, 499 extracted transitions, 148
  panel transitions, 32 query states, 4,640 legal pairs, 4,579 scoreable
  pairs, and 61 over-context masked pairs. Nothing was truncated.
- The raw-transition teacher passed: positive/neutral/negative utility counts
  were 2,271/941/1,367 and mean utility was 0.059851. Exact target-copy and
  length/overlap diagnostics did not explain the signal.
- Best child transition utility exceeded matched whole-parent utility in
  885/1,120 comparisons. Helpful parents contained 1.958 helpful transitions
  on average, while 218 helpful-parent groups also contained a harmful child.
- The 64-pair frozen-linear oracle passed with Spearman 0.957418, sign
  agreement 0.976744, and sequence Huber 0.028181 versus zero 0.105161.
- A fixed latent per transition failed held-out states: Spearman 0.123261,
  sign 0.543103, and Huber 0.052237 versus zero 0.028513. Correct latents were
  significantly worse than zero and random, and all four held-out tasks failed.
- The whole-trajectory static baseline also failed. Transition granularity won
  only one of five material comparisons and was not validated.
- Final branch: `static_transition_program_insufficient`. Pair-specific
  effects are reachable, but one state-independent program per transition is
  insufficient. The next reviewed experiment should test an explicitly
  state-conditioned transition program rather than a larger static encoder.
- Independent validation had zero errors. No tmux/process is active; GPU is
  0 MiB / 0%; the instance is safe to terminate after final Git sync.
- EXP-016D was not launched. No selector, content compiler, full-bank model,
  Stage C2, AppWorld generation/evaluation, Qwen fine-tuning, or end-to-end
  RCMF training occurred in EXP-017.

### EXP-018 state-conditioned transition representation gate

- EXP-018 source commit is
  `0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c`; artifact root is
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_transition_6b_20260814_001`.
- Immutable EXP-017 reuse validation passed: 499 transitions, 148 panel
  transitions, 32 queries, 4,640 legal rows, 4,579 scoreable rows, and 61
  over-context masked rows. There was no leakage, truncation, duplicate key,
  target-utility mismatch, or L0 inconsistency.
- Frozen query and transition representation tensors have shapes `[32,4096]`
  and `[148,4096]`; all query targets were excluded and all validation parent
  trajectories remained excluded.
- The deterministic two-axis split contains 29 train and 8 held-out transition
  parents. Pair counts are A/B/C/D = 2,667/904/752/256 for train/train,
  held-out-state/train-transition, train-state/held-out-transition, and double
  held-out.
- Cell-A-only grouped CV selected all predictor schedules. No B/C/D label was
  used for hyperparameter selection.
- On double-held-out D, the concat-MLP upper bound reached Spearman 0.059482,
  sign agreement 0.758170, and Huber 0.126287. State-only was better on
  Spearman (0.205547) and Huber (0.052382); transition-only Huber was 0.026967.
- Transition shuffling barely affected concat-MLP behavior on D: Spearman
  0.059482 to 0.043260 and Huber 0.126287 to 0.126444. The upper bound failed
  the registered Spearman, baseline, and transition-shuffle checks.
- Signed bilinear D metrics were Spearman 0.111083, sign agreement 0.575163,
  and Huber 0.062106, so the field-compatible interaction also failed.
- Final branch: `state_transition_representations_insufficient`. Per the
  preregistered decision tree, Parts E-G and the optional trajectory control
  were not run. This is a failed representation precondition, not a measured
  behavioral failure of the untrained factorized program.
- Future-compatible V0/T compiled-field equivalence, transition add/remove,
  replacement, parent removal/restoration, arbitrary order, and fixed runtime
  shape tests all passed. No production field or q/k model was trained.
- The append-only ledger contains one normal attempt, `attempt-001`; the
  network/Codex disconnect caused no duplicate or resume. Runtime was 238.5433
  seconds and the 2.159-GiB artifact passed independent validation with zero
  errors.
- Tests passed locally (`169 passed, 1 skipped`) and on Lambda (`170 passed`).
  No selector, program/injector, Qwen behavioral backpropagation, full bank,
  Stage C2, AppWorld evaluation, end-to-end run, or V4 tag occurred.

### EXP-019 interaction-residual and multi-view representation repair

- EXP-019 final source/audit commit before records is
  `5ca600bf76fcdb9db5b0278c60a31dc35b6a7128`; the last experiment-runner fix
  is `cbb75d474e01ee19e35a35d76814b8c63f1efdc7`.
- The immutable EXP-018 manifest and 4,579 scoreable rows were reused. Exact
  A/B/C/D counts remain `2,667/904/752/256`; 61 over-context rows remain
  masked, with no truncation or leakage.
- Majority-sign accuracy is `0.584676/0.726126/0.644550/0.764706` for A/B/C/D.
  D's value is exactly `117/(117+36)`, so sign agreement near 0.764706 is not
  treated as learned interaction.
- Cell A has total utility variance `0.109951`. State and transition main
  effects explain `0.412241` and `0.035677`; their additive model explains
  `0.449074`, leaving residual variance `0.060575`. Utility/residual effective
  ranks are `14.453/16.097`.
- The original-vector objective repair failed. On D, decomposed signed
  bilinear reached raw/per-state/residual Spearman
  `0.166696/0.050357/0.129785` and NDCG@4 `0.484875`. Its state-shuffle drop
  was only `0.012744`, the transition-shuffle bootstrap CI included zero, and
  only one of four held-out tasks was positive.
- The span-aware cache contains state `[32,10,4096]` and transition
  `[148,10,4096]` tensors for final-layer and mean-final-four-layer readouts.
  All 900 spans were source-aligned; 439 expanded only to tokenizer
  boundaries. There was no target-action access or truncation.
- The best field-compatible multi-view model was the low-rank tensor. On D it
  reached NDCG@4 `0.554092`, per-state Spearman `0.121518`, and residual
  Spearman `0.188452`, but transition-only NDCG@4 was higher (`0.566808`),
  the state-shuffle drop was only `0.020493`, and only one of four held-out
  tasks was positive. It failed the gate.
- Multi-view pair MLP and structured-feature controls also failed. Structured
  features did not establish a signal that frozen-Qwen pooling alone missed.
- The prompt-only frozen-Qwen cross-encoder cached all 4,579 legal scoreable
  pairs without truncation. Its aggregate shape is `[4579,12288]` and SHA256
  is `d40f4e2dfc516a02bb4066f195a27e6e3612a344a6761858787aefe86bc1c763`.
- The cross-encoder fit cell A (NDCG@4 `0.820228`) and generalized to C
  (`0.661463`) but failed B (`0.309609`) and D (`0.379564`). On D,
  transition shuffling improved NDCG@4 to `0.495154`; only one of four tasks
  showed positive relative behavior.
- Five-fold cell-A learning curves covered 4, 8, and all 12 train query tasks.
  The prompt cross-encoder moved from NDCG@4 `0.426066` at 8 tasks to
  `0.431144` at 12 while residual Spearman moved from `-0.151063` to
  `0.003225`; results remained unstable across folds.
- Final decision branch: `query_task_coverage_insufficient`. The
  representation gate was not repaired, and behavioral
  `p(s,m_transition)` remains blocked.
- A future 64-query cache projects to `9,280` legal / `9,158` scoreable /
  `122` over-context pairs and `4.5510` H100 hours. A 96-query cache projects
  to `13,920/13,737/183` pairs and `6.8265` H100 hours. Neither was launched.
- The append-only ledger contains nine paired attempts: four normal phase
  completions and five preserved implementation failures. Every attempt says
  `scientific_parameter_changed=false`; atomic cross-encoder rows were reused
  without duplication after recovery.
- Independent validation passed with zero errors, including immutable hashes,
  all cache/prediction/checkpoint hashes, 105 learning-curve rows, and the
  attempt ledger. Full local tests passed `204 passed, 1 skipped`; strengthened
  Lambda validator tests passed `6 passed`.
- No behavioral program, injector, selector, production field, Qwen
  behavioral backpropagation, Stage C2, end-to-end RCMF, AppWorld evaluation,
  demo change, or V4 tag occurred. No tmux/process is active; GPU is
  `0 MiB / 0%`; Lambda is safe to terminate after final Git synchronization.

### EXP-020 all-task query-coverage interaction test

- EXP-020 source/audit commit is
  `886cf2134599e8243d96d3e9fd497c661ae3e3c3`; artifact root is
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/all_task_interaction_6d_20260815_001`.
- The deterministic query manifest contains 92 states: 74 train states from
  all 37 train tasks and 18 heldout states from all 9 validation tasks. Every
  task supplies exactly two early/later states, and all original 32 states are
  an exact subset.
- The immutable 148-transition panel and 29/8 parent split produce 13,616
  Cartesian pairs, 296 illegal leakage pairs, 13,320 legal pairs, 13,128
  scoreable rows, and 192 over-context rows. Nothing was truncated.
- All 4,640 EXP-017 rows were strictly validated and reused. The run newly
  scored 8,549 rows; the completed cache contains 6,332 positive, 2,102
  neutral, and 4,694 negative utilities.
- Expanded A/B/C/D scoreable counts are `8,205/2,051/2,296/576`.
- On the fixed 9-task heldout set, prompt cross-encoder D NDCG@4 increases
  `.360914 -> .461709 -> .523176` across LC12/LC24/LC37. This is materially
  increasing, so coverage mattered, but the LC37 gate still fails.
- At LC37 the cross-encoder has D per-state/residual Spearman
  `.117526/.063559`, state/transition-shuffle drops `.017769/.062644`, a
  transition-shuffle bootstrap CI that includes zero, and positive relative
  behavior on only 5/9 tasks.
- The low-rank field reaches D NDCG@4 `.510827`, per-state/residual Spearman
  `.134551/.439346`, and state/transition-shuffle drops `.175431/.114643`.
  It retains 71.22% of the cross-encoder gain, but only 4/9 tasks are positive
  and its transition-shuffle CI includes zero.
- The optional 638-state action-intent probe succeeds: correct-state mean
  accuracy `.875899`, shuffled `.420863`, majority `.559353`. State decision
  intent is available; the raw-transition target-NLL utility interaction is
  the more specific generalization problem.
- Primary branch: `query_task_coverage_still_data_limited`. Final diagnostic
  branch: `state_intent_available_but_memory_utility_target_not_generalizing`.
  The representation gate remains failed and behavioral
  `p(s,m_transition)` remains blocked.
- Actual H100-active runtime was `12.373621` hours and the final artifact
  directory size, including validation outputs, was `33,258,921,348` bytes
  (`30.9748 GiB`). The full design ran unchanged; the
  12-hour value remained a review threshold rather than a compute cap.
- Ten attempt start/end pairs are preserved: seven completed and three failed.
  All used one run UUID and record `scientific_parameter_changed=false`.
  Atomic rows and checkpoints were resumed without duplication after fixes to
  launcher provenance, strict tokenizer loading, and cross-cache shape
  normalization.
- Independent validation passed with zero errors. Tests passed locally
  (`221 passed, 1 skipped`) and on Lambda (`17 passed`). No tmux/process is
  active; GPU is `0 MiB / 0%`; Lambda is safe to terminate after final Git
  synchronization.
- No behavioral program, injector, selector change, production field, Qwen
  behavioral backpropagation, Stage C2, end-to-end RCMF, AppWorld evaluation,
  demo change, or V4 tag occurred.

### EXP-021 relative and intent-conditioned target audit

- EXP-021 source/audit commit is
  `3995b3cfdffdfc700846d2dec928cf5f7574e6fd`; artifact root is
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/memory_use_target_6e_20260816_001`.
- All EXP-020 inputs remained immutable: 92 queries, 148 transitions, 13,320
  legal rows, 13,128 scoreable rows, 192 masked over-context rows, and
  A/B/C/D counts `8,205/2,051/2,296/576`.
- The 192-pair serialization audit passed. Median cross-template Spearman was
  `.833893`, sign agreement `.892081`, and mean per-state top-4 overlap
  `.966667`. The 381 new Qwen forwards took `.167312 H100 h`; no input was
  truncated.
- Cell-A raw utility variance is `.094417`; additive state/transition main
  effects explain `.434646`, leaving residual variance `.053379`. Raw and
  residual effective ranks are `35.879/41.605`.
- Train-only intent predictions remain strong on held-out queries: app/API/
  action-type accuracies are `.944444/.833333/.888889`, versus shuffled
  `.277778/.222222/.500000`. Completion is noninformative because all held-out
  completion labels are false.
- Five-fold A-only grouped CV selected T4 gap-weighted pairwise preference
  before B/C/D evaluation. On D, the selected field reaches NDCG@4 `.433983`,
  per-state Spearman `.117200`, residual Spearman `-.051060`, and gap accuracy
  `.583228`.
- The immutable EXP-020 transition-only D NDCG@4 is `.480274`. T4 field has
  state/transition-shuffle drops `.072329/.094174`; the transition-shuffle
  bootstrap CI includes zero, and only 4/9 tasks beat their own locked
  baseline.
- Oracle and predicted intent D NDCG@4 are `.337362/.344384`, both below the
  locked transition-only comparator. Transition content adds some isolated
  gain over intent, but no fixed target passes the joint field-compatible gate.
- The formal branch is
  `raw_nll_memory_use_target_not_deployably_predictable`. A prior generated
  branch, `revised_target_learnable_but_field_factorization_insufficient`, is
  superseded because its record evaluator used the wrong transition baseline,
  bootstrap key, and per-task comparator. Old records are preserved; no model,
  prediction, or cache was changed.
- Independent validation passed 20/20 checks with no errors. The append-only
  ledger has 17 rows, one run UUID, and no scientific parameter changes.
- Successful serialization/model work used `2.147676 H100 h`; including the
  preserved interrupted scalar-loss attempt gives approximately `2.504051`
  H100 h. The final artifact is about `7.05 GB` (`6.57 GiB`).
- Behavioral `p(s,m_transition)` remains blocked. No program, injector,
  selector, production field, Qwen behavioral backpropagation, Stage C2,
  AppWorld evaluation, demo change, or V4 tag occurred.

### EXP-022 procedural/outcome supervision audit

- EXP-022 source/validation commit is
  `1c9ed7fca9517e0cf75b5589862d60674a17c4da`; artifact root is
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_outcome_6f_20260816_001`.
- All 638 successful query actions and all 148 fixed-panel transition actions
  parsed by AST. No action was dropped, all query targets matched the source
  successful trajectories, and no raw credential value leaked into a
  signature.
- The compiler produced 13,128 unique procedural labels with unchanged
  A/B/C/D counts `8,205/2,051/2,296/576`.
- Tier-3/4 state coverage is A/B/C/D `60/74, 12/18, 41/74, 9/18`.
- The registered B coverage gate failed: `12/18 = 66.6667% < 70%`.
- Independent validation passed 20/20 checks. Local tests passed 254 with one
  skip; Lambda focused tests passed 16 and the final Lambda full suite passed
  255.
- No model, Qwen forward, AppWorld instance, replay, generation, or H100 work
  ran after the stop decision.
- The branch is `transition_panel_procedural_coverage_insufficient`.
  Behavioral `p(s,m_transition)` remains blocked. B/C/D field and behavioral
  metrics are intentionally absent because the protocol stopped before model
  training.
- A separately reviewed next milestone may expand the panel from 148 to all
  499 training transitions, but must preflight exact leakage, context,
  coverage, pair count, runtime, and artifact size before scoring or training.
- Four append-only attempts used one run UUID. Two exposed UUID false positives
  in an overbroad credential scan; one completed outputs before a bookkeeping
  API error; the tested fourth attempt completed. No scientific parameter
  changed and no disconnect created a duplicate run.
- Raw-NLL remains an immutable secondary measurement. V4 remains a candidate;
  no V4 tag was created or moved.

### EXP-023 full-transition procedural coverage preflight

- EXP-023 execution source is
  `59e1f15b733a3259727b0631265207f0c9354344`; the independent-validator fix is
  `3c5ed9171cd9ba3e9882673752846edc09b02fb4`. Artifact root is
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/procedural_coverage_6g_20260816_001`.
- All 499 transitions parsed by AST with zero fallback, zero credential
  leakage, and exact agreement for all 148 old-panel signatures. They form 150
  unique canonical signatures; 349 transitions belong to 54 duplicate groups,
  and 210/499 (42.08%) are API-documentation actions.
- The 92-query x 499-transition product contains 45,908 pairs: 998 illegal,
  44,910 legal, 43,415 scoreable, and 1,495 over context. Nothing was
  truncated. Legal A/B/C/D counts are `29,736/7,434/6,192/1,548`; scoreable
  counts are `28,582/7,112/6,173/1,548`.
- Old-panel B/D/E Tier-3/4 coverage is `12/18, 9/18, 12/18`; none of the six
  original B gaps is repaired by D alone. The old panel is globally
  insufficient rather than merely parent-split limited.
- Full-bank A/B/C/D/E Tier-3/4 coverage is
  `70/74, 17/18, 55/74, 12/18, 17/18`. Exact-API coverage is
  `73/74, 18/18, 63/74, 15/18, 18/18`.
- Nominal B coverage clears the original 70% criterion, but B/E diverse
  coverage is only 10/18 under the fixed requirement of at least two high-tier
  signatures and two source parents. Seven B states have one high-tier
  signature, two have one high-tier parent, and six are covered only by API
  documentation.
- Context missingness is 3.3289%; one parent causes 1,192/1,495 over-context
  pairs. No state's only Tier-3/4 candidates are over context.
- The preflighted 45-state one-step design has 21,624/22,455 scoreable pairs
  and 302 available conditions. No Qwen, AppWorld, model, or execution work ran.
- The projected required EXP-024 cost is `6.702/13.837/27.599` best/expected/
  conservative H100 hours and 2.77 GiB; an optional cross-encoder raises the
  expected total to 27.252 H100 hours and storage to 3.96 GiB. Explicit
  >12-hour approval is required before launch.
- Decision branch: `nominal_procedural_coverage_lacks_diversity`. The nominal
  coverage gate passed, but the full scientific coverage/diversity gate did
  not. Procedural field training, one-step behavior, and behavioral
  `p(s,m_transition)` remain blocked pending review.
- The one scientific attempt completed normally with no parameter change.
  Independent validation passed 24/24 checks after a validator-only path/hash
  fix; prior scientific artifacts were not rewritten. H100 use was zero,
  runtime was 4,663.52 seconds, and the artifact is 402,387,856 bytes.
- V4 remains a candidate; no V4 tag was created or moved.

## 2026-08-17 EXP-024A replay-gate stop

VERIFIED:

- The corrected signature-balanced preflight covers 45 immutable audit states
  across all 9 held-out tasks. Strata A/B/C/D/E are `9/23/12/1/0`; the primary
  non-documentation Tier-3/4 set has 32 states across 9 tasks.
- The complete 499-transition bank forms 150 signature classes. There are 349
  transitions in 54 duplicate classes and 210 API-documentation transitions.
- The immutable manifest contains 323 conditions: C0-C5 45 each, C6 37, C7 15,
  and C8 1. No prompt or transition was truncated.
- Exact isolated AppWorld replay passed `0/45`. Complete histories matched for
  `2/45`; target observations matched for `23/45`; and only `81/372` history
  observations matched. Both zero-history states failed their target step.
- All 9 official source trajectories record AppWorld code/data/evaluation
  version `0.1.0`. Lambda runs AppWorld `0.2.0.dev0` from upstream commit
  `a072b7a86e7c1d5b1d7175659d750ebb9b79f10a`.
- No Qwen generation or candidate action execution ran. Actual H100 use was
  zero. Post-run validation passed `132/132`, the Lambda full suite passed
  `282`, and final artifact size is 48,298,504 bytes.

INFERENCE:

- The AppWorld code/data contract mismatch is the leading cause candidate for
  the replay divergence. A matched-version replay is required before treating
  this as verified causality.

Decision:

- Stop with `appworld_one_step_replay_invalid`.
- Preserve all prior artifacts and the four-attempt append-only ledger. Do not
  reinterpret missing behavioral metrics as zeros.
- Reproduce AppWorld 0.1.0 in an isolated, pinned environment and rerun only
  exact replay validation. EXP-024A generation remains blocked until 45/45
  states pass.
- Behavioral `p(s,m_transition)`, the procedural field, injector, selector,
  Stage C2, and AppWorld task evaluation remain blocked. V4 remains a
  candidate; no V4 tag was created or moved.

### EXP-024R exact AppWorld 0.1.0 replay validation

- The isolated AppWorld package/code/data/evaluation triple is exactly
  `0.1.0/0.1.0/0.1.0`. Official verification passes 138/138 tests and 147/147
  tasks without modifying the existing 0.2.0.dev0 environment.
- The corrected 13-state sentinel passes 3/13 states, 5/13 complete histories,
  93/102 prior observations, and 11/13 targets. Both no-history states pass.
- On the same 13 states, 0.2.0.dev0 had 0/13 complete, 2/13 histories, 27/102
  prior observations, and 6/13 targets. Exact 0.1.0 is materially closer but
  does not pass the exact gate.
- All 11 locked-normalization differences are authentication JWT timing
  differences: ten expiration deltas are 191 seconds and one is 834 seconds.
  The locked normalization was not changed.
- One source query/task identity mismatch remains at
  `appworld:trace:b0a8eae_2:step:7:line:284`; task/instruction/DB match, but
  the supervisor identity hashes differ.
- The formal branch is
  `appworld_010_execution_semantics_or_normalization_mismatch`. Full 45-state
  replay was not run, version mismatch was not causally confirmed as the sole
  cause, and EXP-024A generation remains blocked.
- Eight append-only attempts preserve all infrastructure and validator
  recoveries. Post-run validation passed every check. H100 use was zero; the
  artifact is 6,277,720 bytes and the external capsule root is 176,204,608
  bytes.
- No Qwen import/forward/generation, memory condition, field/program/injector/
  selector training, Stage C2, environment evaluation, or V4 tag occurred.

### EXP-024R2 JWT semantic replay and identity provenance audit

- EXP-024R2 source/validator commit is
  `ad6ce7f110d147f05abac8ce9b1080ea2f151cde`; artifact root is
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_semantic_replay_6h2_20260817_001`.
- AppWorld 0.1.0 uses HS256 JWTs with `sub=<app>+<username>` and only an `exp`
  time claim, generated from a UTC clock with a random `[600,1800)`-second
  lifetime. No `iat`, `nbf`, or `jti` generation was found.
- The prospective semantic-v2 contract passed its component gate: all 11
  expected/actual JWT pairs match on header and stable claims, both validate
  through installed AppWorld, actual tokens work for subsequent recorded
  calls, and non-temporal mismatches are zero. Expiration deltas are ten at
  191 seconds and one at 834 seconds.
- The all-45 identity audit passes 40/45. The five failures are steps
  `6/7/12/17/18` from `b0a8eae_2`; decision text, raw trajectory, and replay
  contract agree, but all four supervisor identity hashes disagree with both
  retained official 0.1.0 task snapshots. No matching immutable snapshot was
  found.
- Decision branch: `source_query_task_identity_snapshot_unresolved`. The fixed
  semantic-v2 sentinel and full 45-state replay were not run under the strict
  identity gate. Semantic replay is not validated and EXP-024A generation
  remains blocked.
- Six append-only attempts preserve four preflight infrastructure failures and
  two normal completions under one run UUID; no scientific parameter changed
  and the validated identity probe was reused without a duplicate run.
- Postrun validation passed 23/23 checks. Local tests passed 328 with one skip;
  Lambda focused tests passed 30. H100/Qwen use was zero and the artifact is
  341,695 bytes.
- Behavioral `p(s,m_transition)`, field/program/injector/selector training,
  Stage C2, end-to-end RCMF, AppWorld generation, and V4 tagging remain
  blocked.

### EXP-024R3 corpus provenance audit stopped on two inconsistent tasks

- The complete provenance audit accounts for all `46` successful trajectories
  and all `638` decision examples. Identity matches `44/46` tasks.
- The mismatched tasks are `b0a8eae_2` and `b0a8eae_3`; both source layers
  agree internally but disagree with both agreeing official 0.1.0 snapshots
  on first name, last name, email, and phone.
- `b0a8eae_2` is classified as `source_query_header_only_corruption`: its
  trajectory behavior references the official identity five times and the
  source-query identity zero times. One matching task spec exists but is not
  a coherent historical trajectory snapshot.
- The bounded immutable-resource search found no exact coherent historical
  snapshot: `exact_historical_snapshot_not_found`.
- `b0a8eae_2` is held-out-only. `b0a8eae_3` contaminates the Stage-B train
  split, train labels, teacher-source memories, and EXP-017 transition parents.
- Decision branch: `source_dataset_identity_consistency_failure`. No
  quarantine, sentinel, 45/40-state replay, Qwen work, or sensitivity
  recomputation ran. Postrun validation passed `28/28`.
- EXP-024A generation and all behavioral/model work remain blocked. The next
  review must reconcile corpus identity and train-side contamination. V4
  remains a candidate; no tag was created or moved.

### EXP-025A identity-reconciled structural corpus and replay stop

- The exact ingestion defect was reproduced: archived actions/observations
  were paired with query headers rebuilt from an unpinned active AppWorld task
  snapshot.
- Both `b0a8eae_2` and train-side `b0a8eae_3` are verified
  `source_query_header_only_corruption`. Official-header semantic replay passes
  all `18/18` and `17/17` decision states respectively, with zero exceptions
  and zero non-temporal mismatches.
- The pre-registered policy repairs both headers. The new structural candidate
  contains `46` tasks (`37/9`), `638` decisions, and `638` transitions with
  lineage `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`.
  All structural checks pass and recorded actions/observations are unchanged.
- The dependency graph covers `27` artifacts. Minimum recomputation is `3,658`
  Qwen scoring rows plus `35/2/17` state/memory/transition representations;
  train-influenced checkpoints require retraining and are not declared clean.
- Contaminated-checkpoint sensitivity changes no prior decision branch. The
  EXP-022 fixed-panel coverage point is fragile, but no blocked interaction
  gate passes after filtering.
- The corrected 13-state sentinel passes twice. Full replay passes identity
  `45/45`, targets `45/45`, prior observations `369/372`, and complete states
  `42/45`. Three Spotify login observations are root-level JWTs differing only
  in `exp`, outside the frozen semantic-v2 `access_token` field contract.
- Decision branch: `identity_reconciled_corpus_replay_failure`. The structural
  candidate is ready; a formally replay-validated clean corpus is not.
  Generation, Qwen/cache recomputation, model training, Stage C2, and V4
  tagging remain blocked.

### EXP-025B replay-validated clean data contract

- The prospective `appworld_observation_semantic_normalization_7b_v1` adds
  only root path `$` for an AST-verified single `apis.<app>.login(...)` call.
  Both JWTs must validate under AppWorld 0.1.0, headers and stable claims must
  match, `exp` must be the only changed claim, and subsequent recorded
  authenticated calls must accept the live token.
- The corrected 13-state sentinel passes twice at identity/history/target
  `13/13`, prior observations `102/102`, zero exceptions, and exact semantic
  repeat equivalence.
- The fixed three-state schema-extension sentinel passes twice. Locked v2
  remains history `0/3` and prior observations `17/20`; prospective v3 reaches
  history `3/3`, prior observations `20/20`, and complete replay `3/3` with
  exactly three root-login-JWT extensions per repeat.
- The reconciled 45-state audit passes twice under v3: identity/history/target/
  complete replay are each `45/45`, prior observations are `372/372`,
  exceptions and non-temporal JWT mismatches are zero, and all state/database
  fingerprints repeat exactly. Locked v2 remains `42/45` histories and
  `369/372` prior observations.
- Record `identity_reconciled_replay_validated`. The structural lineage remains
  `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`;
  historical artifacts remain immutable. The minimum incremental clean-cache
  rebuild is now allowed, while every model-training path and V4 tagging remain
  blocked.

### EXP-025B clean-corpus oracle one-step causal audit

- The incremental clean rebuild completed without retraining any checkpoint.
  Final clean caches recomputed `35/2/17` state/memory/source-transition
  representations and `2,781/162/324/696` raw-teacher, Stage-C1, Pair-5D, and
  transition-teacher rows. The final Qwen-scoring total was `3,963` rows; the
  additional `305` above the direct preflight count came from the required
  downstream teacher-condition cascade.
- All merged-cache validation passed: zero duplicate keys, zero truncation,
  exact unaffected-row identity, correct leakage masks, reconciled lineage on
  every new row, and no superseded `b0a8eae_3` transition ID.
- The clean causal manifest contains `45` states, `9` tasks, `499` train-bank
  transitions, `150` procedural signature classes, and `323` conditions:
  `45` each for C0-C5, `37` C6 alternates, `15` C7 strict-B controls, and one
  C8 diagnostic. Compared with EXP-024A, `312` conditions are semantically
  unchanged and `11` changed only by reconciled transition IDs.
- The complete live-world smoke passed, including same-world prompt/execution,
  replay namespace continuity, atomic interruption, resume, finalization, and
  validation. The formal run completed `323/323` unique conditions with zero
  replay or execution-infrastructure exceptions.
- On the `32` primary non-documentation Tier-3/4 states, raw oracle C1 versus
  bare C0 improved exact app/API match by `+0.125`, canonical action-signature
  match by `+0.34375`, execution success by `+0.0625`, and semantic successor
  match by `+0.40625`. Positive relative behavior occurred on `7/9` tasks.
- C1 beat signature-only C2 by `+0.21875` exact API and `+0.3125` action
  signature; their task-bootstrap confidence intervals exclude zero. C2
  retained only `9.09%` of C1's signature gain over bare.
- C1 also beat hard-negative C3 and unrelated C5 on exact API, action
  signature, and successor behavior. API-documentation-only states did not
  drive the result.
- The C1/C6 alternate-exemplar audit covers `37` pairs: effect direction agrees
  on `86.49%`, exact API and execution outcomes agree on `97.30%`, and effect
  Pearson/Spearman are `0.8419/0.7911`.
- Clean raw-NLL/outcome analysis is limited to `16` selected conditions across
  `8` states because the locked comparator cache covers the old 148-transition
  panel. Raw-NLL versus semantic-successor effect is Pearson/Spearman
  `0.3164/0.3695`; this subset is explicitly underpowered.
- Decision branch:
  `raw_transition_content_behaviorally_validated_on_clean_corpus`. This is a
  clean oracle one-step causal result, not a deployable field result. Field,
  program, injector, selector, Stage C2, and end-to-end training remain blocked
  pending a separately reviewed EXP-025C.

### EXP-025C signature-balanced field selector and behavioral-preflight stop

- Clean full-bank labels contain `310,433` legal pairs across A/B/C/D/E
  (`199,116/57,407/41,956/11,954/69,361`). Equal state/class weighting passes
  to numerical tolerance.
- The clean intent probe reaches `0.8759` mean strict held-out accuracy. The
  three field seeds complete `120` epochs and `7,560` updates each.
- The ensemble passes strict-B, deployment-E, and held-out-parent D selector
  gates. B/D/E NDCG@4 is `0.7766/0.8264/0.7780`; B/E transition-shuffle drops
  are `0.7715/0.7683`, both with task-bootstrap intervals excluding zero.
- The conditional one-step phase stopped before Qwen load. One F5 predicted-
  intent raw condition is a singleton signature class whose `41,134`-token
  prompt exceeds the locked `40,960` context, with no same-class substitute.
  F1/F3 selections are scoreable on all 45 states.
- Decision branch: `clean_corpus_behavioral_audit_infrastructure_invalid`.
  Selector ranking/generalization is verified; behavioral oracle retention is
  not. No F1-F5 generation or AppWorld condition execution ran.
- p(s,m_transition), program/compiler, injector, Stage C2, end-to-end RCMF,
  and V4 tagging remain blocked pending a narrow context-feasibility review.

### EXP-025C-R deployable selector behavioral validation

- The frozen EXP-025C selector, seed checkpoints, calibration, predictions,
  and selections were reused without retraining or reranking. The canonical
  ensemble SHA256 is
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`.
- The preregistered missing-row policy freezes `225` logical F1-F5 slots:
  `224` executable and one explicit F5 `over_context_missing` row. It has no
  generated response, outcome, metric, label, or imputation.
- The lifecycle smoke passed. The formal run completed all `224/224`
  executable conditions with zero replay/execution infrastructure errors,
  using `125` EXP-025B outputs, `36` in-run aliases, and `63` new frozen-Qwen
  generations and AppWorld same-world executions.
- On the primary 32-state subset, deployment-E raw F3 versus bare improves
  exact API by `+0.09375`, action signature by `+0.375`, execution by
  `+0.0625`, and semantic successor by `+0.34375`. Signature and successor
  task-bootstrap intervals exclude zero; `8/9` tasks are positive.
- F3 beats its signature card F4 by `+0.1875` exact API, `+0.28125` action
  signature, and `+0.3125` successor, all with intervals excluding zero.
- F3 retains `0.75/1.0909/0.8462` of oracle exact-API/signature/successor gain.
  Strict-B F1 retains `0.50/0.9091/0.7692`, and both strict-B and deployment-E
  behavioral claims pass separately.
- F3-F5 uses `31/32` primary paired states. Successor is `+0.16129` with 95%
  CI `[0.06897,0.24138]`; its adverse one-row bound remains `+0.15625`.
- Decision branch:
  `signature_balanced_field_selector_behaviorally_validated`. Automatic field
  selection is behaviorally validated on the clean one-step contract.
  p(s,m_transition), compiler/injector, Stage C2, end-to-end RCMF, and V4
  tagging remain blocked pending a separately reviewed program-distillation
  milestone.

### EXP-025D state-conditioned program runtime-review pause

- Created archive `archive/v4-selector-behaviorally-validated` and working
  branch `research/v4-state-conditioned-transition-program` at exact starting
  SHA `f05a40dfc095fa3ec655441642b0548ef382cf10`; no V4 tag was created.
- The deterministic manifests contain logical A/B/C/D/E counts
  `640/139/112/112/139` and scoreable counts `607/135/112/112/135`.
  All `41` over-context occurrences remain explicit missing measurements;
  truncation and class substitution are zero.
- Frozen B/D/E selections reproduce EXP-025C transition IDs exactly at
  `139/139`, `112/112`, and `139/139`. The A-only decoder split is `192/64`
  with zero grouped-state overlap.
- The clean sparse-teacher audit requires `970` new top-64 pair rows and can
  reuse zero complete rows. Seventeen scalar overlaps are insufficient for the
  required sparse distributions.
- Expected work is `16,384` decoder and `352,704` program pair updates,
  `201.72` H100 hours, `203.72` total wall hours including records, and
  `21.46 GB` of artifacts. Best/conservative H100 estimates are
  `144.13/518.42` hours.
- Status is `completed_runtime_review_required`. No Qwen model, forward,
  training, or GPU work ran. Explicit approval is required because expected
  H100 time exceeds the 12-hour review threshold. The state-conditioned
  program remains unvalidated and all later stages remain blocked.

### EXP-025D-Fast bounded observation-excluded program pilot

- The new bounded manifest contains A/B/C/D/E `128/24/24/24/32`, or `224`
  unique scoreable pairs, with zero truncation or over-context rows.
- Observation isolation, the six-view provenance contract, the benchmark
  adapter boundary, and direct incremental field add/remove/replace all pass.
- Exact prefix-KV equivalence failed within the timebox, so the run used the
  scientifically clean full-forward path.
- The bounded clean no-bias decoder passes its heldout gate. Canonical u64
  pair targets reach utility Spearman `0.989921` and Huber `0.035762`.
  Second-seed decoded-effect cosine is `0.965719`, utility Spearman is
  `0.997059`, and sign agreement is `1.0`.
- The tensor-space PairMLP upper bound fails: heldout cosine `0.214108` and MSE
  reduction versus zero `0.000180`. The observation-excluded factorized model
  also fails and is worse than static-only; the outcome-view diagnostic does
  not rescue it.
- Decision branch: `state_transition_representations_insufficient`. B/C/D/E
  Qwen validation and H1-H4 AppWorld execution were not unlocked. Compiled
  program, full bank, Stage C2, end-to-end RCMF, and V4 tagging remain blocked.

### EXP-025D-Direct direct behavioral factorization stop

- The complete scoreable A/B/C/D/E manifest is `607/135/112/112/135`; the
  A-only task-grouped split is `479/128` pairs across `29/8` disjoint tasks.
- The teacher cache contains `970` unique rows: `132` reused and `838` newly
  scored. No over-context row was truncated or assigned a target.
- The direct PairMLP selected u8 and passed. A/B/C/D/E Spearman is
  `0.5709/0.3864/0.4785/0.4065/0.3953`; Huber reduction versus zero is
  `33.52%/23.62%/28.70%/27.04%/22.67%`.
- The direct factorized model selected u16. A/B/C/D/E Spearman is
  `0.4078/0.2947/0.4412/0.4185/0.2981`, but Huber reduction is
  `+3.27%/-16.67%/-10.77%/-27.72%/-22.57%`. B/E fail positive Huber
  reduction and E also fails the memory-swap contrast.
- This distinguishes latent-target failure from behavioral information:
  PairMLP can learn direct behavior, but the current r16 field-compatible
  factorization cannot transfer calibrated utility to held-out B/E.
- Decision branch: `direct_behavior_factorized_program_failed`. H1-H4 were
  not unlocked. Compiled behavior, full bank, p(s,m_transition), Stage C2,
  end-to-end RCMF, full AppWorld evaluation, and V4 tagging remain blocked.

### EXP-025D-G2 r16 convergence passes teacher forcing but fails behavior

- Exact u16 continuation visited u32 and u48, then stopped before u64 under the
  preregistered A-validation rule. The selected u48 checkpoint has Spearman
  `0.614717`, Huber `0.150141`, and global `gamma=1.0`.
- B/C/D/E Spearman is `0.5516/0.4473/0.5040/0.5738`; Huber reduction versus
  zero is `29.70%/26.44%/30.03%/30.64%`. The teacher-forced gate passes.
- All `180/180` H1-H4 generations and same-world executions completed. On the
  primary 32 states, H1 action signature/successor is `0.28125/0.53125`
  versus C0 `0.31250/0.43750` and raw F3 `0.68750/0.78125`.
- H1 retains only `-0.0833/0.2727` of raw action-signature/successor gain,
  does not beat shuffled transition, and is positive on `3/9` tasks.
- Decision branch:
  `calibrated_factorized_program_not_behaviorally_retained`. Full-bank
  integration, p(s,m_transition), compiler/injector, Stage C2, end-to-end
  RCMF, full AppWorld evaluation, and V4 tagging remain blocked.

### EXP-025D-G3 PairMLP behavior and policy-distillation stop

- The immutable Direct PairMLP u8 checkpoint completed `135/135` P1/P2/P3
  generations and same-world executions. On the primary 32 states, P1
  action-signature/successor is `0.34375/0.56250` versus bare
  `0.31250/0.43750` and raw F3 `0.68750/0.78125`.
- P1 retains only `8.33%/36.36%` of raw action-signature/successor gain. It is
  positive on `5/9` tasks but misses the preregistered 40% retention gate.
- The automatically authorized conditional policy pilot cached `252` raw-
  memory response/top-64 teacher rows, trained on `128` A pairs for exactly
  eight updates each, and improved policy KL over zero on A/B/C/D/E.
- Its second `135/135` one-step audit still fails. Policy P1 has
  signature/successor `0.31250/0.53125`, retains `0%/27.27%` of raw gain,
  equals both shuffle controls on the decisive metrics, and is positive on
  only `3/9` tasks.
- Decision branch: `teacher_forced_objective_not_behaviorally_retained`;
  conditional result: `behavioral_policy_distillation_pairmlp_failed`. r64,
  full-bank compilation, p(s,m_transition), Stage C2, end-to-end RCMF, full
  AppWorld evaluation, and V4 tagging remain blocked.

### EXP-026A direct injection-channel capacity stop

- The exact 32 primary clean states support K4/K8 for `32/32` and K16 for
  `28/32`; four K16 rows remain explicit feasibility misses.
- Free per-pair DeltaE tensors remove the decoder, 128D latent, PairMLP, and
  factorized-program bottlenecks. All `184/184` O4/O8/O16 and shuffled-control
  generations/executions completed with zero infrastructure exceptions.
- K4 is the strongest channel: action-signature/successor is
  `0.50000/0.65625`, versus bare `0.31250/0.43750` and raw F3
  `0.68750/0.78125`. Raw-gain retention is only `50.00%/63.64%`.
- K8 retention is `25.00%/54.55%`; K16 retention is `0%/10%`. No K passes the
  locked 70%/50% behavioral channel gate.
- Decision branch: `input_embedding_channel_behavioral_capacity_failed`.
  Conditional widened PairMLP, r64, full-bank integration, p(s,m_transition),
  Stage C2, end-to-end RCMF, full AppWorld evaluation, and V4 tagging remain
  blocked.

### EXP-026B deep-residual carrier capacity pass

- The deterministic carrier uses decoder blocks `[7,14,21,28]`, the locked
  last four user-token positions, and `65,536` free scalars per pair. Four
  representative states pass exact zero-logit/NLL/generation/KV equivalence,
  locality, active-layer gradient, and frozen-Qwen checks.
- All 32 pair-specific residuals reached u16. Policy KL falls from `0.337537`
  at zero to `0.003246`; teacher CE falls from `0.348049` to `0.006278`; the
  maximum layer/global norm ratios are `0.484395/0.129083`.
- All `64/64` new R/S one-step generations and same-world executions complete
  with zero exceptions. R matches raw F3 exactly on action signature and
  semantic successor (`0.68750/0.78125`) and beats S by
  `+0.31250/+0.28125`.
- Raw-gain retention is `100%/100%`, with positive behavior on `8/9` tasks.
  Decision branch: `deep_residual_carrier_capacity_validated`.
- This validates a free deep carrier, not a deployable compiled program. No
  compiler, PairMLP, factorized program, full bank, Stage C2, end-to-end RCMF,
  full AppWorld evaluation, or V4 tag was created.

### EXP-027A raw-memory end-to-end and deep-residual amortization stop

- The frozen automatic selector plus one raw transition per ReAct turn solves
  `5/37` first test-normal tasks versus bare Qwen `10/37`, reaching the
  preregistered `CLEARLY_WEAK` band. It retains 3 bare successes, gains 2, and
  loses 7.
- The observation-excluded PairMLP deep-residual compiler selects u8 by
  A-validation Huber. B/C/D/E Spearman is
  `0.5724/0.5157/0.5452/0.5834`, with `44.34%/42.26%/43.01%/44.08%` Huber
  reduction versus zero.
- All `180/180` PairMLP P1/P2/P3/P0 one-step generations and same-world
  executions complete with zero exceptions. On 32 primary states P1 action
  signature/successor is `0.40625/0.59375`, versus C0
  `0.31250/0.43750` and F3 `0.68750/0.78125`.
- P1 is worse than transition shuffle on both primary behavioral metrics,
  equal to state shuffle on successor, lowers execution by `9.38` percentage
  points, and is positive on only `4/9` tasks.
- Decision branch: `deep_residual_amortization_failed`. The conditional
  factorized Phase D was not started. Full-bank compilation, p(s,m_transition),
  Stage C2, end-to-end compiled RCMF, full AppWorld evaluation, and V4 tagging
  remain blocked.

### EXP-028A train-side causal gate and structured compiler rescue

- The deterministic train-side panel exhausted all `499` clean train states:
  `464` paired scoreable outcomes, `27` over-context missing rows, and `8`
  strict semantic-replay missing rows. Labels are `129` POSITIVE, `300`
  NEUTRAL, and `35` HARMFUL.
- The `186` structured features are deployment-available and have zero
  leakage-audit violations. On eight heldout clean-train tasks, the selected
  threshold `0.60` activates `11/98` states with zero harmful activations and
  improves successor/signature from `0.4082/0.3980` to `0.4592/0.4694`.
- Gated raw first37 remains `8/37`, equal to matched bare, because the gate is
  OFF on all `872` turns. This is not causal task-level evidence.
- The u4 AppWorld-structured compiler passes train-validation eligibility.
  In the locked 32-primary-state audit, S1 improves C0 signature/successor by
  `+0.09375/+0.09375` and both shuffles by `+0.03125/+0.03125`, but is positive
  on only `2/9` tasks. Classification is `PARTIAL_POSITIVE`.
- Gated compiled first37 is `8/37` with gate activation `0/873`; it therefore
  executes bare Qwen throughout and does not validate end-to-end compiled
  memory.
- Decision branch: `appworld_structured_compiler_competitive`. Full-bank
  integration, another compiler, p(s,m_transition), Stage C2, end-to-end RCMF,
  full AppWorld evaluation, and V4 tagging remain blocked pending a
  deployment-feature gate-distribution review.

### EXP-028B ungated structured compiler live-specificity failure

- The frozen EXP-028A gate inference contract matches exactly. Heldout-train
  P(POSITIVE) mean/max is `0.288031/0.943520`, while first37 live mean/max is
  `1.00525e-8/8.75569e-6`; all locked thresholds activate `0/871` available
  live rows. Diagnosis: `broad_feature_state_distribution_shift`.
- A balanced train-vs-live feature classifier reaches heldout AUC `0.997624`;
  `64/186` features have absolute SMD at least `0.5`. The largest shift is
  `pair.documentation_compatibility` at `-4.8389`. UNK rates remain zero.
- Ungated first37 results are U0 matched bare `8/37`, U1 correct structured
  compiler `0/37`, and U2 transition-shuffled compiler `2/37`. U1 has no
  retained bare successes and is two tasks below its shuffled control.
- Decision branch: `structured_compiler_live_specificity_failed`. The curated
  one-step specificity signal does not survive complete live ReAct
  trajectories; gate recalibration alone cannot rescue the compiler.
- All 168 official AppWorld 0.1.0 `test_normal` tasks have historical per-task
  bare outputs, so the preregistered untouched fresh-37 pool is empty. The
  manifest records `insufficient_untouched_tasks`; no replacement was made.
- Structured-compiler work, gate tuning, full-bank integration, Qwen training,
  Stage C2, end-to-end RCMF, and V4 tagging remain blocked.

### EXP-029A fixed memory-reader heldout failure

- Deterministic bare-Qwen collection froze `222` live states over all 37 clean
  train tasks: `174` model-training states from 29 tasks and `48` heldout
  validation states from eight disjoint tasks.
- All `444/444` paired bare/raw conditions completed. Labels are `5` POSITIVE,
  `210` NEUTRAL, and `7` HARMFUL; no test-normal state or outcome entered the
  experiment.
- The fixed bottleneck-64 reader has `2,162,688` parameters and passes zero
  equivalence, locality, no-decode-injection, gradient, frozen-Qwen,
  mixed-precision, checkpoint-recomputation, and no-raw-prompt checks.
- Training used `243` class-balanced units and `972` backwards. The u4 policy
  KL improves to `0.038568` and the maximum ratio remains `0.004515`.
- All `576/576` heldout live R1/R2/R3/R0 conditions completed. At u1/u2/u4,
  all four controls have action signature `0.1875` and semantic successor
  `0.1458`; correct-pair positive tasks are `0/8` throughout.
- No checkpoint reaches PARTIAL. Decision branch: `fixed_memory_reader_failed`.
  Conditional first37 did not run.
- Neural compiled-memory architecture work, another reader/adapter, full-bank
  integration, Qwen training, Stage C2, end-to-end RCMF, and V4 tagging are
  stopped for the submission.

### EXP-030A published-style cross-attention reader policy-gate failure

- The dedicated external-memory reader passes zero/no-memory equivalence,
  decode access, separate-memory-KV, frozen-Qwen, gradient, memory-specific
  logit, and exact save/load/resume checks across all 36 Qwen layers.
- The 499-memory cache has 16 slots per layer and occupies 2.3567 GB. The
  rank-16 fusion reader has 4,718,592 trainable parameters; Qwen has zero
  trainable parameters and zero gradients.
- Phase 1 trained for three epochs on 401 source transitions and selected
  epoch 1 on 98 heldout-train states at CE `0.597649`. Phase 2 trained four
  epochs over 366 causal states and 576 units per epoch.
- On the 24 heldout POSITIVE states, the best epoch has raw-teacher policy KL
  X0/X1/X2/X3 = `0.583907/0.839403/0.976347/1.770020`. Correct memory beats
  both shuffles but is worse than zero memory; all four checkpoints fail the
  mandatory policy gate.
- Heldout live, reversible-field, whole-bank, and first37 conditions are all
  zero because no checkpoint is selectable. No test-normal outcome was used.
- Decision branch: `published_cross_attention_reader_failed_on_appworld`.
  Stop before the reversible field. Compiled-memory architecture work,
  another reader, full-bank integration, Qwen training, Stage C2, end-to-end
  RCMF, full AppWorld evaluation, and V4 tagging remain stopped for the
  submission.

### EXP-031A direct joint full-bank RCMF live specificity

- All 499 clean transitions were encoded as eight complete semantic views and
  compiled through four section writers into a reversible fixed field. Every
  nonzero scientific forward used the complete task-legal field; no retrieval,
  selected memory, per-memory runtime score, or raw memory prompt was used.
- Joint training completed two epochs and 1,152 backwards with frozen Qwen.
  Epoch 2 was STRONG on 98 heldout-train states: L0/L1/L2/L3 signature is
  `0.3980/0.4490/0.4184/0.3980`, and successor is
  `0.4082/0.4286/0.4082/0.3980`; positive task count is `4/8`.
- Feed-forward addition of 98 heldout memories took `1.005 ms` compilation and
  `0.0702 ms` field addition per memory on average, with no old-record scan,
  no retraining, unchanged field shape, and rebuild error `3.8147e-6`.
- First37 D0/D1/D2 is `8/37`, `8/37`, `5/37`: correct and bare tie, while the
  correct field beats the key-payload-shuffled field by three tasks. The exact
  operational branch is `rcmf_full_field_live_memory_specific_signal`; branch
  G is not reached because D1 does not exceed D0.
- The committed audit has 183 files, zero index mismatches, zero registered
  secret leaks, and zero raw JWT matches. All 34 attempts are closed.
- Freeze the result. The next action is decision-boundary/claim review,
  complexity measurement, audit-derived analysis, and manuscript integration;
  no V5 tag or new component study is authorized.

### EXP-031B benefit-preserving calibration active

- Active branch: `research/v5-rcmf-benefit-preserving-calibration`.
- EXP-031A source and artifacts are immutable and restoration-validated.
- The versioned 9b charter/config/algebra predeclare exact controls, global and
  layerwise residual scales, trust-region caps, pre-RMS confidence, and the
  positive normalized field diagnostic. No model parameter is trainable.
- Candidate outcomes and first37 outcomes have not been inspected. Scientific
  GPU work is blocked until the 14-state gain/loss audit, exact replay
  materialization, immutable-input preparation, and G100/bare equivalence
  gates pass.
- Current CPU verification: focused 9a/9b suite `35 passed`; complete
  repository suite `639 passed, 1 skipped`.
- The gain/loss audit now preregisters one exact D1 critical step for each of
  the six gains, six losses, and two retained successes. These identities were
  locked before any calibration-candidate outcome or new GPU generation was
  inspected.
- YAML task IDs beginning with digits are quoted where needed so all 14 IDs
  remain strings. This configuration-identity correction occurred before the
  audit attempt or any candidate execution; the focused 9b suite is now
  `17 passed` for the pending audit source.
- Audit attempt `exp031b-gain-loss-audit-001` stopped before report or replay
  output because the stricter 9b Git-safe guard detected credential-like
  assignments in inherited redacted trace text. The follow-up source applies
  the established EXP-031A hash-placeholder redaction again to every emitted
  task/action/observation field while retaining exact raw SHA256 identities.
- Audit attempt `exp031b-gain-loss-audit-002` also failed closed before
  output because a credential-like metadata field remained. The final recovery
  applies recursive key-aware redaction to the complete payload and replay
  manifest, then recomputes their Git-safe hashes.
- Audit attempt `exp031b-gain-loss-audit-003` also failed closed. The
  scanner now reports only the minimal serialized JSON path and match SHA256
  so the remaining trigger can be fixed without exposing its value.
- Attempt `exp031b-gain-loss-audit-004` localized the remaining trigger
  to the `29a7b7e_3` replay trajectory at one response field. Its value
  remained undisclosed; the match SHA256 is recorded. The 9b redactor now
  covers quoted credential values containing whitespace using the scanner's
  exact syntax.
- Attempt `exp031b-gain-loss-audit-005` completed normally. The audit covers
  all `14/14` preregistered tasks and emits `14/14` exact replay identities;
  the Git-safe replay manifest is
  `cd867357130a31ba073e810dca91099b21e371dd789edd01e7f9f8217d392fb7`.
- All six gains, six losses, and two retained successes are accounted for.
  Maximum normalized-slot reconstruction error is `2.81334e-5`; all 14 D1
  trajectories contain both positive and negative dominant contributions.
- Hypotheses B-E are supported; hypothesis A is supported with whole-bank
  attribution limits. No candidate outcome has been inspected. Stage 8A
  cached diagnostics and exact equivalence gates are next.

### EXP-031B Stage 8A source checkpoint

- The cached-diagnostics runner is implemented but no scientific GPU phase or
  calibration candidate has run at this checkpoint.
- G100 equivalence now covers exact original/calibrated logits, reader
  attention, generated token IDs, and executed code. The zero-field path also
  must reproduce bare logits, generated token IDs, and code exactly.
- Route-C caps use every prompt token from the 98 heldout states with equal
  token weight. State membership is reconstructed from frozen state IDs and
  the immutable 29/8 task split; a fixed bare continuation is used only to
  execute the causal prompt prefix, and continuation positions are excluded.
  No paired outcome label or success result enters C50/C75/C90 or Q50/Q75/Q90.
- The exact cached metric definitions and the Route-D raw-RMS spread gate are
  locked in the versioned config before candidate outcomes.
- Local verification: focused Stage-9b tests `22 passed`; complete repository
  suite `649 passed, 1 skipped`. Scientific GPU work remains gated on source
  commit/push/synchronization and immutable-input revalidation.

### EXP-031B Stage 8A calibration locked

- Exact G100/bare/zero-field equivalence passed under attempt
  `exp031b-stage8a-equivalence-001` in `22.9771` seconds with zero logit error
  and exact deterministic token/code identity.
- The outcome-independent 98-state prompt-token profile completed under
  `exp031b-stage8a-profile-001` in `171.8282` seconds. Calibration semantic
  SHA256 is `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`.
- Raw-field CV `0.803213` and p90/p10 `12.2912` pass the preregistered Route-D
  spread gate. C/Q values are now committed before candidate diagnostics.

### EXP-031B Stage 8A diagnostic recovery

- Attempt `exp031b-stage8a-diagnose-001` failed before its first atomic
  candidate row with `RuntimeError: No available kernel`. A Flash-only SDPA
  context had been applied to a padded two-row teacher-forced batch; the
  immutable EXP-031A heldout path permits deterministic math-SDPA fallback.
- No candidate result was produced or inspected. Remove only the extra
  Flash-only context from cached batched forwards, preserve every scientific
  input and metric, validate on CUDA, and resume under a new attempt ID.

### EXP-031B Completed - Benefit-Preserving Calibration Stops

- Run `rcmf_benefit_preserving_calibration_9b_20260827_001` is complete on `research/v5-rcmf-benefit-preserving-calibration` with seed `25101`.
- Result record commit: `80907554516e7d1cb1b4fb30df0c8e94c1c7126b`.
- The immutable EXP-031A freeze, independent artifact snapshot, repository bundle, and restoration smoke passed. Scratch provenance is sufficient: 63 scripts and 176 bundles are fully indexed, no EXP-031A scientific semantics are missing from Git, and no clean rerun is required.
- Stage 8A evaluated 22 outcome-independent candidates over 112 states and 2,464 cached rows. Exact G100/bare and zero/bare equivalence passed; calibration SHA256 is `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`.
- Stage 8B completed 308/308 corrected critical conditions. Stage 8C completed 882/882 new heldout conditions plus 294 zero reuses. L1 and Q90 were first37-eligible; L1 was selected first.
- L1 complete first37 is `7/37` correct and `5/37` shuffle, compared with immutable bare `8/37`. The correct field retains only 2/6 original gains, loses all three exact-set-migration gains, and preserves both retained successes.
- The formal branch is `benefit_preserving_calibration_stop_route`. The old mechanical correct-over-shuffle label remains descriptive but cannot override failed benefit-preservation gates. Q90 was not run.
- Accepted H100-active time is `8.2973 h`; total accounted H100-active time including preserved invalid attempts is `9.1689 h`. All 33 attempts are closed.
- The final Git-safe audit passes recursive strict scanning with zero raw JWT matches and zero registered sensitive-observation leaks. The first invalid export is preserved in Lambda quarantine and was never transferred or committed.
- Freeze EXP-031A/031B. No new calibration candidate, retraining, retrieval, hard gate, broader first37 run, or V5 tag is authorized.

### EXP-031C Completed - Q90 Full Trajectories Stop the Route

- Run `rcmf_q90_full_trajectory_9c_20260828_001` is complete on `research/v5-rcmf-q90-full-trajectory` with seed `25101`; all 19 attempts are closed.
- Exact G100/bare equivalence, Q90 identity, fresh-world determinism, frozen-parameter checks, and constant-size no-retrieval invariants passed.
- Heldout complete trajectories passed the preregistered continuation gate: H0/H1/H2/H3/H4 is `3/8`, `5/8`, `3/8`, `6/8`, `4/8`.
- Exposed first37 Q90 correct/shuffle is `5/37` versus `3/37`, while immutable bare/original correct are both `8/37`.
- Q90 retains only `3/6` original gains, loses the cross-app family and one of two retained successes, and recovers only `8749218_1`.
- Mechanical correct-over-shuffle label: `LIVE_MEMORY_SPECIFIC_SIGNAL`. Scientific decision: `STOP_ROUTE`.
- Final Git-safe audit covers 2,689 steps with zero raw JWT matches and zero registered sensitive-observation leaks. The model-derived query/slot tensor bundle remains hash-addressed on Lambda only.
- Freeze EXP-031A/B/C. No new calibration, retraining, retrieval, hard gate, broader evaluation, portability run, or V5 tag is authorized.

### EXP-032A Completed - Trajectory Union Distillation Fails Heldout

- Run `rcmf_onpolicy_trajectory_distillation_10a_20260828_001` completed with
  seed `25101`; 27/27 attempts are closed.
- Complete train-task rollouts produced T0/T1/T2 success counts of `15/29`,
  `14/29`, and `18/29`, with task classes 7 bare-only, 6 RCMF-only,
  8 both, and 8 neither.
- The frozen union contains 483 training rows, 494 units, 109 preservation
  states, 101 memory-benefit states, 111 auxiliary states, 3 preference pairs,
  8 strict loop negatives, and exact 124/494 bank augmentation.
- Reader losses decreased `0.153050 -> 0.113576`, but heldout correct/shuffle
  was only `0/8 vs 0/8` and `1/8 vs 0/8` against immutable H1 `5/8`.
- The preauthorized writer+reader epoch reached loss `0.101688` but heldout
  correct/shuffle was `0/8 vs 2/8`.
- No candidate was eligible, no final model was selected, and N1/N2 first37
  was not run.
- Decision: `trajectory_union_distillation_failed_on_heldout`.
- Accounted H100-active time was `5.6175 h`; wall span was `7.5167 h`.
- Freeze EXP-032A and preserve its complete Git-safe audit and Lambda artifact
  root. No new architecture, gate, retrieval path, calibration, or V5 tag is
  authorized by this task.

### EXP-033A Completed - Frozen EXP-031A One-Demo Dev Evaluation

- Run `rcmf_exp031a_one_demo_dev_11a_20260829_001` completed on `research/v5-rcmf-one-demo-dev-eval` with seed `25101`; no optimizer or backward pass ran.
- The exact official AppWorld 0.1.0 dev split contains 57 tasks (ordered-list SHA256 `c6aad8dca959d9c54537555dd6c3a4ececdd55390029511ab7971550d796e463`). Demo/dev and memory-parent/dev overlap checks passed, and model-input ground-truth leak count is zero.
- The new `full_demo_first_only` profile retains the original first complete demonstration exactly. Existing `full_demo` behavior remains byte-for-byte unchanged.
- Frozen identities passed: EXP-031A checkpoint `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`, correct 499-memory field `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`, selector `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`, and shuffle manifest `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`.
- Complete dev outcomes are D0 bare `12/57`, D1 correct field `17/57`, and D2 matched shuffle `12/57`.
- D1-D0 is `+5/57` with 11 gains and 6 losses; paired bootstrap 95% CI `[-0.05263, 0.22807]`; exact McNemar `p=0.33231`.
- D1-D2 is `+5/57` with 7 D1-only and 2 D2-only successes; paired bootstrap 95% CI `[-0.01754, 0.19298]`; exact McNemar `p=0.17969`.
- Both leave-one-task-out effects remain positive (`[0.07143, 0.10714]`). D1-D0 gains span eight task families; D1-D2 wins span six.
- The descriptive result is `absolute_improvement_and_matched_shuffle_specificity`. Confidence intervals include zero, dev is exposed, and this is not a one-demo-versus-three-demo causal comparison.
- Formal task wall sum is `5.5988 h`; accounted GPU-phase attempt wall is `5.6783 h`. The ledger has 10 attempts, 2 failed, and 0 open.
- The committed Git-safe audit contains 171 condition traces, 4,411 step rows, and 57 task comparisons. Audit-index SHA256 is `1616378ab22874c21d4f9bff84db52078ed4d9533b4d86cac79014adb7a09b72`; secret verification found zero leaks and zero raw JWTs.
- EXP-033A stops here. No training, first37/test run, prompt variant, calibration, gate, addressing change, or follow-on experiment was launched.

### EXP-034B Completed - Fresh One-Demo Selector Does Not Rescue Dev

- Run `rcmf_one_demo_selector_retrain_11c_20260830_001` completed on `research/v5-rcmf-one-demo-selector-retrain` with seed `25101`.
- A fresh three-member selector was trained from the exact locked EXP-025C recipe on 638 one-demo states and 499 unchanged transitions. Its ensemble SHA256 is `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300`.
- Selector diagnostics remained strong and shuffle-sensitive: B/E NDCG@4 is `0.783678/0.785296`, versus transition-shuffle `0.081675/0.083958`.
- The fresh selector changed 111/464 downstream selections. Recomputed labels are 134 POSITIVE, 281 NEUTRAL, and 49 HARMFUL.
- The unchanged downstream recipe selected epoch 1 on heldout train. Checkpoint SHA256 is `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764`; deployment field SHA256 is `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169`.
- Complete official-dev outcomes are D0 bare `12/57`, N1 correct `10/57`, and N2 matched shuffle `15/57`.
- N1-D0 is `-2/57`, CI `[-0.140351,0.070175]`; N1-N2 is `-5/57`, CI `[-0.192982,0.017544]`. Both directions remain negative under every leave-one-task-out deletion.
- Scientific decision: `STOP`. Fresh one-demo selector retraining does not rescue complete-trajectory memory-specific behavior under the frozen pipeline.
- The independently verified Git-safe audit contains 114 task-condition traces and 3,132 step rows. Index SHA256 is `c4bea6d3fb4c7ab2fef2f489bb626b8b5a0fee75f1dca06d24ff59a744daa802`; secret leaks are zero.
- No follow-on selector, calibration, architecture, retrieval, first37/test run, or V5 tag was started.
