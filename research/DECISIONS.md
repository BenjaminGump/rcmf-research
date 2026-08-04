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
