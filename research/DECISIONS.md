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

## 2026-08-14 Freeze RCMF V3 before transition-memory research

VERIFIED:

- The source state is commit
  `97ca723ad66597d2afcbbce1eb5466eb34c009f6` on
  `workflow/research-loop`.
- V3 uses one complete successful trajectory as one `MemoryRecord` and one
  logical compiled field write.
- Teacher, signed-selector, reversible-field, direct K=4 input-injection, and
  rank-128 linear decoder components have strong validated evidence.
- Content-derived static trajectory programs and end-to-end trajectory-memory
  behavior are not validated.

Decision:

- Freeze the current design as
  `RCMF V3 - Component-Validated Trajectory-Memory Field (Pre-Transition)`.
- Use annotated tag `rcmf-v3-component-validated-pre-transition` and archive
  branch `archive/rcmf-v3-component-validated` as immutable/browsable refs.
- Begin transition-memory research only on
  `research/v4-decision-transition-memory` and describe it as a V4 candidate.
- Do not create V1 or V2 tags without independently auditing their exact
  representative commits. Do not create a V4 tag in the transition pilot.
- Do not describe V3 as final, successful, working, or end-to-end validated.
- Preserve parent trajectories as human-readable ledger units. A future
  transition field must delete a parent by subtracting all child transition
  deltas, preserving the fixed-cost reversible-field principle.

Reason:

- V3 diagnostics show that pair-specific behavioral signals exist and can be
  executed through the validated injection/decoder path, but one static
  content-derived vector per whole trajectory does not preserve
  memory-specific behavior. A complete decision transition is therefore the
  next atomic-memory hypothesis to test.

## 2026-08-14 EXP-017 runtime threshold semantics

VERIFIED:

- The unchanged EXP-017 preflight contains 148 transitions, 32 queries, 4,640
  legal pairs, 4,579 scoreable pairs, and 61 over-context masked pairs.
- Its projected best/expected/conservative costs are 7.596/8.687/12.270 H100
  hours. The expected projection is below the 12-hour review threshold.
- The teacher phase completed without truncation or pair reduction. Its
  4,640-row cache passed validation in 2.275 H100 hours.

Decision:

- Treat 12 H100 hours only as a preflight review threshold, not as a compute
  budget or a dynamic stopping rule.
- If a future expected preflight projection exceeds the threshold, pause for
  explicit approval and then run the approved design unchanged.
- During EXP-017 behavior optimization, optional u128 continuation is governed
  only by the pre-registered train-only convergence rule. Runtime projections
  at u64 are informational and cannot suppress continuation.
- Do not reduce the transition panel, query set, legal pair set, optimization
  updates, controls, or validation to meet 12 hours.

Reason:

- H100 cost is reimbursable, and scientific completeness is the governing
  constraint. A dynamic time gate could silently weaken the experiment after
  its design had already passed preflight review.

## 2026-08-14 EXP-017 decision-transition pilot conclusion

VERIFIED:

- The 148-transition raw-text teacher passed all validity checks over 4,579
  scoreable legal pairs. Positive/neutral/negative counts were
  2,271/941/1,367.
- The best transition beat its matched parent whole-trajectory teacher in
  885/1,120 comparisons, showing useful transition-local signal and harmful
  child transitions inside otherwise helpful parents.
- The frozen rank-128 decoder pair oracle passed with utility Spearman
  0.957418, sign agreement 0.976744, and sequence Huber 73.20% below zero.
- A single static latent per transition failed held-out query states:
  Spearman 0.123261, sign agreement 0.543103, and Huber 0.052237 versus zero
  0.028513. All four held-out tasks failed.
- The static transition model did not materially beat the directly comparable
  whole-trajectory static baseline on two required dimensions. The granularity
  gate failed.
- Independent post-run validation passed with zero errors. Qwen and the frozen
  decoder remained unchanged, and no selector, compiler, full bank, Stage C2,
  AppWorld generation/evaluation, or end-to-end training was run.

Decision:

- Record branch `static_transition_program_insufficient`.
- Keep decision transitions as a promising teacher/ledger decomposition, but
  do not call transition granularity behaviorally validated.
- Reject the hypothesis that one state-independent static 128D program per
  transition is sufficient across query states.
- The next reviewed milestone should test an explicitly state-conditioned
  transition program `p(s,m_transition)` or equivalent interaction. Do not
  respond by merely widening the static transition encoder.
- Keep parent trajectories as human-readable ledger units. A parent deletion
  subtracts all child transition deltas, preserving fixed-cost reversible
  field algebra.
- V4 remains a candidate. Do not create a V4 tag or merge this branch into
  `workflow/research-loop` from EXP-017.
- EXP-016D remains unlaunched; EXP-017 did not silently substitute for it.

Implementation deviations and recovery:

- Behavior attempt 1 stopped after pair-oracle u64 because the static runner
  signature omitted `expected_validation_task_ids`.
- Behavior attempt 2 stopped before any static update because the internal
  evaluator incorrectly retained that unused required parameter.
- Exit codes and logs were preserved. Regression tests now check the public
  signature and every direct keyword-only call contract. The final run resumed
  the existing pair-oracle checkpoint in the same `_001` artifact.
- No scientific parameter or artifact identity changed during either repair.

## 2026-08-14 EXP-018 state-transition representation gate

VERIFIED:

- EXP-017 reuse validation passed over 499 transitions, 148 panel transitions,
  32 queries, 4,640 legal rows, 4,579 scoreable rows, and 61 masked
  over-context rows without rewriting the source artifact.
- A deterministic parent-grouped transition split contains 29 train and 8
  held-out parent trajectories. The four scoreable cells contain
  2,667/904/752/256 rows for A/B/C/D.
- Five-fold task/parent-grouped model selection used only cell A. B/C/D labels
  did not affect hyperparameters or checkpoint selection.
- On double-held-out D, the concat-MLP upper bound reached Spearman 0.059482,
  sign agreement 0.758170, and Huber 0.126287. It did not beat state-only or
  transition-only and did not materially degrade under transition shuffling.
- The signed bilinear model reached Spearman 0.111083, sign agreement 0.575163,
  and Huber 0.062106 on D and also failed its gate.
- Future fixed-cost V0/T tensor contraction and transition/parent
  add-remove-replace-order reversibility checks passed.
- Independent validation reported zero errors. The append-only ledger contains
  one normal attempt and no resume or duplicate run.

Decision:

- Record branch `state_transition_representations_insufficient`.
- Stop before Qwen behavioral training, as required by Part D. Do not train or
  report the factorized `p(s,m_transition)` behavioral model, latent PairMLP,
  optional trajectory control, selector, program head, injector, or full bank
  from EXP-018.
- Do not interpret this as evidence that the factorized behavioral equation,
  frozen decoder, or K=4 input-injection channel failed. Those components were
  intentionally not exercised after the representation gate failed.
- Keep the exact two-axis split and teacher rows immutable for a later
  representation-focused diagnostic. Require genuine double-held-out
  transition-shuffle sensitivity before reconsidering Qwen behavioral
  training.
- Keep V4 as a candidate and do not create or move an RCMF V4 tag.

Operational definitions fixed before the run:

- Parent split: SHA256-deterministic ordering with 29 train and 8 held-out
  parent trajectories.
- A representation model materially beats a baseline at a Spearman gain of at
  least 0.05 and materially degrades under a shuffle at a Spearman drop of at
  least 0.05. These thresholds operationalize the milestone's word
  "materially" and were not selected from B/C/D labels.
- The concat upper-bound gate additionally requires D Spearman at least 0.20
  and sign agreement at least 0.60.
- Complete transition texts are represented by token-weighted means over
  complete chunks when needed; no truncation is allowed. In this run all 148
  transition texts fit one chunk.

Implementation deviations and recovery:

- None. One attempt ran from start to normal completion at source commit
  `0fa7e8dd6ac3a49d4895e624a72f9e9de2da547c`; no scientific parameter
  changed, no checkpoint resume was required, and the network/Codex disconnect
  did not trigger a duplicate run.

