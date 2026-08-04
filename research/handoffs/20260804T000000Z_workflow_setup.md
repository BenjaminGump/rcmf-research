# Codex Session Handoff

## Session Metadata

- Date: 2026-08-04
- User request: Build a Codex + ChatGPT + GitHub + Lambda closed-loop research
  workflow for RCMF.
- Lambda project path: `/lambda/nfs/rcmf-persist/project`
- Starting branch: `master`
- Starting commit: `11571b0`
- Ending branch: `workflow/research-loop`
- Ending commit: pending until this workflow commit is created

## 1. Requested Goal

Create a durable GitHub-mediated workflow where ChatGPT handles research
analysis and experiment design, Codex handles implementation and Lambda
experiments, and GitHub stores code, configs, concise results, decisions, and
handoffs.

## 2. Initial State

Local and Lambda code were both at `11571b0`. Lambda was idle with one H100
80GB available. No GitHub remote was configured.

## 3. Files Inspected

- `docs/ChatGPT_Codex_GitHub_Lambda_RCMF_闭环研究工作流.md`
- `README.md`
- `.gitignore`
- `pyproject.toml`
- `scripts/`
- `rcmf/`
- `configs/`
- existing AppWorld result documentation in `docs/`

## 4. Changes Made

- Added repository-level Codex workflow rules.
- Added a repository map for ChatGPT/Codex orientation.
- Added research state, architecture, evaluation contract, decisions, failure
  analysis, next experiments, result summaries, and experiment ledger.
- Added a handoff template and this initial workflow handoff.
- Added lightweight `tools/research_ops/` utilities.

## 5. Intended Method vs Actual Implementation

The ChatGPT-generated plan assumes GitHub CLI or GitHub SSH auth is already
available. In the actual local environment, `gh` is missing and GitHub SSH auth
fails. Therefore repository creation and push require user action before the
loop is complete.

## 6. Commands Executed

- `git status --short --branch`
- `git remote -v`
- `gh --version`
- `gh auth status`
- `ssh -T -o BatchMode=yes -o StrictHostKeyChecking=accept-new git@github.com`
- Lambda SSH audit commands for commit, status, GPU, mount, and disk state.

## 7. Validation

Pending after workflow files are added:

- Python compile checks for `tools/research_ops/`.
- `tools/research_ops/validate_research_state.py`.
- Existing project tests if code changes are made. This workflow setup should
  not change training/runtime code.

## 8. Results

No new RCMF experiment was run in this workflow setup step.

## 9. Failed Attempts

- `gh` could not be used because the command is not installed.
- GitHub SSH auth failed with `Permission denied (publickey)`.

## 10. Engineering Workarounds

The workflow structure is built locally first. GitHub repository creation and
push are deferred until the user confirms repository visibility and configures a
writable GitHub auth path.

## 11. Research-Relevant Observations

The most important current research fact remains: semantic retrieval reaches
`4/10` on fixed first-10 but only `7/37` on a stopped partial full run.

## 12. Unresolved Questions for ChatGPT

- Why does semantic retrieval help fixed first-10 but fail to generalize?
- Is the failure primarily state-address collapse, injector disturbance, task
  distribution mismatch, or overfitting to memory examples?

## 13. Exact Reproduction

Read `research/CURRENT_STATE.md` and `research/EVALUATION_CONTRACT.md`.

## 14. Artifact References

Large artifacts remain on Lambda and are referenced in `research/results/`.

## 15. GitHub State

- Commit pushed: no
- Remote: none
- Branch: `workflow/research-loop`
- Working tree clean: pending
