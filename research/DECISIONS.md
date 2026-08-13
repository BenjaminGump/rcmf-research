# Decisions and Deviations

## 2026-08-04 workflow setup

VERIFIED:

- The ChatGPT-generated workflow document assumes GitHub CLI can be used, but
  `gh` is not installed on the local Windows host.
- At initial audit time, local GitHub SSH auth failed with
  `Permission denied (publickey)`.
- Therefore Codex cannot create or push the GitHub repository until the user
  configures GitHub auth or provides another writable repository mechanism.

Decision:

- Build the local GitHub-safe workflow structure first.
- Do not create a public repository until the user confirms repository
  visibility.

## 2026-08-04 GitHub repository setup

VERIFIED:

- The intended GitHub repository is
  `git@github.com:BenjaminGump/rcmf-research.git`.
- The local machine has `C:\Users\Admin\.ssh\github_rcmf` with fingerprint
  `SHA256:OWb0aCR7HIqa8luPJSQM/f9M9r4pWp7klDTBr79goiQ`.
- The user verified interactive SSH authentication as `BenjaminGump`.

Decision:

- Use `git@github.com:BenjaminGump/rcmf-research.git` as `origin`.
- Prefer a private GitHub repository unless the user explicitly chooses public
  visibility, because the repository contains AppWorld synthetic credentials
  and detailed research artifacts.

Follow-up:

- `origin` was configured locally as
  `git@github.com:BenjaminGump/rcmf-research.git`.
- Push succeeded after the user loaded `github_rcmf` into Windows ssh-agent.
- Git push from Codex must use
  `GIT_SSH_COMMAND='C:/Windows/System32/OpenSSH/ssh.exe -o BatchMode=yes'`;
  otherwise Git may invoke an SSH client that does not use the same agent path.

## Lambda environment naming

VERIFIED:

- The user originally provided `/home/ubuntu/venvs/rcmf`, but the verified
  working Lambda virtual environment is `/home/ubuntu/venvs/rcmf-py311`.

Decision:

- New workflow documentation records `/home/ubuntu/venvs/rcmf-py311`.
- Historical docs may still mention the older path; treat the verified path in
  `research/CURRENT_STATE.md` as current.

## Branch naming

VERIFIED:

- The workflow document recommends `workflow/research-loop`.

Decision:

- Use branch `workflow/research-loop` for this workflow setup, rather than the
  default `codex/` branch prefix, because the user explicitly asked to follow
  the workflow document.

## Large artifacts and historical sync files

VERIFIED:

- `.gitignore` excludes tar files, bundles, runs, and data.
- Some ignored local synchronization files are present in the working directory
  but are not tracked.
- The repository object store is small at audit time.

Decision:

- Do not delete ignored local files during workflow setup.
- Do not rewrite history.
- If a future GitHub push reveals undesirable historical objects, discuss a
  history-cleaning plan with the user before doing anything destructive.

## AppWorld over-context official trajectory

VERIFIED:

- Official train trajectory `2a163ab_3` produced pathological multi-million
  token contexts in the prepared per-step data.
- The raw official trace was not edited.
- User approved filtering this task from the prepared training data.

Decision:

- Use the filtered prepared dataset for full-demo RCMF AppWorld training.
- For future datasets, run context-length preflight and ask before filtering.

## 2026-08-04 next-iteration correctness pass

VERIFIED:

- User-provided `docs/RCMF_Next_Iteration_Codex_Task.md` requires no expensive
  full run until correctness and diagnostics pass.
- Local next-iteration tests pass: `python -m pytest -q` -> `43 passed`.
- The previous semantic-retrieval partial full run is worse than bare Qwen on
  the paired first-37 slice: RCMF `7/37`, baseline `10/37`.

Decisions:

- Do not start a new full-size GPU training run in this pass. Sync, validate,
  and run smoke/diagnostics first.
- Replace active AppWorld additive-memory config with `additive_token`.
- Retain deprecated `additive_prefix` only as a first-k additive-token alias for
  old checkpoints/configs.
- Retain the older virtual-token `prefix` injector in factory/test code for
  historical reproducibility, but keep it out of active AppWorld configs.
- Treat one `MemoryRecord` as one compiled write. If Qwen tokenization requires
  chunking, aggregate chunk hidden states with a token-weighted mean and call
  the compiler once.
- Keep the CLI mode name `all_except_current_task` for compatibility, but make
  its AppWorld behavior exclude task, episode, replay, and lineage keys.
- Set AppWorld `loss.utility: false` because no utility-loss term is currently
  implemented in `RCMFTrainer.training_step`.

Deviation from requested next-iteration ambition:

- The primary raw-text memory teacher is not implemented or run in this pass.
  It remains the next milestone and must use raw Qwen scoring over raw memory
  text, not compiled RCMF leave-one-out labels.

Follow-up:

- Run Lambda-side py_compile/pytest after sync.
- Run tokenizer-only memory chunk audit and memory-injection diagnostics on
  Lambda before any new full training.

## 2026-08-05 primary raw-text teacher pilot

VERIFIED:

- Milestone 3 was scoped to a Primary Raw-Text Memory Teacher Pilot only.
- The implemented teacher cache used frozen `Qwen/Qwen3-8B` target scoring and
  did not use compiled RCMF memory, external APIs, action generation, full
  student training, or full AppWorld evaluation.