## 2026-08-14 EXP-019 representation interaction repair

VERIFIED:

- EXP-019 reproduced EXP-018, retained exact A/B/C/D counts
  `2,667/904/752/256`, and kept all 61 over-context rows masked without
  leakage or truncation.
- D's always-positive sign baseline is `0.764706`, exactly
  `117/(117+36)`. Sign agreement is therefore diagnostic only and is not a
  scientific gate.
- Cell-A state and transition main effects jointly explain `0.449074` of
  utility variance, leaving interaction-residual variance `0.060575` and
  residual effective rank `16.097`.
- Residual/listwise objective repair on the old representations failed the
  double-held-out interaction gate. Signed bilinear D NDCG@4 was `0.484875`
  with only a `0.012744` state-shuffle drop and a transition-shuffle contrast
  whose task-grouped bootstrap interval included zero.
- Span-aware frozen-Qwen views were valid and nonconstant, but no multi-view
  field-compatible model passed. The strongest, a low-rank tensor, reached D
  NDCG@4 `0.554092` versus transition-only `0.566808`, with state-shuffle
  drop `0.020493` and only one positive held-out task.
- The structured-feature model also failed. This does not support the branch
  that Qwen pooling alone failed while deterministic structure succeeded.
- The frozen-Qwen prompt-only cross-encoder cached all 4,579 pairs and fit A,
  but failed D: NDCG@4 `0.379564`, per-state Spearman `-0.054573`, residual
  Spearman `-0.035283`. Transition shuffling improved NDCG@4 to `0.495154`.
- The cross-encoder's cell-A grouped learning curve remained slightly rising
  and unstable at 12 query tasks. NDCG@4 moved `0.426066 -> 0.431144` from
  8 to 12 tasks, while residual Spearman moved `-0.151063 -> 0.003225`.
- Independent post-run validation passed with zero errors. All five failed and
  four completed attempts remain paired in the append-only ledger, and no
  scientific parameter changed during repair.

Decision:

- Record branch `query_task_coverage_insufficient`.
- The EXP-019 representation gate is not repaired. Behavioral
  `p(s,m_transition)` training remains blocked.
- Do not conclude that prompt-only interaction is impossible: the strongest
  upper-bound learning curve did not meet the preregistered saturation
  condition at the current 12 train query tasks.
- The next separately reviewed milestone should expand query-state teacher
  coverage, preserving the transition panel, leakage rules, exact two-axis
  evaluation, full-demo prompt, no-truncation rule, and train-only selection.
- Rerun the prompt-only upper bound first. A field-compatible interaction must
  then either pass the full D gate or retain at least 70% of the upper-bound
  gain over the best single-axis baseline with material state- and
  transition-shuffle sensitivity.
- Do not launch behavioral training automatically even if an expanded
  representation experiment passes; return for user and ChatGPT review.
- Keep V4 as a candidate. Do not create or move an RCMF V4 tag.

Implementation deviations and recovery:

- Attempt 001 exposed an overly strict EXP-018 reproduction tolerance for a
  `1.12e-9` serialization difference. The tolerance repair was regression
  tested and did not change a metric or gate.
- Attempts 003 and 004 exposed tokenizer-boundary handling errors in span
  validation. The final implementation accepts only exact or token-expanded
  spans whose decoded text remains source-aligned; it never truncates or
  semantically broadens a span.
- Attempt 006 finished every atomic cross-encoder row before a wrong
  multiview-artifact path failed post-cache processing. Attempt 007 validated
  and reused all 4,579 rows, with zero newly computed or duplicate rows.
- Attempt 008 failed before learning-curve training due a wrong aggregate
  path. Attempt 009 resumed from the validated Part-E artifact.
- Failed attempts and reports were preserved. All ledger entries record
  `scientific_parameter_changed=false`; no run UUID was duplicated.

## 2026-08-15 EXP-020 all-task query coverage

VERIFIED:

- EXP-020 covers all 37 train tasks and all 9 heldout validation tasks with
  exactly two deterministic early/later states each. The 92-state manifest
  contains the immutable original 32-state panel as an exact subset.
- The unchanged 148-transition panel yields 13,616 Cartesian pairs, 296
  leakage-excluded pairs, 13,320 legal pairs, 13,128 scoreable rows, and 192
  masked over-context rows. All 4,640 compatible EXP-017 rows were reused and
  8,549 rows were newly scored. No input was truncated.
- A/B/C/D scoreable counts are `8,205/2,051/2,296/576`. B/C/D labels did not
  affect model, view, epoch, or hyperparameter selection.
- Prompt cross-encoder D NDCG@4 increases
  `.360914 -> .461709 -> .523176` across LC12/LC24/LC37 on the same 9-task
  evaluation set. The curve is materially increasing rather than saturated.
- Despite the increase, LC37 cross-encoder per-state Spearman is `.117526`,
  residual Spearman is `.063559`, state/transition-shuffle drops are
  `.017769/.062644`, the transition-shuffle bootstrap CI includes zero, and
  only 5/9 heldout tasks show positive relative behavior. The upper-bound gate
  fails.
- The multiview low-rank field retains 71.22% of the cross-encoder gain over
  transition-only and has material state/transition-shuffle drops, but its D
  per-state Spearman is only `.134551`, the transition-shuffle CI includes
  zero, and only 4/9 tasks are positive. The field-compatible gate fails.
- The all-successful-example action-intent probe reaches `.875899` correct
  mean accuracy versus `.420863` after state shuffling and `.559353` majority.
  Frozen state views retain heldout-task decision intent.
- Independent validation reports zero errors. No behavioral program, injector,
  selector, production field, Qwen behavioral backpropagation, Stage C2,
  AppWorld evaluation, end-to-end run, demo change, or V4 tag occurred.

Decision:

- Record the preregistered primary branch
  `query_task_coverage_still_data_limited`: the cross-encoder fails at LC37,
  but its fixed-evaluation learning curve remains materially increasing.
- Record the optional diagnostic branch
  `state_intent_available_but_memory_utility_target_not_generalizing` because
  action intent generalizes while every state-transition utility model fails
  the interaction gate.
- Do not treat more query coverage as having repaired the representation gate.
  Behavioral `p(s,m_transition)` remains blocked.
- Do not automatically score more states from the same 37 tasks. A separately
  reviewed next milestone should compare an action-intent-conditioned or
  relative/pairwise memory-use target with absolute raw-text target-NLL
  utility. Additional source tasks or augmentation remain options if that
  target audit supports them.
- Keep the full teacher cache and representations as immutable diagnostics.
  Keep V4 as a candidate and do not create or move a V4 tag.

Implementation deviations and recovery:

- Attempt 002 exposed missing resume-provenance arguments in the preflight
  launcher and failed before tokenizer, model, GPU, or artifact work. The
  launcher contract and regression tests were added.
- Attempt 005 exposed that `build_backend(load_model=False)` leaves the
  tokenizer unset. Strict validation had therefore reached a generic renderer
  fallback. Existing teacher and prompt hashes were unchanged; the repair
  explicitly loads the canonical tokenizer and rejects fallback in this path.
- Attempt 007 completed all 60 new state rows before a shape-sensitive check
  compared immutable `[3,4096]` rows with flattened `[12288]` rows. Flattened
  values were exactly equal. The repair normalizes shape only for strict value
  comparison, and attempt 008 resumed from the latest atomic row.
- All ten attempts remain append-only, use one run UUID, and record
  `scientific_parameter_changed=false`. Network/laptop disconnects did not
  create a duplicate process or artifact.

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

## 2026-08-16 EXP-021 memory-use target audit

VERIFIED:

- The immutable EXP-020 utility cache and split were reproduced exactly:
  92 query states, 148 transitions, 13,320 legal rows, 13,128 scoreable rows,
  192 over-context rows, and A/B/C/D `8,205/2,051/2,296/576`.
