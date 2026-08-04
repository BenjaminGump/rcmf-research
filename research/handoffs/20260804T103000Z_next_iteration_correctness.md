# Codex Session Handoff

## Session Metadata

- Date: 2026-08-04.
- User request: implement the next-iteration changes from
  `docs/RCMF_Next_Iteration_Codex_Task.md`, record any deviations, and prepare
  the project for the next Lambda smoke/training cycle.
- Lambda project path: `/lambda/nfs/rcmf-persist/project`.
- Starting branch: `workflow/research-loop`.
- Starting commit: `2a2e4dc`.
- Ending branch: `workflow/research-loop`.
- Ending commit: pending final record commit; latest Lambda-validated code
  commit is `9fb0817`.

## 1. Requested Goal

Adjust the current AppWorld RCMF experiment so the next iteration no longer
simplifies memory-bank semantics or representation paths. Do not launch an
expensive full run before correctness and diagnostics pass.

## 2. Initial State

VERIFIED:

- Bare Qwen full baseline: `53/168 = 31.55%`.
- Semantic-retrieval RCMF first-10: `4/10`.
- Semantic-retrieval RCMF partial full: `7/37`.
- Active branch before work: `workflow/research-loop`.

## 3. Files Inspected

- `docs/RCMF_Next_Iteration_Codex_Task.md`
- `rcmf/benchmarks/appworld/prompt.py`
- `rcmf/benchmarks/appworld/agent.py`
- `rcmf/training/datasets.py`
- `scripts/train.py`
- `scripts/compile_memory.py`
- `scripts/inspect_memory_injection_stats.py`
- `rcmf/injection/prefix.py`
- `rcmf/model/backends/hf_qwen.py`
- `rcmf/model/backends/mock.py`
- `research/CURRENT_STATE.md`
- `research/DECISIONS.md`

## 4. Changes Made

VERIFIED:

- Added canonical AppWorld full-demo message rendering via
  `build_appworld_messages()`.
- Training and evaluation qwen_hidden state representations now share the same
  full-demo chat-message renderer and Qwen chat template path.
- Training target ids append EOS, and labels remain `-100` for prompt tokens.
- qwen_hidden memory records are represented at record level:
  chunk hidden states are token-weighted into one representation and compiled
  once.
- `support_mode=all_except_current_task` now excludes task, episode, replay,
  and lineage keys, not only task id.
- Active AppWorld configs use `additive_token` with `position=first_k` and
  `num_tokens=4`.
- Added `last_prompt_k` and `last_user_k` additive-token configs.
- Added token-selection audit metadata for additive-token injection.
- Added `scripts/audit_memory_record_chunks.py`.
- Replaced `scripts/inspect_memory_injection_stats.py` with JSON/Markdown
  diagnostics for representation, address, memory read, and injector collapse.
- Added `scripts/compare_appworld_success_sets.py`.
- Backfilled first-37 paired success-set report:
  `research/results/rcmf_semretr_vs_qwen_first37_paired_20260804.md`.