- The pilot preserved full-bank leakage semantics: same task, episode, replay,
  and lineage memories are excluded from normal teacher candidates.
- Before scoring, every state-memory pair was token-length preflighted.
- Ten pairs exceeded the 40,960-token context limit after raw memory insertion.

Decision:

- Skip over-context state-memory pairs in the pilot after reporting their IDs
  and token counts.
- Do not truncate full-demo prompts, raw memory text, or targets.
- Treat the 0/4 all-memory audit recall as a blocker for scaling teacher labels
  directly into student training.

Deviation or workaround:

- None from the Milestone 3 scope. The over-context rows were not simplified or
  filtered silently; they were recorded in the teacher preflight artifact and
  summary.

Follow-up:

- Review candidate proposal quality with ChatGPT and the user before any full
  teacher cache or full RCMF training run.

## 2026-08-05 expanded all-legal teacher audit3B

VERIFIED:

- Milestone 3B reused the existing 24 pilot states and cached rows, then scored
  every legal memory for those states.
- The formal leakage definition was preserved: exclude same task, episode,
  replay, and lineage memories.
- Across the 24 states there were 1,080 legal pairs, 1,052 scoreable pairs, and
  28 over-context pairs.
- The full 638-state preflight found 28,710 exact legal pairs, 27,054
  scoreable pairs, and 1,656 over-context pairs.
- The deterministic reproducibility check for fixed positive, neutral, and
  negative pairs passed exactly.
- Representative prompt inspection found no obvious leakage, delimiter,
  section-order, target-hash, or memory-hash errors.

Decision:

- Continue to record and mask over-context state-memory pairs. Do not truncate
  prompts, raw memories, or targets.
- Set `configs/benchmark/appworld_rcmf_full_prompt.yaml` to provisional
  student-training default `injector.position=last_user_k` with K=4.
- Keep `first_k` and `last_prompt_k` configs available for later ablation, but
  do not launch three full training runs.
- Recommend option A, complete all-legal teacher-cache generation, after user
  and ChatGPT review. This recommendation is based on reproducibility,
  prompt-inspection health, positive/negative utility signal, context
  feasibility, and estimated cost; it is not based only on candidate recall.

Deviation or workaround:

- None from the Milestone 3B scope. No full teacher-cache generation, full
  student training, or full AppWorld evaluation was launched.

Follow-up:

- Wait for user and ChatGPT review before running the complete all-legal cache
  or any student training.

## 2026-08-05 complete all-legal raw-text teacher cache

VERIFIED:

- Milestone 3C generated the complete all-legal raw-text teacher cache for all
  638 filtered training decision states.
- The teacher definition was not modified from the approved raw-text frozen
  Qwen target-scoring definition.
- The formal leakage definition was preserved: exclude same task, episode,
  replay, and lineage.
- No prompt, target, or raw memory text was truncated.
- Over-context pairs were recorded and masked with null utility and
  `valid_for_loss=false`.
- Exact final counts matched the preflight contract: 28,710 legal pairs,
  27,054 scoreable pairs, and 1,656 over-context pairs.
- Validation passed, reproducibility passed, and representative inspection
  found 0 obvious issues.

Decision:

- Treat `raw_text_memory_teacher_full_cache_v1` as the current primary
  teacher-label artifact for review.
- Do not start RCMF student training or full AppWorld evaluation until the user
  and ChatGPT review complete-cache label quality, missingness, and overlap
  diagnostics.
- Use the Milestone 3C task-grouped split manifest as the provisional future
  student split, but do not train on it yet.

Deviation or workaround:

- None from the Milestone 3C teacher/scoring scope.
- Lambda still cannot pull GitHub directly; the existing documented git bundle
  sync fallback remains in use.
- The progress ETA during the long tmux run was not the final cost estimate,
  because cached rows were completed immediately. The final runtime
  `10.26` H100 hours is the source of truth.

Follow-up:

- Design a review-gated student-label compiler that handles over-context rows,
  all-missing states, positive/negative coverage, thresholds, and loss weights
  explicitly before any student training.

## 2026-08-06 Stage-B addressing-only pilot

VERIFIED:

- Milestone 4 implemented the Stage-B student-label compiler and
  addressing-only trainer/evaluator.
- The trainer computes model applicability with gradients as
  `q(s, i) = rho_i * dot(b(s), alpha_i)`.
- The trainer does not use precomputed `utility_scores` as model scores.
- Program head was frozen and verified unchanged with max absolute delta `0.0`
  in every pilot seed.
- The additive-token injector was not constructed.
- Qwen action loss and AppWorld agent evaluation were not run.
- The strict inductive memory split excluded all validation-task memories and
  removed special memory `076f5673-6565-5f20-aada-6f16a0f8d4b0` from the
  effective Stage-B bank because it had zero valid train labels.
- The scientific gate failed: learned mean NDCG@4 was `0.386161`, below the
  global train-utility baseline `0.453376`; shuffled-state NDCG@4 was also
  `0.386161`.

Decision:

- Stop at Stage B as requested. Do not proceed to program-head, injector, or
  full RCMF training.
- Treat the current Stage-B addressing formulation as not yet scientifically
  validated for held-out-task memory selection.
- Keep the compiled labels and pilot checkpoints for diagnosis, but do not use
  them as a green light for Stage C.