- The fixed 192-pair serialization audit passes all registered robustness
  checks. The raw target remains the locked historical comparator.
- Cell-A-only grouped CV selects T4 pairwise preference. No B/C/D label was
  used for target, epoch, loss, or model selection.
- On D, selected T4 field NDCG@4 `.433983` is below immutable EXP-020
  transition-only `.480274`; per-state/residual Spearman is
  `.117200/-.051060`, only 4/9 tasks improve, and the transition-shuffle CI
  includes zero.
- Oracle and predicted intent do not beat transition-only. No revised target
  passes the field-compatible deployability gate.

Decision:

- Record branch `raw_nll_memory_use_target_not_deployably_predictable`.
- Keep raw transition target-NLL utility as a valid deterministic measurement,
  but do not use it as the next deployment target under the current evidence.
- Keep behavioral `p(s,m_transition)` blocked. Do not train the program,
  injector, selector, full field, Stage C2, or AppWorld agent from this result.
- The next reviewed milestone should compare structural procedural labels,
  direct next-action/API compatibility, and environment/outcome supervision.
  Genuinely new source tasks or augmentation may be considered only after the
  teacher target is preregistered. Do not add more states from the exhausted
  37-task source merely to repeat the same target.
- Keep V4 as a candidate and do not create or move a V4 tag.

Implementation deviations and recovery:

- The initial preflight referenced an obsolete EXP-019 summary path. The path
  was corrected before scientific work began.
- Cell labels in immutable EXP-020 metadata required canonical A/B/C/D alias
  normalization. Counts and row identities were unchanged.
- Serialization attempt 001 completed all durable rows before an aliased
  combined-token field failed aggregation. Attempt 002 reused every row and
  scored nothing again.
- Model attempt 001 exposed a mathematically redundant scalar pair-loss loop.
  The exact vectorized form was regression tested; attempt 002 restarted model
  fitting because the interrupted process had no atomic optimizer checkpoint.
- The attempt validator initially assumed every row had start/end event
  semantics. It now validates the one preserved legacy failure row separately.
- Final gate evaluation initially used a run-local transition mean instead of
  the locked EXP-020 transition-only comparator, read the wrong
  transition-shuffle bootstrap key, and used one global baseline for per-task
  comparisons. The old report is preserved and marked superseded. A
  record-only repair recomputed the gate from unchanged predictions and locked
  metrics.
- The first repair attempt exposed the nested EXP-020 metric schema and failed
  before writing a replacement summary. The second used a tested shape adapter
  and completed. Every ledger row records
  `scientific_parameter_changed=false`.

## 2026-08-16 EXP-022 transition-panel coverage stop

VERIFIED:

- The fixed 148-transition panel gives legal Tier-3/4 candidates to only 12 of
  18 held-out query states in cell B (`66.6667%`). The preregistered threshold
  is 70%.
- Cell D coverage is 9/18. Six B states lack any legal Tier-3/4 train-parent
  transition.
- Parser, label, hash, ledger, and no-training/no-replay checks passed 20/20.

Decision:

- Stop EXP-022 with branch
  `transition_panel_procedural_coverage_insufficient` before model training and
  AppWorld replay. Do not round 66.6667% to the gate, substitute states, or
  silently expand the panel.
- Do not interpret absent field/shuffle/behavior metrics as failed models; they
  were never run under the registered stop rule.
- Keep behavioral `p(s,m_transition)` blocked.
- The next review may consider all 499 transitions as a coverage repair. It
  must preserve leakage/no-truncation rules and report exact context and cost
  preflight before launch.
- Keep raw-NLL as a secondary comparator and V4 as a candidate. Do not create
  or move a V4 tag.

Implementation recovery:

- The credential safety scanner initially inspected UUID metadata and produced
  false positives. It now scans signature payloads only; regression tests prove
  UUID metadata is excluded while credential-like signature values still fail.
- A nonexistent `AttemptLedger.checkpoint` call failed after durable outputs.
  The repair uses the established `progress(latest_validated_checkpoint=...)`
  API and has a dedicated regression test. Failed attempts remain preserved.

## 2026-08-16 EXP-023 full-transition coverage and diversity decision

VERIFIED:

- The complete 499-transition bank gives scoreable Tier-3/4 coverage to 17/18
  held-out query states in strict train-parent B and deployment-space E. This
  exceeds the unchanged 70% historical continuity threshold.
- The old 148 panel remains globally insufficient: B/D/E coverage is
  `12/18, 9/18, 12/18`, and none of the six B gaps is rescued by D alone.
- Full-bank scoreable A/B/C/D/E Tier-3/4 coverage is
  `70/74, 17/18, 55/74, 12/18, 17/18`; exact-API coverage is
  `73/74, 18/18, 63/74, 15/18, 18/18`.
- The bank contains 150 signatures for 499 transitions. API documentation is
  210/499, and 349 transitions occur in 54 duplicate groups. In B, only 10/18
  states have at least two high-tier signatures and two high-tier parents;
  seven have one signature, two have one parent, and six rely solely on API
  documentation for high-tier coverage.
- Exact context preflight found 44,910 legal, 43,415 scoreable, and 1,495
  over-context pairs, with no truncation and no state losing all high-tier
  candidates to context.

Decision:

- Record branch `nominal_procedural_coverage_lacks_diversity`.
- Distinguish the results precisely: the original 70% nominal B threshold
  passes, while the preregistered diversity interpretation does not. Do not
  describe the nominal coverage result itself as failed.
- Keep the procedural field model, Qwen/AppWorld one-step audit, and behavioral
  `p(s,m_transition)` blocked until duplicate-signature and API-documentation
  dominance are reviewed.
- A future EXP-024 must preregister whether signatures are equivalence classes,
  weighted records, or unchanged repeated records. It must not choose this
  policy from held-out labels or silently deduplicate the bank.
- The required expected EXP-024 projection is 13.837 H100 hours, above the
  12-hour review threshold. Obtain explicit approval before launch; do not
  weaken the full design merely to fit the threshold.
- Keep V4 as a candidate and do not create or move a V4 tag.

Implementation and validation notes:

- The scientific run contains exactly one normally completed attempt and no
  parameter change or duplicate run.
- The first independent-validator invocation interpreted logical data-hash keys
  as filesystem paths. The validator-only fix and regression test are in
  `3c5ed9171cd9ba3e9882673752846edc09b02fb4`; final validation passed 24/24 and
  no scientific artifact was rewritten.
- The launcher shell wrote cosmetic `0n` to `exit_code.txt`. The authoritative
  attempt ledger records integer exit code 0 and heartbeat status `completed`.

## 2026-08-17 EXP-024A exact replay invalid

VERIFIED:

- Signature/strata/condition preflight completed under the unchanged EXP-023
  definitions. It fixed 323 one-step conditions over 45 states and passed the
  non-documentation coverage gate.
- Exact replay failed all 45 selected states before Qwen was loaded. There are
  no generated candidate actions or behavioral control results.
- Official successful trajectory artifacts are AppWorld 0.1.0; the Lambda
  package is 0.2.0.dev0 from upstream commit `a072b7a...`.

Decision:

- Apply the preregistered branch `appworld_one_step_replay_invalid` and stop.
  Do not weaken observation normalization, skip failed states, or proceed with
  partial replay coverage.
- Record all absent behavior metrics as `not_run_replay_gate_failed`, never as
  numerical zero.
- Treat the version mismatch as a verified provenance mismatch and a causal
  hypothesis. Only a matched-version replay can verify that it caused the
  divergence.
- The next review should isolate AppWorld 0.1.0 code and data without modifying
  the current verified 0.2.0.dev0 environment. Re-run replay first and require
  45/45 before Qwen generation.
- Field training, behavioral program training, injector/selector changes,
  Stage C2, full AppWorld evaluation, and a V4 tag remain prohibited.

Implementation recovery:

