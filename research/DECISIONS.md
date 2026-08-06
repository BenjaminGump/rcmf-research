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