Deviation or workaround:

- The first label compiler run completed but exposed avoidable repeated hashing
  of the 121MB teacher cache. This was fixed by caching source hashes once, and
  labels were regenerated as `student_labels_20260806_002`.
- The first pilot summary evaluated the final model rather than the best
  early-stopped checkpoint. This was fixed and the pilot was regenerated as
  `addressing_only_pilot_20260806_003`.
- tmux command wrapping for the final regenerated pilot did not carry the
  internal command reliably, so the final `_003` run was executed in the
  foreground with a long timeout and tee'd log. The run completed in about
  79 seconds. No long-running process remains.

Follow-up:

- Before any Stage C work, analyze why state addresses and alpha collapse
  naturally under the current losses.
- Candidate next steps include revisiting the address normalization, adding an
  explicitly justified anti-collapse or contrastive term, and comparing against
  a simpler supervised scorer that directly consumes frozen state/memory
  representations.

## 2026-08-06 Milestone 4B addressing diagnosis

VERIFIED:

- Milestone 4B implemented and ran forensic checkpoint diagnostics, teacher
  utility decomposition, residual scorer ablations, and dense-address RCMF
  ablations.
- The corrected artifact is
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/addressing_4b_20260806_002`.
- Source commit for the corrected 4B run:
  `e61981fdd10514ba3250f32176f45ea21c2d0661`.
- No Stage C, program-head training, injector construction/training, Qwen
  action loss, full end-to-end RCMF training, or AppWorld evaluation was run.
- Hard-top-k disjoint-support zero-gradient trapping is real: disjoint
  supports produced `q=0.0` and zero gradients into both state and memory
  logits; an overlapping-support control had nonzero gradients.
- The existing hard-top-k best checkpoints for seeds 1 and 3 were fully
  trapped: all validation state-memory pairs had zero support overlap and zero
  raw dot product.
- Seed 2 escaped the fully disjoint trap but still collapsed to a shared
  one-overlap basis with state top-1 load `1.0` and alpha top-1 load `1.0`.
- State-only residual head and signed two-tower residual scorer both beat the
  global memory prior and were materially degraded by shuffled validation
  states.
- Dense separate-head and dense shared-head address variants both collapsed to
  the global prior, with zero correct-minus-shuffled improvement.

Decision:

- Treat the current RCMF address parameterization as the Milestone 4B
  bottleneck.
- Do not proceed to Stage C. The diagnostic decision-tree branch is
  `dense_rcmf_address_failed`.
- Preserve the dense-address run as a negative result: simply replacing
  hard-top-k with dense softmax did not recover the signed residual interaction
  signal.
- Prioritize a redesigned residual-address mechanism that can express signed
  state-memory interactions before reintroducing program or injector training.

Deviation or workaround:

- A smoke run caught a missing `defaultdict` import before the formal 4B run.
  The corrected formal artifact is `_002`; `_001` should be treated as
  superseded by `_002` because `_002` also records hard-control paired
  bootstrap rows and clearer forensic conclusions.
- The final run was foreground rather than tmux because it completed in about
  two minutes and wrote complete JSON/Markdown artifacts. No process remains.

Follow-up:

- Test an RCMF-compatible signed residual interaction that uses the global
  memory prior as a frozen baseline and learns only residual selection.
- Avoid restoring hard top-k until a dense or continuous address design passes
  the state-conditioned validation gate; consider sparsity annealing only after
  dense warm-up succeeds.

## 2026-08-06 Milestone 4C signed residual field

VERIFIED:

- Milestone 4C implemented and ran a signed continuous residual address model
  that preserves the successful Milestone 4B signed two-tower interaction while
  retaining a memory-bank algebra.
- Corrected artifact:
  `/lambda/nfs/rcmf-persist/project/runs/stage_b/signed_field_4c_20260806_002`.
- Source commit:
  `2fc95e2d41da933810df53e78a0eed62c972ee70`.
- Exact signed reference and core signed field matched under copied weights
  with max absolute errors `0.0`.
- Core signed field rank128 passed continuity and 5-fold task-grouped CV gates.
- Field algebra and add/remove/replace reversibility passed at rank128.

Decision:

- Replace the failed nonnegative softmax/top-k Stage-B interaction with the
  signed residual field as the provisional Stage-B address mechanism.
- Keep empirical train-derived `mu_i` as the default global prior for the next
  milestone. The learned-prior ablation did not fail, but it should remain a
  secondary deployability track until Stage C is diagnosed.
- Retain rank128 as the conservative default for the next milestone. Rank64
  passed the simple improvement criterion but was weaker on NDCG@4.
- Record the Stage-B signed-address scientific gate as passed, but do not start
  Stage C until user and ChatGPT review.

Deviation or workaround:

- The initial `_001` run is superseded. Its model metrics were valid, but it
  used float32 field-algebra validation with too strict an absolute tolerance
  and showed `passed=false`; it also exposed an AUPRC integration bug that
  produced negative AUPRC.
- Commit `2fc95e2` fixed both issues by validating the algebra proof in
  float64 and integrating AUPRC from recall 0. The corrected formal run is
  `_002`.

Follow-up:

- Design a Stage-C signed-program distillation pilot that introduces program
  vectors while preserving the signed residual selector and separate gate.
- Continue to avoid hard top-k, sparsemax, or sparsity annealing until the
  continuous signed field is integrated with program learning and diagnosed.

## 2026-08-06 Milestone 5 / Stage C1 signed program distillation

VERIFIED:

- Stage C1 introduced content-derived program vectors and a K=4
  `last_user_k` additive-token injector while keeping Qwen3-8B and the
  Milestone-4C signed selector frozen.
- The teacher-response cache selected the best legal effective-bank raw memory
  per state when utility was greater than `0.01`; otherwise it used the bare
  full-demo Qwen prompt. The student prompt did not contain raw memory text.
- The response cache validated with 638 states, 523 positive-teacher states,
  107 baseline-teacher states, and 8 all-missing states.
- Program field algebra, add/remove/replace reversibility, zero-delta
  equivalence, and tiny overfit all passed.
- The full teacher-forced pilot trained three seeds and completed in about
  `6.01` H100 hours. No AppWorld generation/evaluation was run.
- Correct content-derived programs improved validation target NLL relative to
  bare/zero field and random/shuffled-program controls, but did not beat the
  free-ID program control on average.
- No-positive validation states degraded by `0.028565` target NLL relative to
  bare Qwen, exceeding the `0.02` gate threshold.
- The behavioral leave-one-out audit found zero NLL effect from removing the
  teacher-best memory in all 16 audited positive states.

Decision:

- Stop after Stage C1 as requested. Do not proceed to Stage C2, program
  distillation beyond this diagnostic, selector/program/injector joint
  fine-tuning, Qwen action loss, or AppWorld agent evaluation.
- Treat the current signed program/additive-token channel as not yet
  scientifically validated for memory-content compilation.
- Preserve the Stage-4C signed selector as the best-supported addressing
  component, but do not infer that adding program vectors has succeeded.
- Label the reached branch as
  `signed_program_channel_not_behaviorally_useful_or_content_not_distinct`.

Deviation or workaround:

- The first response-cache validation failed after all 638 rows were generated
  because top-K-plus-other probability buckets occasionally summed to about
  `1.0000037`, above the initial overly strict tolerance. This was a numeric
  validation issue, not a teacher-definition change. Commit `77173e2` switched
  future bucket computation to float64 and set the validation tolerance to
  `1e-5`; the existing rows were revalidated successfully without rescoring.
- The first signed-program attempt `_001` exposed a sparse teacher-KL numeric
  instability when the union bucket probability was effectively one. Commit
  `e170022` clamped the student other-bucket probability below one and made
  geometry diagnostics ignore nonfinite rows instead of crashing.
- The initial `_002` summary had invalid `shuffled_state` and `mean_state`
  controls when `eval_batch_size=1`: the script built those controls inside
  each batch, so a one-row batch used its own state as the shuffled/mean state.
  Commit `9f16010` fixed this by creating full-evaluation state-index
  overrides and full-evaluation mean q/gate controls before batching. The
  final `_002` summary was recomputed from the existing checkpoints without
  retraining.
- None of these fixes changed the teacher definition, leakage rules, raw-memory
  insertion, target scoring, trainable/frozen module boundary, or no-truncation
  contract.

Follow-up:

- Diagnose why the trained injector/program path gives zero memory-specific
  leave-one-out effect despite improving target NLL over bare Qwen.
- Compare content-derived programs against free-ID programs more directly:
  matched capacity, per-memory contribution, program permutation sensitivity,
  and whether the injector can exploit state-level shortcuts.
- Consider a Stage-C repair that explicitly supervises per-memory behavioral
  differences or leave-one-out effects before any AppWorld generation run.

## 2026-08-07 Milestone 5B corrected Stage-C1 leave-one-out diagnostics

VERIFIED:

- The old Stage-C1 leave-one-out audit was invalid. `_leave_one_out_audit()`
  changed `legal_effective_mask[remove_index] = False`, but `_compute_z()`
  called `build_include_mask(..., validation_full_bank=True)`, which replaced
  every validation-row mask with all true.
- Therefore the old audited memory was never actually removed, and the old
  zero-effect leave-one-out result is superseded.
- Commit `f998a45` added explicit `include_mask_override` support. Normal
  validation still uses the full 36-memory effective train bank; counterfactual
  validation must now supply an explicit override; train rows still honor their
  legal mask.
- Unit tests prove that validation defaults to all effective memories, explicit
  counterfactual masks zero the removed memory score, z matches an explicit
  field recomputation, removing/restoring a nonzero memory changes/restores z,
  and train-time exclusion semantics are unchanged.
- Milestone 5B reran diagnostics from existing checkpoints only. It did not
  retrain Stage C1, start Stage C2, fine-tune any module, run AppWorld
  generation/evaluation, or regenerate the teacher response cache.
- Corrected teacher-best leave-one-out effect was nonzero but small: mean
  `0.002334`, CI `[0.000444, 0.004588]` over 345 state-seed rows.
- Teacher-best was poorly aligned with the Stage-4C selector: Recall@1/4/8 was
  `0.113043/0.313043/0.466667`, median rank was `10`, and `24.35%` of
  teacher-best memories received negative signed score.
- Teacher-best contribution was small: mean `3.50%` of summed contribution
  norm, contribution-rank median `13`.
- Compiled all-memory leave-one-out effects on the 32-state subset did not
  correlate with raw teacher utility: Pearson `-0.006813`, Spearman
  `-0.010966`.
- Content-derived programs were statistically worse than free-ID programs on
  target NLL overall and on positive-teacher states: all-state content-freeID
  CI `[0.000152, 0.015463]`; positive-state CI `[0.001650, 0.018212]`.
- Free-ID was not clearly better on no-positive states, whose content-freeID CI
  included zero.
- Injector scale `0.25` reduced no-positive degradation below `0.02` and had a
  slightly larger teacher-best LOO effect than scale `1.0`, but target NLL was
  much worse than scale `1.0`; this is diagnostic only.

Decision:

- Retract only the specific Stage-C1 claim that corrected teacher-best LOO was
  exactly zero. The corrected effect is nonzero but too small to establish
  memory-content causality.
- Keep the broader Stage-C1 scientific gate as failed.
- Use decision branch `selector_teacher_alignment_issue`.
- Do not start Stage C2 or another full-bank program-channel run before
  repairing selector-teacher alignment.
- Treat a restrained-injector rerun as a secondary hypothesis, because scale
  `0.25` improves no-positive preservation but loses much of the positive-state
  gain and does not solve teacher-utility correlation.

Deviation or workaround:

- None from the Milestone 5B hard scope. The teacher response cache was only
  revalidated, not regenerated. All diagnostics used existing checkpoints.

Follow-up:

- Repair Stage-B/selector supervision so the selected memory set better matches
  raw-teacher-best utility, or add a teacher-best-aware selection objective.
- After selector-teacher alignment improves, rerun a small Stage-C1 diagnostic
  with explicit pair-level or single-memory behavioral distillation and a
  restrained injector.
- Continue to block AppWorld generation/evaluation until a repaired Stage-C
  pilot shows memory-specific leave-one-out effects that correlate with teacher
  utility.

## 2026-08-07 Milestone 5C raw-teacher top-utility selector repair

VERIFIED:

- Milestone 5C retrained and diagnosed only the signed Stage-B selector.
  Qwen forward passes were not used during selector training. Stage-C program
  heads and the additive-token injector were not trained. AppWorld
  generation/evaluation, Stage C2, and end-to-end RCMF training were not run.
- The fixed CV ablation set contained the reproduced Stage-4C original loss,
  three all-pair gap thresholds, three top-listwise temperatures, a
  gap+top+sign-calibration variant, and a gap+top+sign+near-best variant.
- Hyperparameter/model selection used deterministic 5-fold task-grouped CV
  over the 37 training tasks only. The original 9-task validation split was
  used only after selecting the configuration.
- The selected configuration was `C_top_listwise_temp0p03`.
- CV selected-config metrics were Recall@4 `0.387077`, Recall@8 `0.616991`,
  NDCG@4 improvement over fold global prior `0.083399`,
  correct-minus-shuffled NDCG@4 `0.131188`, utility-score Spearman
  `0.119677`, and teacher-best negative-score fraction `0.203096`.
- The selected config did not pass the CV gate. It improved top-8 alignment
  and retained state-dependent NDCG, but it did not satisfy Recall@4 or
  utility-score Spearman requirements.
- On the original 9-task continuity split, the selected config had
  Recall@1/2/4/8 `0.179710/0.266667/0.359420/0.582609`, median teacher-best
  rank `7`, negative-score fraction `0.176812`, utility-score Spearman
  `0.174524`, NDCG@4 `0.581587`, and correct-minus-shuffled NDCG@4
  `0.206391`.
- The continuity gate did not pass. NDCG and state-dependence improved, but
  top-utility alignment and signed-score calibration remained insufficient.
- Geometry did not collapse: interaction variance `1.506124`, q effective rank
  `36.530959`, and k effective rank `17.653024`.
- Eval-only Stage-C1 projection replaced only the selector payload while using
  existing Stage-C1 content program/injector checkpoints. It did not retrain
  Stage C.
- Projection found teacher-best LOO mean `0.010726` with CI
  `[0.003277, 0.019094]`, but selector-top LOO remained larger at mean
  `0.027432` with CI `[0.017361, 0.039065]`.
- Raw utility versus analytic delta-z norm in the projection remained weak:
  Spearman `0.047117`.

Decision:

- Use decision branch `selector_capacity_or_representation_tradeoff`.
- Do not claim `selector_teacher_alignment_repaired`.
- Do not start Stage C2 or another full-field Stage-C1 retraining run from
  this selector.
- Retain the empirical result that top-listwise supervision is useful for
  Recall@8 and NDCG, but insufficient for calibrated raw-teacher-best
  alignment.
- The next program milestone must use explicit pair-level or single-memory
  behavioral grounding before any repeated full-bank Stage-C1 training.

Deviation or workaround:

- The tmux log ended with `EXIT:True` because the local PowerShell command
  expanded `$?` before SSH in the logging wrapper. The run itself completed
  normally and wrote a valid `summary.json`; no traceback or Python exception
  appeared in the log.
- The eval-only Stage-C1 projection is allowed only as the explicit Milestone
  5C diagnostic projection. It is not treated as Stage-C training or a repaired
  Stage-C result.

Follow-up:

- Diagnose why top-listwise improves Recall@8 but not Spearman or negative
  signed-score calibration. Candidate directions include calibrated score
  margins, listwise temperature/scale normalization, and training directly on
  teacher-best/near-best set probability without sacrificing NDCG.
- Design the next Stage-C repair around pair-level or single-memory behavioral
  distillation so memory-specific effects are supervised before full-bank
  aggregation.

## 2026-08-08 Milestone 5D pair-level / single-memory grounding

VERIFIED:

- Milestone 5D built and validated a pair-level response cache and trained
  single-memory program/injector diagnostics only.
- The primary model bypassed the signed selector on purpose:
  `z(s,i)=p_i`, with no selector score, no selector gate, no empirical `mu_i`,
  and no full-bank aggregation. This was causal isolation of the program
  channel, not a proposed replacement for the signed Stage-B field.
- Qwen3-8B was frozen. The signed selector was not trained. Stage-C1
  full-bank training was not repeated. Stage C2, end-to-end RCMF, AppWorld
  generation/evaluation, and Qwen action loss were not run.
- Pair cache validation passed for `1,728` selected legal pairs, including
  `1,152` train and `576` state-held-out validation pairs. It reused `88`
  compatible Stage-C1 rows and newly scored `1,640` rows.
- Pair category coverage was complete: train positive/neutral/negative/random
  `288/288/288/288`; validation `144/144/144/144`.
- Train-only perturbation smoke selected ratio target `1.0`, and the previous
  unrestrained `7-8x` injector regime did not reappear.
- Zero-program equivalence and tiny overfit both passed, so the graph is not
  trivially disconnected.
- State-held-out content results were weak: target NLL `0.665915`, sparse KL
  `0.318875`, behavioral-delta Huber `2.207829`, raw utility versus compiled
  utility Spearman `-0.293472`, sign agreement `0.403382`.
- Content did not meaningfully beat memory-identity controls:
  content-minus-shuffled-program target NLL `-0.000166`,
  content-minus-memory-swap target NLL `-0.000081`, and
  content-minus-random-program sparse KL `+0.010575`.
- Memory-held-out content compiler CV failed: mean Spearman `-0.189175`, and
  `0/5` folds had positive Spearman.
- Content program vectors were highly aligned, with pairwise cosine mean
  `0.998634` and centered effective rank `12.712268`.

Decision:

- Use decision branch `program_injector_behavioral_channel_insufficient`.
- Do not record `pair_level_memory_grounding_passed`.
- Do not start Stage C2, full-bank Stage-C1 retraining, joint
  selector/program training, Qwen action loss, or AppWorld agent evaluation
  from this result.
- Treat the current content-derived memory-program path as not yet
  memory-specific enough for full-bank aggregation.

Deviation or workaround:

- No hard-scope deviation. The tmux log ended with `EXIT:True` because the
  local PowerShell SSH wrapper expanded `$?` before the remote shell wrote the
  log footer. The Python run completed normally and wrote a validated
  `summary.json` and `report.md`.

Follow-up:

- Before another memory-content compiler or full-bank Stage-C run, test the
  additive-token/injector channel with an even simpler oracle behavioral
  capacity diagnostic, such as trainable free per-pair `z` vectors or explicit
  per-token logit-delta reconstruction.
- If an oracle per-pair latent cannot reproduce teacher deltas, redesign the
  injector or target-distribution loss. If it can, revisit the program
  compiler and memory representation choices before restoring selector-weighted
  aggregation.

## 2026-08-08 Milestone 5E oracle pair-latent injector capacity

VERIFIED:

- Milestone 5E reused the Stage-5D pair-response cache and diagnosed only the
  additive-token injection channel.
- Qwen3-8B was frozen. The signed selector, selector scores/gate, empirical
  `mu_i`, and full-bank aggregation were not used.
- AppWorld generation/evaluation, Stage C2, end-to-end RCMF training, and
  memory-content compiler retraining were not run.
- Pair cache validation passed for `1,728` pairs. Target-token teacher utility
  identity passed with maximum absolute error `1.001358e-06`.
- Best K=4 direct DeltaE oracle reached u_text/u_direct Spearman `0.641904`,
  sign agreement `0.776978`, target-token delta correlation `0.369083`, and
  mean perturbation ratio `0.488439`; this failed the direct-channel gate.
- Optional K=8 direct DeltaE did not repair the failure.
- Target-token delta Huber was much better aligned with raw utility than the
  old sparse behavioral-delta Huber objective: Spearman `0.636335` versus
  `-0.092488`.
- Frozen-injector pair-z inversion failed and did not beat zero/random controls
  on the primary utility-aligned metrics.
- Free per-memory z showed weak positive Spearman/sign values but badly
  worsened NLL/KL/delta-Huber relative to zero, so it is not a successful
  fixed-memory latent result.

Decision:

- Use decision branch `direct_delta_fails`.
- Treat the primary bottleneck as
  `additive_token_injection_location_bandwidth_or_behavioral_target`.
- Record the old Stage-5D sparse union-top64 behavioral-delta objective as a
  verified contributing bottleneck.
- Do not proceed to Stage C2 and do not return immediately to memory-content
  compiler design.
- The next repair should isolate injection site/decoder/objective issues. Since
  K=8 did not fix direct DeltaE, prioritize later-layer residual or logit/hidden
  oracle diagnostics under the target-token-delta metric.

Deviation or workaround:

- The Stage-5E script's `fixed_memory_latent_gate_passed` helper is too
  permissive because it only checks weak positive correlation/sign criteria.
  The research interpretation overrides it: free memory-z did not pass once
  NLL, sparse KL, target-delta error, and zero-control comparisons are included.
  Future gate helpers should include these control-relative requirements.

Follow-up:

- Design EXP-016 as an injection-site/objective diagnostic rather than another
  memory compiler run.
- Keep target-token delta reconstruction as the primary utility-aligned
  objective in the next capacity test.

## 2026-08-09 Milestone 5F-A convergence-corrected direct oracle

VERIFIED:

- The formal Stage-5E command gave every pair-specific direct DeltaE only two
  gradient-bearing visits. The original artifact is preserved and is now
  labeled `underoptimized_two_update_result` for capacity interpretation.
- The old dense table used one shared tensor with Adam. A row with stale Adam
  state could move on a later optimizer step even when that row was not in the
  current batch. The corrected implementation uses independent parameters so
  unselected rows receive no gradient and no optimizer update.
- The corrected artifacts store exact per-pair update counters and optimizer
  state. All formal checkpoints had equal minimum, maximum, and mean updates
  per pair.
- Sequence-level utility supervision on the balanced 192-pair set reached
  Spearman `0.976238`, sign agreement `1.0`, and sequence Huber `0.054151` at
  ratio 1.0 and 64 updates per pair.
- Ratio 1.0 passed every direct utility-capacity check except plateau. Its
  sequence Huber improved another `22.5965%` from u48 to u64.
- The run stayed within the K=4 input-embedding, `last_user_k` scope. No new
  injection site, selector, compiler, full-bank model, Stage C2, or AppWorld
  evaluation was run.

Decision:

- Replace the old Stage-5E `direct_delta_fails` capacity interpretation with
  `oracle_not_converged_extend_updates`.
- Keep the Stage-5E objective-mismatch result: the old sparse behavioral-delta
  objective remains poor evidence for utility alignment.
- Do not claim that K=4 input-embedding capacity has formally passed until the
  documented plateau criterion is met.
- Do not redesign the injection site while the direct oracle is still
  improving materially.
- Do not run the conditional pair-z inversion, memory compiler training,
  Stage C2, or AppWorld evaluation yet.

Deviation or workaround:

- No hard-scope deviation occurred.
- The pilot records u48/u80/u96/u112 in addition to the required
  u2/u8/u16/u32/u64/u128 points. These intermediate checkpoints are required
  to evaluate the fixed 16-update plateau window and do not change the subset
  or objective selection protocol.
- Objective selection used the predetermined lexicographic rule. It selected
  sequence utility plus sparse KL because that pilot reached a documented
  plateau, even though pure sequence utility had lower u128 Huber but was
  still improving. The 192-pair result was not used to revise this choice.

Follow-up:

- Run EXP-016B as a resumable ratio-1.0 convergence extension from u64 to at
  least u128, with fixed 16-update checkpoints and a predetermined train-only
  learning-rate stabilization rule if needed.
- If the direct oracle reaches a plateau and still satisfies the other gate
  checks, stop for review before a separately approved 128D injector/decoder
  capacity milestone.

## 2026-08-09 Milestone 5F-B direct-oracle convergence extension

VERIFIED:

- The Stage-5F-A ratio-1.0 checkpoint resumed with all 192 ordered pair IDs,
  DeltaE rows, Adam states, update counters, objective settings, and learning
  rate intact. The u64 metric reproduction maximum absolute difference was
  `0.0` before any new update.
- The formal run continued every pair through u80/u96/u112/u128 without
  reinitialization. All per-pair counters exactly matched each checkpoint.
- u128 met the supplied plateau predicate: relative sequence-Huber improvement
  from u112 was `-0.1689106903` and Spearman improvement was
  `-0.0053458074`; both are `<0.01`.
- The curve was nonmonotonic. u112 sequence Huber `0.029525` was better than
  u128 `0.034512`; the latter was `16.8911%` worse. The predicate counts
  deterioration as less than 1% improvement, so this is a rule-compliant stop,
  not evidence of a monotonic asymptote.
- At u128, utility Spearman was `0.979465`, sign agreement `0.992806`, sequence
  Huber `0.034512`, positive/negative mean student utility
  `+0.667495/-0.753849`, neutral mean absolute utility `0.000135`, and max
  perturbation ratio `1.0000001` within tolerance.
- Final sequence Huber was `93.3019%` below zero and decisively below matched
  random. All eight registered utility-capacity checks passed.
- Qwen remained frozen. No pair-z, shared-injector decoder training, memory
  compiler, selector, Stage C2, AppWorld generation/evaluation, or end-to-end
  training was run.

Decision:

- Record branch
  `input_embedding_channel_capacity_passed_after_convergence`.
- Treat K=4 `last_user_k` input-embedding injection as having sufficient
  direct-oracle sequence-utility capacity under ratio 1.0.
- Keep Stage-5E's direct-channel failure superseded as an underoptimized
  two-update result; retain its sparse-objective mismatch finding.
- Do not redesign the injection site based on Stage 5E. The next separately
  approved milestone should isolate whether a properly optimized 128D
  pair-latent/shared-injector decoder can realize the validated direct signal.
- Do not infer that the deployable memory compiler or full program field has
  passed; EXP-016B is an oracle capacity result only.

Deviation or workaround:

- The immutable Stage-5F-A checkpoint predates embedded DeltaE/config/model/
  cache hashes. It was not rewritten. EXP-016B validates an external immutable
  source-integrity sidecar containing the independently audited checkpoint,
  normalized DeltaE, pair-manifest, model, tokenizer, cache, and config
  identities.
- The first launch at source commit
  `b0037568a3decb8661c58630f73ad14c1fd539c6` aborted before model loading or
  u65 because the new tensor hash and old audit hash framed identical bytes
  differently. Commit `02f13ec2bba7600441b565cd97884fc23f9fdbc9`
  restored exact compatibility and added a regression test. The source
  checkpoint and training state were unchanged.
- No scientific-scope deviation occurred. Learning rate remained `0.05`, and
  the run stopped at the first eligible checkpoint under the exact supplied
  plateau rule.

Follow-up:

- Review EXP-016B before approving a 128D pair-latent/shared-injector capacity
  milestone. That experiment must preserve adequate updates-per-pair and must
  not silently expand into compiler, selector, Stage C2, or AppWorld work.
- For future convergence protocols, consider specifying a non-deterioration
  guard in addition to the current "less than 1% improvement" predicate.

## 2026-08-13 Milestone 5F-C shared-decoder capacity audit

VERIFIED:

- u112 and u128 direct DeltaE checkpoints were loaded read-only with exact
  ordered pair IDs, tensor shape `[192,4,4096]`, hashes, update counts, ratio
  budget, Qwen/tokenizer/cache identity, and metric reproduction delta `0`.
- Global uncentered rank 128 passed the behavioral low-rank gate for both
  targets. Rank 192 exactly reproduced the original DeltaE and Qwen behavior.
- The immutable three-fold manifest has 192 pairs grouped into 57 states, no
  decoder train/held-out state leakage, and all 36 memories in every training
  fold.
- Both shared decoders fit train-fold DeltaE. Linear relative Frobenius error
  was at most `1.57e-5`; MLP error was at most `1.88e-3`.
- Pooled frozen-linear inversion achieved u112/u128 utility Spearman
  `0.988537 / 0.994685`, sign agreement `0.992806 / 1.000000`, and sequence
  Huber `0.027538 / 0.015615`.
- Every numerical frozen-linear gate check passed on both targets and results
  were positive in all three folds. Frozen decoder hashes remained unchanged.
- No decoder path reached the corrected plateau in all three folds. The
  formal shared-decoder gate therefore did not pass.

Decision:

- Record branch `shared_decoder_optimization_or_generalization_failure` for
  both the primary u112 and robustness u128 targets.
- Do not label rank 128 as insufficient: the global rank-128 gate passed and
  frozen-linear held-out inversion shows strong utility capacity.
- Do not label tensor reconstruction as the active failure: every train-fold
  decoder reached its tensor plateau with near-exact reconstruction.
- Treat held-out Qwen inversion convergence/generalization as the unresolved
  formal bottleneck. Strong nonplateaued results are evidence, not a gate pass.
- Do not claim `current_injector_mlp_decoder_is_bottleneck` yet because the
  frozen-linear path also failed the required plateau check, although the
  linear decoder is the empirically preferred path.
- The next separately reviewed milestone should resume only the existing
  frozen-linear held-out z checkpoints to a corrected plateau. It must not
  start memory-content compilation, selector training, Stage C2, AppWorld, or
  end-to-end RCMF work.

Deviation or workaround:

- `_001` was preserved after float32 SVD failed the rank-192 exactness check.
- `_002` was preserved after tolerance handling scaled tiny over-ratio rows
  and changed the rank-192 behavioral result.
- `_003` was resumed atomically after implementation fixes for a missing
  import, tensor target device alignment, numerical-floor plateau logic, best
  tensor checkpoint restoration, and the u64 continuation rule. Regression
  tests cover each corrected behavior.
- The u64 continuation repair was required by the milestone contract. Fold-2
  frozen MLP stopped at u64 for both targets because Huber deteriorated beyond
  the 1.02 best-value guard; completed checkpoints were not overwritten.
- A restart command with an incorrect working directory exited before model or
  checkpoint loading. The corrected command resumed `_003`; no duplicate run
  was created.
- No scientific-scope deviation occurred. K, injection site, objective, folds,
  targets, ratio, update schedule, and frozen-Qwen contract were unchanged.

Prospective rule:

- Future plateau checks require absolute relative Huber change `<1%`, absolute
  Spearman change `<0.01`, and current Huber no worse than `1.02` times the
  best observed Huber. Large deterioration cannot count as convergence.

## 2026-08-04 Lambda GitHub sync fallback

VERIFIED:

- Lambda accepted GitHub's host key with
  `GIT_SSH_COMMAND='ssh -o StrictHostKeyChecking=accept-new'`.
- Lambda then failed `git pull origin workflow/research-loop` with
  `Permission denied (publickey)` because no GitHub private key/deploy key is
  configured on the instance.

Decision:

- Do not add GitHub credentials to Lambda during this pass.
- Use an already-pushed local Git branch as source of truth, create a git
  bundle locally, upload it with the existing Lambda SSH key, and fast-forward
  Lambda from the bundle.
- Record this fallback in
  `docs/Codex_Lambda_RCMF_远程执行规范.md`.