- Preflight attempt 001 incorrectly reused labels limited to EXP-020's 92-state
  query manifest, omitting 27 states unique to the immutable 45-state one-step
  manifest. The failed attempt was preserved. The repair recomputed all legal
  labels with the unchanged EXP-023 parser/tier logic and added a regression
  test; attempt 002 then completed.
- The original success-only post-run validator could not represent a valid
  preregistered stop. A branch-aware finalizer and validator now require zero
  generation artifacts, preserve failed attempts, and passed 132/132 checks.

## 2026-08-17 EXP-024R matched-version replay remains invalid

VERIFIED:

- A fully isolated AppWorld 0.1.0 capsule passes official tests and task
  verification and exposes package/code/data/evaluation 0.1.0.
- The corrected sentinel improves sharply over 0.2.0.dev0 but passes only
  3/13 states. Both no-history states pass, so the old package-version mismatch
  was not the complete replay cause.
- Every remaining normalized observation difference is a time-dependent login
  JWT. One source query supervisor identity also disagrees with its task
  metadata.

Decision:

- Record `appworld_010_execution_semantics_or_normalization_mismatch`.
- Do not launch the full 45-state replay, Qwen generation, or any EXP-024A
  memory condition. Missing 45-state metrics remain `not_run`, not zero.
- Do not change the locked normalization after observing the JWT mismatch. Any
  semantic-token comparison must be separately preregistered as a secondary
  definition while retaining the locked exact result.
- Treat AppWorld 0.1.0 matching as necessary but insufficient. Its role is a
  verified contributor, not a causally complete explanation.
- Keep behavioral `p(s,m_transition)`, procedural field training, injector,
  selector, Stage C2, end-to-end RCMF, and V4 tagging blocked.

Implementation recovery:

- The wheel metadata permits Python 3.10 but official source imports
  `typing.Self`; use isolated Python 3.11.15. The release has no dependency
  lock, and Click 8.1.7 is required for the Typer 0.12.5 CLI. Neither changes a
  scientific parameter.
- Sentinel attempt 001 compared a full current-task query with the core
  AppWorld instruction. It is preserved and superseded by regression-tested
  v2 contract/results in separate paths.
- Analysis attempt 001 expected a redundant history-count field. Analysis 002
  resumed from the unchanged sentinel summary after a tested aggregation fix.

## 2026-08-17 EXP-024R2 semantic replay blocked by source identity

VERIFIED:

- AppWorld 0.1.0's observed login-token differences are strictly temporal:
  all 11 JWT pairs differ only in `exp`, retain matching headers/stable claims,
  validate through installed AppWorld, and have zero non-temporal mismatch.
- A schema-limited semantic contract can safely ignore only the allowed `exp`
  value and consequent signature while rejecting identity, permission,
  response, malformed-token, and external-timestamp changes.
- The all-45 source identity gate is 40/45. Five fixed `b0a8eae_2` states use
  one internally consistent source query whose supervisor differs from both
  retained official 0.1.0 task snapshots.

Decision:

- Record `source_query_task_identity_snapshot_unresolved`.
- Do not run the repeated sentinel or full semantic replay when identity is
  below 45/45. Their missing metrics are `not_run`, not zero.
- Do not claim `appworld_010_semantic_replay_validated`; only the JWT semantic
  component is validated.
- Keep EXP-024A generation and every behavioral/program/field training path
  blocked until the exact historical identity provenance is resolved and the
  fixed sentinel then passes twice.
- Preserve locked v1 as the historical comparator. Semantic v2 remains a
  prospective secondary replay contract and does not rewrite EXP-024R.
- Keep V4 as a candidate and do not create or move a tag.

Implementation recovery:

- Four preflight failures exposed old-summary naming, non-JWT schema fields,
  valid-JWT index alignment, and EXP-020 subset-coverage assumptions. All are
  preserved in the append-only ledger and repaired with regression tests.
- The identity-only probe completed once and was reused by exact scientific
  request hash. No duplicate probe world or run UUID was created.
- Validator-only fixes selected the immutable `/data/tasks/<task_id>` manifest
  from EXP-024R's list schema and materialized the JSONL ledger once. Final
  validation passed 23/23 without changing scientific outputs.

## 2026-08-17 EXP-024R3 corpus identity consistency failure

VERIFIED:

- Two of 46 source tasks, `b0a8eae_2` and `b0a8eae_3`, have supervisor
  identity hashes inconsistent with both agreeing official AppWorld 0.1.0
  snapshots. All 638 decisions remain consistent with their parent raw
  trajectory queries.
- `b0a8eae_2` is `source_query_header_only_corruption`; its behavior follows
  the official identity. A matching task spec is not a coherent snapshot.
- No exact coherent historical snapshot was found in the bounded immutable
  search.
- `b0a8eae_3` is train-side and contributes to train labels, teacher-source
  memories, and transition parents.

Decision:

- Record `source_dataset_identity_consistency_failure` and stop before any
  task quarantine or AppWorld replay.
- Do not create a 40-state provenance-valid manifest. Its prerequisite that
  `b0a8eae_2` be the only inconsistent task is false.
- Do not recompute only a `b0a8eae_2` deletion sensitivity analysis; it would
  not define a provenance-valid corpus while `b0a8eae_3` remains in training.
- Preserve EXP-018 through EXP-023 metrics and decisions as immutable, while
  marking their source-identity provenance scope for review.
- Keep EXP-024A generation, semantic replay, field/program/injector/selector
  work, Stage C2, end-to-end RCMF, and V4 tagging blocked.

Implementation recovery:

- Preflight 001 incorrectly coupled source-layer agreement to current source
  file visibility; preflight 002 overclassified an identity-matching task spec
  as coherent. Both are preserved under hashed attempt snapshots.
- Regression-tested source-layer and behavioral-coherence rules in preflight
  003 produced the final result. No scientific parameter changed.

## 2026-08-17 EXP-025A identity reconciliation and strict replay failure

VERIFIED:

- The corpus defect is an unpinned active-task-snapshot lookup during official
  trace ingestion, not a task suffix, row offset, or behaviorally mixed world.
- Both affected tasks are header-only corruption and pass complete candidate
  replay after changing only their canonical query headers to official 0.1.0
  metadata.
- A structurally valid 46-task candidate was built without changing actions or
  observations. Train-side influence invalidates old checkpoints even where
  individual cache rows are reusable.
- The corrected sentinel passes, but the strict full replay is `42/45` because
  three root-level login JWTs fall outside the preregistered token-field
  schema. All three differ only in `exp`; the contract was not broadened.

Decision:

- Apply `repair_query_header_to_official_metadata` to both `b0a8eae_2` and
  `b0a8eae_3` in the new corpus lineage only. Historical artifacts remain
  immutable.
- Record `identity_reconciled_corpus_replay_failure` rather than the ready-corpus
  branches. Distinguish `structural_corpus_candidate_ready=true` from
  `clean_corpus_ready=false`.
- Do not declare any old checkpoint clean. Require retraining for every model
  influenced by train-side `b0a8eae_3`.
- Do not broaden semantic-v2 after observing root-level JWTs. A narrow root
  login-token contract requires separate preregistration and replay review.
- Keep all generation, representation/teacher recomputation, model training,
  Stage C2, end-to-end RCMF, and V4 tagging blocked.

Implementation recovery:

- Three failed attempts exposed an access-token schema placeholder and two
  saved-prediction schema variants. All failures are preserved, regression
  tested, and resumed without changing scientific inputs.
- The final append-only ledger has 17 complete attempts and no open row. The
  post-run validator reports zero errors.

## 2026-08-18 EXP-025B root-login JWT replay validation

VERIFIED:

- The only EXP-025A full-replay failures were three root-level JWT outputs from
  one `spotify.login` history action. In both new full repeats, those JWTs match
  on algorithm, type, subject/app/user stable claims and differ only in `exp`;
  installed AppWorld 0.1.0 validation and subsequent authenticated calls pass.
- Adversarial tests reject changed identity, app, username, role/permission,
  token type, algorithm, non-token response content, non-login actions,
  malformed tokens, JWT/non-JWT pairs, and arbitrary root timestamps.