- Synced the implementation to Lambda and validated commit `9fb0817`.
- Generated Lambda diagnostics under
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/next_iteration_20260804`.

## 5. Intended Method vs Actual Implementation

VERIFIED:

- The requested full-memory-bank semantics were implemented for training by
  selecting all legal records after leakage filtering.
- The requested one MemoryRecord -> one compiled delta rule was implemented for
  training caches and checkpoint memory compilation.

Deviation:

- The primary raw-text memory teacher was not implemented or run in this pass.
  It remains the next milestone and must use raw Qwen scoring over raw memory
  text, not compiled RCMF leave-one-out labels.
- No new full-size GPU training or AppWorld evaluation was started.
- Lambda could not pull GitHub directly because it has no GitHub private key.
  Sync used a local git bundle uploaded through the existing Lambda SSH key and
  fast-forwarded with `git merge --ff-only FETCH_HEAD`.

Reason:

- The next-iteration task explicitly says not to run expensive full jobs until
  correctness/smoke pass.

## 6. Commands Executed

Local:

```powershell
python -m py_compile scripts\train.py scripts\compile_memory.py scripts\inspect_memory_injection_stats.py scripts\audit_memory_record_chunks.py rcmf\training\datasets.py rcmf\injection\prefix.py rcmf\model\backends\mock.py rcmf\model\backends\hf_qwen.py
python -m pytest -q tests\test_training_smoke.py tests\test_prefix_injection.py tests\test_no_data_leakage.py
python -m pytest -q
```

Lambda read-only artifact summary:

```bash
python - <<'PY'
# Read baseline and RCMF per-task JSON files and compute paired first-37 deltas.
PY
```

## 7. Validation

VERIFIED:

- Local py_compile passed.
- Targeted local tests: `19 passed`.
- Full local tests: `43 passed`.

VERIFIED:

- Lambda-side py_compile passed.
- Lambda-side full pytest: `43 passed`.
- Lambda-side memory chunk audit completed.
- Lambda-side memory-injection diagnostics completed for the legacy
  semantic-retrieval checkpoint.

## 8. Results

VERIFIED:

- First-37 paired result from existing artifacts:
  baseline `10/37`, RCMF `7/37`, retained `5`, lost `5`, gained `2`,
  both failed `25`.
- Result report:
  `research/results/rcmf_semretr_vs_qwen_first37_paired_20260804.md`.
- Memory chunk audit:
  46 records, 46 chunks, 0 multi-chunk records, max 35,566 tokens under the
  40,960-token model limit.
- Legacy semantic-retrieval diagnostics:
  memory_z pairwise cosine mean `0.999994`, memory_z mean direction norm
  `0.999997`, address top1 max load fraction `0.448276`.

## 9. Failed Attempts

- Initial direct SSH from the local sandbox failed with permission denied
  because key/network access needs escalation. The read-only Lambda artifact
  summary succeeded after approved SSH escalation.
- Lambda `git pull origin workflow/research-loop` failed with
  `Permission denied (publickey)`. The resolved path was the documented git
  bundle fallback.

## 10. Engineering Workarounds

- Kept the old `all_except_current_task` CLI name but upgraded behavior to
  stricter task/episode/replay/lineage exclusion for compatibility.
- Kept deprecated `additive_prefix` as a compatibility alias.
- Kept virtual-token `prefix` in factory/tests for historical reproducibility
  but removed it from active AppWorld configs.
- Used git bundle sync rather than configuring GitHub credentials on Lambda.

## 11. Research-Relevant Observations

VERIFIED:

- The semantic-retrieval partial full candidate is worse than bare Qwen on the
  paired 37-task slice.

INFERENCES:

- The previous first-10 gain is not enough evidence for a robust memory
  mechanism.
- The next meaningful full run should wait for Lambda smoke diagnostics.

## 12. Unresolved Questions for ChatGPT

- How to design the primary raw-text memory teacher so it is faithful but
  affordable.
- Which retained/gained/lost tasks are most informative for trace-level failure
  taxonomy.
- Whether additive-token position should move from `first_k` to `last_user_k`
  after smoke diagnostics.

## 13. Exact Reproduction

Local tests:

```powershell
$py='C:\Users\Admin\miniconda3\envs\appworld_env\python.exe'
& $py -m pytest -q
```

Future Lambda smoke should start from:

```bash
cd /lambda/nfs/rcmf-persist/project
source /home/ubuntu/venvs/rcmf-py311/bin/activate
python -m py_compile scripts/train.py scripts/compile_memory.py scripts/inspect_memory_injection_stats.py scripts/audit_memory_record_chunks.py
python -m pytest -q tests/test_training_smoke.py tests/test_prefix_injection.py tests/test_no_data_leakage.py
```

## 14. Artifact References

- Baseline:
  `/lambda/nfs/rcmf-persist/project/runs/experiments/qwen_appworld_full_prompt_context40_newline_full_20260731_235900/evaluate/test`.
- Candidate:
  `/lambda/nfs/rcmf-persist/project/runs/experiments/rcmf_appworld_full_prompt_filtered_no_2a163ab3_semretr_full_20260803_172000/evaluate/test`.
- Local report:
  `research/results/rcmf_semretr_vs_qwen_first37_paired_20260804.md`.
- Chunk audit:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/next_iteration_20260804/memory_record_chunk_audit.json`.
- Diagnostics:
  `/lambda/nfs/rcmf-persist/project/runs/diagnostics/next_iteration_20260804/memory_injection_diagnostics_semretr_legacy_statecache.json`.

## 15. GitHub State

- Commit pushed: `9fb0817` and final record commit pending.
- Remote: `git@github.com:BenjaminGump/rcmf-research.git`.
- Branch: `workflow/research-loop`.
- Working tree clean: pending final record commit.
