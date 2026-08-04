# Decisions and Deviations

## 2026-08-04 workflow setup

VERIFIED:

- The ChatGPT-generated workflow document assumes GitHub CLI can be used, but
  `gh` is not installed on the local Windows host.
- Local GitHub SSH auth currently fails with `Permission denied (publickey)`.
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

- Use `git@github.com:BenjaminGump/rcmf-research.git` as `origin` once Codex
  can complete non-interactive SSH signing.
- Prefer a private GitHub repository unless the user explicitly chooses public
  visibility, because the repository contains AppWorld synthetic credentials
  and detailed research artifacts.

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