- The original corrected sentinel, fixed three-state extension sentinel, and
  complete 45-state replay each pass twice with exact semantic and database
  repeatability. V1 and v2 metrics remain preserved as historical controls.

Decision:

- Record `identity_reconciled_replay_validated` and freeze
  `appworld_identity_reconciled_replay_validated_v1` before any Qwen work.
- Permit only the preregistered incremental cache rebuild and oracle one-step
  causal audit in EXP-025B. Do not declare old train-influenced checkpoints
  clean and do not retrain them in this milestone.
- Keep field/program/injector/selector training, Stage C2, end-to-end RCMF,
  full AppWorld evaluation, and V4 tagging blocked.

## 2026-08-18 EXP-025B clean oracle causal-content validation

VERIFIED:

- The replay-validated reconciled lineage supports a complete incremental
  rebuild with hash-exact reuse of unaffected rows and no historical artifact
  rewrite. All final cache, leakage, truncation, identity, and duplicate-key
  checks pass.
- The live AppWorld 0.1.0 bridge keeps replay variables, live JWTs, prompt
  observations, and generated-code execution in the same fresh world and
  Python namespace for every condition.
- Frozen Qwen completed all `323/323` preregistered conditions. On the primary
  non-documentation Tier-3/4 subset, the raw procedural oracle materially beats
  bare, signature-only, hard-negative, and unrelated controls. The action-
  signature and successor improvements have task-grouped confidence intervals
  excluding zero, and `7/9` tasks are positive.
- Raw versus alternate same-signature exemplars satisfy the preregistered
  consistency gate. API-documentation-only rows do not explain the primary
  gain.

INFERENCE:

- Under this oracle one-step contract, raw episodic transition content carries
  behavioral information beyond the normalized procedural metadata card.
- The next useful question is whether a signature-balanced deployable field can
  predict the helpful transition while preserving this content effect.

UNVERIFIED:

- No field, selector, program, injector, or deployable retrieval mechanism was
  trained or evaluated. The result does not establish full-trajectory benefit,
  Stage C repair, or end-to-end RCMF value.
- The clean raw-NLL correlation is descriptive only because only `16` selected
  conditions across `8` states intersect the locked 148-panel comparator.

Decision:

- Record `raw_transition_content_behaviorally_validated_on_clean_corpus`.
- Treat the oracle causal-content gate as passed and the replay/cache
  infrastructure as valid.
- Keep field/program training blocked until a separately reviewed EXP-025C.
  Do not automatically resume historical V3 training, p(s,m_transition),
  selector/injector work, Stage C2, end-to-end RCMF, or V4 tagging.

Implementation recovery:

- Preserve the failed representation-count and Pair-5D reuse attempts in the
  append-only ledger. Their regression-tested fixes changed no scientific
  parameter; the canonical Pair-5D pass restored unnecessarily recomputed rows
  to exact legacy bytes.
- The final ledger has `22` closed attempts (`44` start/end rows), no open
  attempt, and no duplicate scientific run.

## 2026-08-18 EXP-025C selector pass and behavioral preflight block

VERIFIED:

- Signature-class balancing, clean intent prediction, A-only grouped CV, three
  final seeds, and B/C/D/E evaluation completed on the replay-validated clean
  lineage.
- Strict-B and deployment-E pass all selector gates. Held-out-parent D also
  has positive transition-only gain, shuffle sensitivity, and task coverage.
- One required F5 raw condition has a `41,134`-token prompt against the locked
  `40,960` limit. Its selected class is a singleton, so the mandated same-class
  fallback does not exist.

Decision:

- Preserve the selector result as a passed ranking/generalization gate, but do
  not call automatic field selection behaviorally validated.
- Record `clean_corpus_behavioral_audit_infrastructure_invalid` because the
  complete `45 x 5` condition contract cannot be frozen without truncation,
  cross-class substitution, or a context-contract change.
- Do not run a partial `224/225` audit and do not use another class selected
  after seeing behavior. No Qwen generation or AppWorld condition execution is
  allowed under the current manifest.
- Require a separately reviewed, prospective context-feasibility rule before
  resuming. Do not retrain the selector while resolving this manifest issue.
- Keep p(s,m_transition), program/compiler, injector, Stage C2, end-to-end
  RCMF, and V4 tagging blocked.

Implementation recovery:

- Four failed preflight attempts remain in the append-only ledger. Renderer,
  immutable-config, and transition-cache count corrections were regression
  tested and changed no scientific parameter. The final failure is a genuine
  context/data constraint rather than an implementation crash.
- The final ledger has `7` closed attempts, `14` events, and no open attempt.

## 2026-08-18 EXP-025C-R missing-control-aware selector validation

VERIFIED:

- The selector ensemble, three seed checkpoints, calibration, predictions,
  rankings, classes, and transition selections are hash-identical to EXP-025C.
- `selector_behavioral_missing_policy_7cr_v1` retains the impossible F5 slot
  as one explicit missing measurement. The final contract is `225` logical,
  `224` executable, and one missing, with no truncation, class fallback,
  changed selection, generated result, or imputation for the missing slot.
- The lifecycle smoke and all `224/224` executable formal conditions pass the
  AppWorld 0.1.0 same-world bridge. There are zero infrastructure exceptions,
  duplicate outputs, or cross-condition contamination events.
- On the primary subset, deployment-E F3 improves action-signature and
  semantic-successor behavior over bare with task-bootstrap intervals excluding
  zero, preserves execution, retains at least 70% of oracle gain on all three
  retention metrics, and has positive relative behavior on `8/9` tasks.
- F3 materially beats its signature-only card, hard negative, popularity, and
  unrelated controls on preregistered primary metrics. Strict-B F1 passes its
  separate behavioral claim and retains two of three oracle gains above 70%.
- The complete-case F3-F5 successor contrast is `+0.16129`, 95% CI
  `[0.06897,0.24138]`; the field-adverse one-row bound remains positive.

INFERENCE:

- Signature-balanced field selection can automatically retrieve useful raw
  episodic transition content. Its behavioral benefit does not reduce to the
  structured signature card, duplicate popularity, or the clean intent-only
  control under this one-step audit.

UNVERIFIED:

- The result does not validate a state-conditioned transition program,
  compiler, additive injector, full trajectory policy, Stage C2, or end-to-end
  RCMF.

Decision:

- Supersede EXP-025C's behavioral-preflight stop with the prospective missing-
  row completion; do not rewrite the EXP-025C artifacts or branch.
- Record `signature_balanced_field_selector_behaviorally_validated`.
- Treat automatic selector behavior as validated on the clean one-step
  contract, while keeping p(s,m_transition) blocked until a separately reviewed
  state-conditioned transition-program distillation milestone.
- Do not start compiler/injector training, Stage C2, end-to-end RCMF, full
  AppWorld evaluation, or V4 tagging.

Implementation recovery:

- Preserve all eight closed attempts. The malformed request SHA and two
  operator transcription failures changed no scientific parameter; the
  immutable manifest supersession and final independent validator both pass.

## 2026-08-18 EXP-025D preflight and runtime review

VERIFIED:

- The selector, clean lineages, source counts, ordered score tensors, and
  strict-B/D/deployment-E frozen selections validate exactly.
- The deterministic pair manifests meet the requested pilot ranges without
  using held-out labels. Over-context rows remain missing with zero truncation
  or class fallback.
- The required top-64 teacher cache has `970` new unique scoreable pairs and
  no complete reusable clean rows.
- The fixed decoder/program schedule projects to `201.72` expected H100 hours
  and `21.46 GB`, above the 12-hour review threshold.

UNVERIFIED:

- Decoder capacity, program fidelity, factorized versus PairMLP behavior,
  B/C/D/E generalization, shuffle sensitivity, and compiled one-step behavior
  have not been run.

Decision:

- Pause the same EXP-025D run UUID at
  `completed_runtime_review_required` and request explicit approval.
