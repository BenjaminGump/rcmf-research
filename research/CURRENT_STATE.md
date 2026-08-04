# Current State

Last updated: 2026-08-04.

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
- Codex non-interactive SSH still needs this key loaded into the active
  ssh-agent, or an equivalent non-interactive signing path, before `git push`
  can run from Codex.

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

## INFERENCES

- The semantic-retrieval auxiliary loss improves the fixed first-10 slice but
  does not yet generalize to the broader AppWorld test distribution.
- The remaining problem is likely not the AppWorld prompt or execution loop,
  because the memory-scale-zero control reproduces the bare first-10 baseline.
- The learned memory read still appears too global/state-insensitive, even
  though semantic retrieval improves variation compared with the low-injector
  run.

## GitHub Status

- User-provided repository SSH URL:
  `git@github.com:BenjaminGump/rcmf-research.git`.
- Local `origin` remote is configured as
  `git@github.com:BenjaminGump/rcmf-research.git`.
- No GitHub push has succeeded yet.
- Push from Codex is blocked because the non-elevated sandbox cannot read
  `C:\Users\Admin\.ssh\github_rcmf`, while the elevated non-interactive SSH
  process cannot unlock the key unless it is already loaded into ssh-agent.

## UNVERIFIED

- GitHub visibility: not confirmed by the user yet.
- Whether the repository exists and is private/public.
- GitHub push from Codex: blocked until `github_rcmf` is available through
  ssh-agent or another non-interactive signing path.

## Immediate Workflow Status

- Working branch for workflow setup: `workflow/research-loop`.
- Workflow scaffold commit: `b8c6479`.
- The new workflow docs and tooling should be pushed to GitHub after GitHub auth
  and repository visibility are confirmed.
