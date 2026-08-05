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
- Next-iteration active AppWorld configs use `injector.type=additive_token`,
  `position=first_k`, and `num_tokens=4`; old `additive_prefix` remains only as
  a compatibility alias.
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
- Whether the primary raw-text memory teacher labels are good enough for
  student training; the first pilot found useful positive labels but poor
  candidate recall on the all-memory audit subset.
- Whether the new record-level full-bank training improves AppWorld
  performance; no new full training run has started for this iteration.

## Immediate Workflow Status

- Working branch: `workflow/research-loop`.
- Latest pushed and Lambda-synced source commit: `e295a2b`.
- Lambda cannot currently pull GitHub directly because the instance has no
  GitHub private key/deploy key; sync used a local git bundle after pushing to
  GitHub.
- Lambda post-pilot status: no tmux server running and GPU memory/utilization
  reported `0 MiB / 0%`.
- Do not launch full RCMF training until the user and ChatGPT review teacher
  label quality, context feasibility, compute cost, and additive-token smoke
  results.