- Do not reduce pairs, updates, seeds, architectures, controls, or held-out
  cells merely to fit the review threshold.
- Do not claim a scientific branch or validated state-conditioned program.
  Full-bank work, Stage C2, end-to-end RCMF, and V4 tagging remain blocked.

Implementation recovery:

- Preserve five closed attempts. The final append-only data-manifest
  supersession changes only the `transitions` input from the structural
  manifest to its ID-keyed EXP-025B token-preflight manifest; the original run
  manifest is unchanged and no scientific parameter changed.

## 2026-08-20 EXP-025D-Fast tensor-space stop

VERIFIED:

- The primary program is observation-excluded and records exactly six source
  goal/state/action vectors; observation shuffle invariance passes.
- Incremental field writes match explicit sums and audit rebuilds without
  scanning unrelated records. Read shapes remain fixed.
- A clean no-bias decoder passes, and canonical behavioral pair targets are
  stable across optimizer seeds in latent and decoded-effect space.
- PairMLP fails the preregistered tensor target gate, while the primary
  factorized, static-only, shuffled, free-ID, and outcome-view controls all
  remain near or worse than zero on grouped validation.

INFERENCE:

- Failure occurs at amortization from the current independent frozen state and
  transition representations, not at canonical pair-target construction.
- Adding post-action outcome views is not supported as a repair because that
  diagnostic also fails.

UNVERIFIED:

- No B/C/D/E teacher-forced behavior, H1-H4 generation, AppWorld one-step
  result, full-bank program, Stage C2, or end-to-end result exists.

Decision:

- Record `state_transition_representations_insufficient`.
- Do not start full-bank integration or claim a working compiled program.
- Require separately reviewed pair-aware representation repair before any
  further Qwen or AppWorld program validation.

Implementation recovery:

- Preserve nine closed attempts. GPU-001/002/003 exposed device, decoder-path,
  and stability-call issues; each fix has a regression test and changed no
  scientific parameter. GPU-004 resumed exact u64/u16 checkpoints and reached
  the scientific PairMLP stop.

## 2026-08-21 EXP-025D-Direct factorized behavioral stop

VERIFIED:

- Direct frozen-Qwen training makes the observation-excluded PairMLP pass its
  A/B/E gate, despite the same architecture failing canonical latent-z
  distillation.
- The r16 factorized model passes its A-validation gate after u16 and retains
  positive B/E rank correlation, but B/E utility Huber is worse than zero and
  E does not beat memory swap.
- The selector hash, observation boundary, ratio budget, K=4 injection, demos,
  and frozen-Qwen contract remain intact. No one-step phase ran.

INFERENCE:

- Canonical latent coordinates, rather than pair information itself, caused
  the old PairMLP amortization failure.
- The remaining bottleneck is field-compatible factorization/calibration
  across held-out states, not a general inability to predict pair behavior.

UNVERIFIED:

- No compiled-program generation, one-step AppWorld behavior, full bank,
  Stage C2, or end-to-end result exists.

Decision:

- Record `direct_behavior_factorized_program_failed`.
- Do not run H1-H4 or claim a working compiled program.
- Require immediate project-scope review before any narrow factorization
  repair; do not start p(s,m_transition), compiler/injector, full-bank,
  Stage C2, end-to-end, full AppWorld, or V4-tag work.

Implementation recovery:

- Preserve the rejected config typo and the tokenized-row lookup failure as
  provenance. The latter stopped after teacher-cache completion and before
  any training update; the resumed attempt reused the exact validated cache.
  No scientific parameter or global seed changed.

## 2026-08-21 EXP-025D-G2 calibrated r16 behavioral stop

VERIFIED:

- Exact continuation from the immutable u16 checkpoint reaches u48 and passes
  the A/B/E teacher-forced gate after A-only global-gain selection.
- The H1 compiled intervention improves semantic successor over bare by
  `+0.09375`, but retains only `27.27%` of the raw-transition successor gain,
  loses action-signature accuracy, does not beat shuffled transition, and is
  positive on only `3/9` tasks.
- All final diagnostic bootstrap intervals use the single global seed `25101`.
  The superseded seed-offset analysis is retained only as provenance.

INFERENCE:

- The prior r16 teacher-forced failure was under-convergence, but its one-step
  failure is real under the current compiled intervention.
- Matching scalar memory utility does not preserve the action-level mechanism
  carried by selected raw transition text.

UNVERIFIED:

- No full-bank compiled program, p(s,m_transition), Stage C2, end-to-end RCMF,
  full AppWorld evaluation, or V4 tag exists.

Decision:

- Record `calibrated_factorized_program_not_behaviorally_retained`.
- Do not resume u64, start r64, or begin full-bank integration automatically.
- Preserve the validated clean selector/raw-transition causal result for the
  submission path. Any new compiled-program work requires separate review.

Implementation recovery:

- `train-001` and `train-002` stopped before the first update on atomic-row
  loading and CUDA RNG-device restoration, respectively. Both fixes have
  regression tests and changed no scientific parameter. A postrun analysis-
  only correction removed per-metric bootstrap seed offsets without changing
  training, generations, row metrics, or the scientific branch.

## 2026-08-22 EXP-025D-G3 PairMLP behavioral-retention stop

VERIFIED:

- The immutable Direct PairMLP transfers a positive semantic-successor effect
  over bare, but retains only `36.36%` of raw selected-transition gain and
  fails the required 40% gate.
- The preregistered conditional token-policy pilot improves teacher-forced
  policy KL over zero in every cell but does not improve action signature,
  retains only `27.27%` of successor gain, and equals state/transition shuffle
  controls on both decisive behavior metrics.
- Both one-step phases complete all `270/270` conditions with zero
  infrastructure or execution exceptions under one global seed `25101`.

INFERENCE:

- The behavioral-retention failure is not specific to scalar target-NLL
  utility. Raw-memory response-token distillation also fails to preserve the
  pair-specific one-step mechanism through the current compiled PairMLP.
- r64 capacity is not justified by this result because the required PairMLP
  behavioral upper bound did not pass.

UNVERIFIED:

- Alternative injection/compiler mechanisms, full-bank compilation, and
  end-to-end AppWorld behavior.

Decision:

- Record `teacher_forced_objective_not_behaviorally_retained` and conditional
  result `behavioral_policy_distillation_pairmlp_failed`.
- Do not run r64 or another representation study from this milestone.
- Freeze the compiled-program route for the submission critical path and
  preserve the validated clean selector/raw-transition causal result.

Implementation recovery:

- Ten append-only attempts are closed normally. A shell monitoring connection
  reset during policy training, while Lambda heartbeat/checkpoint writes
  continued; no run, attempt, or condition was duplicated.

## 2026-08-22 EXP-026A direct injection-channel capacity stop

VERIFIED:

- Free per-pair direct DeltaE interventions at K4/K8/K16 eliminate the shared
  decoder, 128D latent, PairMLP, and factorized-program bottlenecks.
- K4 produces a real effect over bare and the cyclic shuffled control, but
  retains only `50.00%` of raw action-signature gain and `63.64%` of raw
  semantic-successor gain. K8 and K16 are weaker.
- No K passes the preregistered 70%/50% channel-capacity gate. All `184/184`
  one-step conditions complete without infrastructure exceptions.

INFERENCE:

- The additive `last_user_k` embedding channel is now the best-supported
  bottleneck for compiled episodic behavior on the submission path.
- Additional r16/r64/PairMLP objective or capacity work is not justified before
  submission because the free per-pair channel oracle itself fails.

UNVERIFIED:

- Alternative intervention sites, full-trajectory behavior, and a deployable
  compiled transition field.

Decision:

- Record `input_embedding_channel_behavioral_capacity_failed`.
- Do not trigger the conditional widened PairMLP, r64, another program
  objective, or full-bank integration.
- Freeze the compiled-program route for the submission. Preserve the clean
  signature-balanced selector and raw-transition one-step causal result as the
  positive contribution; report the channel result as a bounded negative.

Implementation recovery:

- Four append-only stopped attempts preserve a selector-hash typo, a bounded
  parent-cache omission, replay-config unwrapping, and a CPU bootstrap result-
  key mismatch. Regression tests cover the fixes; no scientific parameter,
  completed training phase, or generated condition changed or duplicated.

## 2026-08-22 EXP-026B deep-residual carrier capacity validated

VERIFIED:

- A free pair-specific residual intervention at deterministic layers
  `[7,14,21,28]` and the locked four last-user positions exactly retains the
  raw F3 action-signature and semantic-successor gains on 32 primary states.
- R versus S improves action signature by `+0.3125` and semantic successor by
  `+0.28125`; both task-bootstrap intervals exclude zero. Execution is
  `1.0000`, and `8/9` tasks are positive.
- Zero equivalence, hook locality, nonzero gradients at all active layers,
  decode exclusion, norm budget, and frozen-Qwen checks pass.

INFERENCE:

- The EXP-026A failure was specific to the shallow input-embedding carrier,
  not a general inability of a fixed-size neural intervention to preserve raw
  episodic behavior.
- One narrowly scoped deep-residual compiler experiment is now justified.

UNVERIFIED:

- Whether observation-excluded state/transition representations can amortize
  the free DeltaH oracle; full-bank, multi-step, and end-to-end behavior.

Decision:

- Record `deep_residual_carrier_capacity_validated`.
- Stop EXP-026B without training a compiler. Require separate review for one
  single-seed PairMLP to 256D latent to shared deep-residual decoder at the
  fixed four layers and positions.
- Do not test another carrier, broaden layer/position search, start a full
  bank, p(s,m_transition), Stage C2, end-to-end RCMF, full AppWorld evaluation,
  or create/move a V4 tag.

Implementation recovery:

- Three append-only validation attempts stopped before training on audit
  device placement, unbatched shape handling, and activation-checkpoint hook
  lifetime. Regression tests cover each fix; no scientific parameter, pair
  update, or generated condition was changed or duplicated.

## 2026-08-22 EXP-027A deep-residual amortization failed

VERIFIED:

- Automatic frozen-selector raw memory reaches `5/37` on the paired first-37
  audit, versus bare `10/37`.
- PairMLP teacher-forced fit generalizes across A/B/C/D/E, but the correct
  compiled program does not beat state or transition shuffle in one-step
  behavior.
- P1 retains `25.00%/45.45%` of raw action-signature/semantic-successor gain,
  reduces execution by `9.38` percentage points, and is positive on `4/9`
  tasks.

INFERENCE:

- The validated deep carrier is not the bottleneck. Amortization from current
  observation-excluded representations and objective is the active failure.
- The validated one-step raw-memory intervention is insufficient to support a
  multi-step task-success claim under the present automatic selection loop.

UNVERIFIED:

- An AppWorld-enhanced structured compiler, a fixed trained memory-reader
  adapter, and full-bank compiled behavior.

Decision:

- Record `deep_residual_amortization_failed` and do not run conditional
  factorized Phase D.
- Freeze the compiled-program route for submission review. Do not start a new
  carrier, rank sweep, full bank, p(s,m_transition), Stage C2, end-to-end RCMF,
  full AppWorld evaluation, or create/move a V4 tag.

Implementation recovery:

- Twelve ledger attempts are closed; three preserve bounded infrastructure
  failures. A later import-only preflight failed before ledger initialization;
  commit `8467756c` fixed the internal representation-loader import. Focused
  tests pass locally and on Lambda, and no scientific row was duplicated.

## 2026-08-23 EXP-027B memory-specific deep amortization failed

VERIFIED:

- Exact-harness bare Qwen reaches `8/37`; automatic frozen-selector raw memory
  reaches `5/37`. The old `10/37` bare result is not the primary comparator.
- Raw-policy supervision plus direct alternating transition/state mismatch
  gradients produces positive teacher-forced specificity on A validation and
  B/C/D/E.
- A-only checkpoint selection chose u4. The correct compiler has lower
  raw-policy KL than zero across all final cells and stays within the residual
  ratio budget.
- In the primary one-step audit, P1 action signature and semantic successor are
  `0.40625/0.53125`; P2 and P3 are exactly the same on both metrics.
- P1 improves C0 by `+0.09375/+0.09375`, but execution falls by `6.25` points
  and only `4/9` tasks are positive.

INFERENCE:

- The corrected objective learns teacher-forced policy separation but not
  behaviorally memory-specific deterministic generation.
- More generic PairMLP capacity, rank sweeps, or factorized training is not a
  defensible submission-critical-path response.

UNVERIFIED:

- A compiler with compact AppWorld procedural features available at deployment.

Decision:

- Record `memory_specific_deep_amortization_failed`.
- Stop generic PairMLP/program work for the submission.
- Do not automatically train a memory-reader adapter, rank sweep, factorized
  model, full bank, `p(s,m_transition)`, Stage C2, end-to-end RCMF, full
  AppWorld evaluation, or create/move a V4 tag.
- Permit only a separately reviewed AppWorld-structured compiler rescue, after
  reviewing submission scope and expected value.

Implementation recovery:

- One append-only one-step preflight attempt failed before generation because
  the reused 7F estimator required a legacy runtime key. Commit `bcf7682`
  added a tested fallback to the existing policy-forward estimate. No
  scientific condition, parameter, checkpoint, or generated row changed.

## 2026-08-24 EXP-028A structured compiler competitive, not validated

VERIFIED:

- The deterministic paired panel accounts for every clean train state with
  `464` labeled, `27` over-context, and `8` strict-replay-missing rows. The
  observed labels are `129/300/35` POSITIVE/NEUTRAL/HARMFUL.
- The deployment-available structured gate passes on eight heldout train
  tasks at threshold `0.60`, with `11/98` activations, zero harmful
  activations, and positive successor/signature/execution deltas.
- The structured compiler is `PARTIAL_POSITIVE`: on 32 primary heldout states,
  S1 improves C0 signature and successor by `+0.09375` each and beats both
  shuffles by `+0.03125`, but is positive on only `2/9` tasks.
- Gated raw and gated compiled first37 both score `8/37`, exactly matched bare,
  with zero gate activation (`0/872` and `0/873` turns).

INFERENCE:

- The gate learns a useful train-side causal subset, but its locked activation
  distribution does not transfer to the first37 test-normal stream.
- The small one-step compiler effect is insufficient to justify full-bank
  integration on the submission critical path.

UNVERIFIED:

- End-to-end compiled-memory benefit, a production full bank, cross-seed
  robustness, and generalization beyond the AppWorld feature adapter.

Decision:

- Record `appworld_structured_compiler_competitive`.
- Do not describe first37 gained/lost IDs as causal evidence; there were no
  memory activations in either gated run.
- Stop for review. Do not build the full bank, train another compiler,
  p(s,m_transition), Qwen, Stage C2, end-to-end RCMF, or create/move a V4 tag.
- The only immediate analysis recommended is a deployment-feature-only gate
  distribution audit without using test outcomes.

Implementation recovery:

- Eight strict replay rows remain explicit missing measurements; semantic-v3
  was not relaxed.
- Two training attempts stopped before u2 until residual hooks were retained
  through checkpoint backward. Two validation attempts stopped before
  acceptance until the live projection used same-run bare block-input norms
  and a fixed versioned `0.99` numerical margin. The scientific ratio budget
  remained `<=1.0`; stopped attempts produced no accepted scientific rows.

## 2026-08-24 EXP-028B ungated live specificity failed

VERIFIED:

- The frozen EXP-028A feature order, vocabulary, missing-value handling,
  standardizer, gate checkpoint, temperature `2.0`, threshold `0.60`, and
  inference code match. Recomputed live probabilities differ from saved rows
  by at most `1.1920929e-7`; there is no implementation mismatch.
- Heldout-train activation is `11/98`; first37 activation is zero at every
  preregistered threshold. The live domain has `64/186` features with absolute
  SMD at least `0.5` and a train-vs-live classifier AUC of `0.997624`.
- U0/U1/U2 success is `8/37`, `0/37`, and `2/37`. The correct forced-on
  compiler loses to both bare and its deterministic transition shuffle.
- The U2 map has 499 rows, no fixed transition IDs, and a different signature
  class for every row. No task outcome influenced the mapping.
- All 168 official test-normal tasks are exposed by historical per-task
  evaluation; no untouched task remains for the requested fresh-37 manifest.

INFERENCE:

- The gate's zero live activation reflects broad feature/state distribution
  shift, but this is not the primary remaining blocker: forced-on correct
  compilation is worse than shuffled compilation over full trajectories.
- The small curated one-step structured-compiler effect is not deployably
  memory-specific in live ReAct trajectories.

UNVERIFIED:

- Whether a fixed trained memory-reader adapter can retain selected raw-memory
  behavior, and any statistically reliable task-success claim across seeds.

Decision:

- Record `structured_compiler_live_specificity_failed`.
- Stop structured-compiler work for the submission. Do not lower/retrain the
  gate, build the full bank, start another compiler automatically, train Qwen,
  start Stage C2, or create/move a V4 tag.
- The next review must choose between one tightly bounded fixed memory-reader
  adapter study and narrowing the paper scope to the validated positive and
  negative results already obtained.

Implementation recovery:

- The first U1 attempt stopped when a live state reached 65,267 prompt tokens
  under the locked 40,960 no-truncation contract. The resumed harness records
  locked context overflow as a terminal task failure and continues; prompt,
  context, history, selector, compiler, and evaluation semantics are unchanged.

## 2026-08-24 EXP-029A fixed memory reader failed

VERIFIED:

- On-policy collection and paired outcomes cover `222` clean train-task states
  and `444` T0/T1 conditions, with zero test-normal outcomes used.
- The observed label distribution is `5/210/7` POSITIVE/NEUTRAL/HARMFUL.
  Twenty-one immutable EXP-028A positive expert states augment training only;
  the 48-state heldout split remains purely on-policy.
- The fixed reader passes all implementation invariants, has `2,162,688`
  parameters independent of memory count, and leaves all Qwen parameters
  frozen with zero gradients.
- Training reaches u4 without instability. Across all three heldout
  checkpoints, R1, R2, R3, and R0 have identical action-signature and
  semantic-successor rates (`0.1875/0.1458`). R1 is positive on `0/8` tasks.
- No checkpoint is eligible; first37 is not run.

INFERENCE:

- The learned reader can reduce teacher-policy loss but does not preserve
  memory-specific deterministic behavior on heldout live train-task states.
- Together with the prior generic and structured compiler failures, this is
  sufficient to stop neural compiled-memory architecture work on the
  submission critical path.

UNVERIFIED:

- Whether unbounded training, another reader architecture, or multi-seed work
  could eventually succeed; none is authorized before submission.
- Reader/compiler end-to-end task success and full-bank operation.

Decision:

- Record `fixed_memory_reader_failed`.
- Stop another reader/adapter, PairMLP, factorized-field, full-bank, Qwen,
  Stage C2, end-to-end RCMF, full AppWorld, and V4-tag work for submission.
- Move the next 48 hours to claim locking, tables, limitations,
  reproducibility, and paper writing.

Implementation recovery:

- Four append-only attempts preserve bounded pre-result failures involving a
  DecisionExample field, mixed precision, checkpoint hook lifetime, and live
  bridge condition identity. Each was fixed with focused regression coverage;
  no accepted scientific row or frozen parameter changed.

## 2026-08-25 EXP-030A cross-attention reader failed before field

VERIFIED:

- The borrowed dedicated-memory cross-attention pathway is technically valid:
  zero/no-memory behavior is exact, generated tokens query memory, memory does
  not enter self-attention KV, all 36 fusion layers train, and Qwen remains
  frozen with zero gradients.
- Phase 1 selected epoch 1 on eight heldout train tasks. Phase 2 completed four
  bounded specificity/compliance epochs without changing the single seed or
  the frozen scientific contract.
- At every Phase-2 checkpoint, correct-memory policy KL on heldout POSITIVE
  states exceeds zero-memory KL. The best row is `0.839403` versus `0.583907`.
- Correct memory at the best epoch beats transition and state shuffles
  (`0.976347` and `1.770020`), but the preregistered policy gate requires all
  three strict comparisons.
- No checkpoint is eligible. Heldout live, field, whole-bank, and first37
  conditions did not run. No test-normal outcome was inspected.

INFERENCE:

- The borrowed cross-attention reader can encode memory identity but does not
  provide a policy-useful selected-memory interface under this bounded
  AppWorld curriculum.
- Since the reader gate fails, reversible whole-bank field behavior remains
  unvalidated and cannot be claimed.

UNVERIFIED:

- Whether another reader, more training, another seed, or a different
  curriculum would pass. None is authorized on the submission path.

Decision:

- Record `published_cross_attention_reader_failed_on_appworld`.
- Stop before field construction and do not run the 1,176 live conditions,
  because live behavior cannot make a policy-ineligible checkpoint selectable.
- Freeze compiled-memory architecture work for the submission and move to
  claim locking, tables, limitations, reproducibility, and paper writing.

Implementation recovery:

- Seven failed attempts are preserved append-only. Focused fixes addressed
  checkpoint hook identity, bounded memory use, runner compatibility, and an
  immutable policy-row schema adapter without changing scientific parameters
  or rewriting accepted rows.

## 2026-08-26 EXP-031A complete reversible field has live memory specificity

VERIFIED:

- The complete path is raw transition ledger -> four RCMF writers -> reversible
  full-bank field -> state-conditioned eight-slot read -> four standard
  cross-attention readers -> frozen Qwen. Every scientific nonzero forward
  traverses this path.
- The 401-memory model-training field and 499-memory deployment field have
  fixed A/B shapes `[960,8,256]` and `[8,256]`. Production reads do not loop
  over memories; production add scans no existing record.
- Epoch 2 passes STRONG heldout live validation. L1 correct improves signature
  and successor over zero and both shuffled controls, with no execution loss
  and positive behavior on `4/8` tasks.
- Incremental addition of 98 memories requires no retraining and preserves
  explicit rebuild/remove/restore agreement within the fixed tolerance.
- First37 success is D0/D1/D2 = `8/8/5`. D1-D0 is zero; D1-D2 is +3. No runtime
  retrieval, selected memory, raw memory prompt, or execution exception occurs.
- The Git-safe detailed audit is complete and hash-valid; the Lambda raw logs
  remain immutable. All attempts are closed.

INFERENCE:

- The complete fixed-size RCMF field has a live memory-specific causal effect,
  because correct key-payload association beats a deterministic whole-bank
  shuffle under an otherwise identical single-seed harness.
- The current system does not establish an absolute end-to-end improvement over
  bare Qwen, because D1 equals D0.

UNVERIFIED:

- Multi-seed robustness, significance, second-benchmark portability, and an
  untouched AppWorld final-test result. The official test-normal pool is
  already exposed development data.

Decision:

- Record `rcmf_full_field_live_memory_specific_signal` for the omitted
  D1==D0/D1>D2 boundary. Do not mislabel it as
  `rcmf_full_field_preliminary_positive`, whose gate requires D1>D0.
- Freeze the checkpoint and field. Do not start another component study, broad
  evaluation, portability run, or V5 tag without a separately reviewed
  preregistration.

Implementation recovery:

- Append-only failures preserve one source-manifest parser issue, one
  live-history serialization issue, and four post-science audit-export issues.
  Additional preflight corrections did not alter scientific parameters or
  completed rows.
- The Windows `apply_patch` helper failed with `helper_unknown_error` during
  final audit hardening. Scoped deterministic PowerShell UTF-8 writes were
  used, reviewed with `git diff`, and validated by 23 focused tests.