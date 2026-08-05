# Codex Session Handoff

## Session Metadata

- Date: 2026-08-05.
- User request: Milestone 3B only, expand the all-legal teacher audit to all
  24 pilot states, run full-cache preflight, set `last_user_k` K=4 as
  provisional default, and stop before full teacher-cache generation or student
  training.
- Lambda project path: `/lambda/nfs/rcmf-persist/project`.
- Starting branch: `workflow/research-loop`.
- Starting commit requested by user:
  `b45d9da025a00f02cdf66f4f714b5dd0a24672d1`.
- Source commit used for Lambda audit:
  `964063416a2fc3c48bf04bb11db7354fac96028c`.
- Ending branch: `workflow/research-loop`.
- Final record commit: created after this handoff is written.

## 1. Requested Goal

Run an expanded all-legal teacher audit over the existing 24 pilot states,
reuse cached rows, compute recall/regret/coverage/source ablations, verify
deterministic scoring, inspect representative prompts, preflight the complete
638-state all-legal cache, recommend A/B/C, and stop.

## 2. Files Changed

- `configs/benchmark/appworld_rcmf_full_prompt.yaml`
- `scripts/run_raw_text_teacher_audit3b.py`
- `tests/test_raw_text_teacher_audit3b.py`
- `research/CURRENT_STATE.md`
- `research/NEXT_EXPERIMENTS.md`
- `research/DECISIONS.md`
- `research/experiments.jsonl`
- `research/results/raw_text_teacher_audit3b_20260805_001.md`
- `research/handoffs/20260805T044500Z_raw_text_teacher_audit3b.md`

## 3. Implementation Summary

VERIFIED:

- Added `raw_text_memory_teacher_audit3b_v1`.
- The script reads the existing Milestone 3 pilot directory and reuses
  `pilot_states.json`, `teacher_labels.jsonl`, and `representation_cache.pt`.
- The audit scores every legal memory for all 24 pilot states, excluding same
  task, episode, replay, and lineage.
- Existing cached rows are copied into the audit3B output with normalized
  proposal metadata; missing legal rows are scored with frozen Qwen target
  loss.
- Over-context rows are recorded and masked; no prompt, raw memory, or target
  is truncated.
- Full-cache preflight writes exact token rows for the full 638-state dataset
  without scoring all pairs.
- Added metrics for recall@1/2/4/8, regret, thresholded recall/regret, positive
  mass coverage, and source ablations.
- Added deterministic positive/neutral/negative reproducibility rescoring.
- Added representative prompt inspection for leakage and delimiter errors.
- Set `configs/benchmark/appworld_rcmf_full_prompt.yaml` to provisional
  `injector.position=last_user_k`, `num_tokens=4`.

## 4. Validation

Local:

```powershell
python -m py_compile scripts\run_raw_text_teacher_audit3b.py scripts\run_raw_text_teacher_pilot.py scripts\smoke_additive_token_positions.py
python -m pytest -q tests\test_raw_text_teacher_audit3b.py tests\test_raw_text_teacher_pilot.py tests\test_prefix_injection.py
python -m pytest -q
```

VERIFIED:

- Targeted tests: `11 passed`.
- Full local tests: `48 passed`.
- Lambda full tests at source commit `9640634`: `48 passed`.

## 5. Lambda Command

```bash
cd /lambda/nfs/rcmf-persist/project
/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_raw_text_teacher_audit3b.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --pilot-dir runs/teacher/raw_text_pilot_20260805_001 \
  --output-dir runs/teacher/raw_text_audit3b_20260805_001 \
  --representation-batch-size 1
```

## 6. Results

VERIFIED:

- Artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001`.
- 24-state legal pairs: 1,080.
- Scored rows: 1,052.
- Over-context masked rows: 28.
- Cached rows reused: 260.
- Newly scored rows: 802.
- Utility counts positive/neutral/negative: 364/122/566.
- Utility mean/std/min/max:
  `0.047545` / `0.350456` / `-1.243614` / `1.620315`.
- Existing proposal recall@1/2/4/8: `1/24` for every K.
- Mean/median/max regret: `0.275401` / `0.104668` / `1.108213`.
- Positive utility mass coverage: `0.107657`.
- Reproducibility check passed with L0/Lj/utility diffs all `0.0`.
- Representative prompt inspection obvious issue count: 0.
- Full-cache preflight: 638 states, 46 memory records, 28,710 exact legal
  pairs, 27,054 scoreable pairs, 1,656 over-context pairs.
- Estimated complete all-legal teacher-cache scoring cost: `11.31` H100 hours.
- Lambda post-run status: no tmux server and GPU `0 MiB / 0%`.

## 7. Recommendation

Recommendation: A, generate the complete all-legal teacher cache after user and
ChatGPT review.

Rationale:

- Reproducibility check passed.
- Representative prompt inspection found no obvious leakage or delimiter issue.
- Positive and negative utility signal exists.
- Full-cache preflight shows context masking is manageable.
- Estimated cost is moderate.
- All-legal scoring removes the candidate-recall bottleneck; the recommendation
  is not based only on candidate recall.

## 8. Deviations and Workarounds

VERIFIED:

- No full teacher-cache generation, full student training, or full AppWorld
  evaluation was launched.
- No truncation was used.
- Over-context pairs were recorded and masked.
- Lambda still cannot pull GitHub directly; sync used the existing git bundle
  fallback.

## 9. Next Gate

- Stop until the user and ChatGPT review the expanded audit.
- If option A is approved, generate the complete all-legal teacher cache first.
- Do not start student training until the complete cache itself is reviewed.

## 10. Artifact References

- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/summary.json`.
- Audit labels:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/teacher_labels_audit3b.jsonl`.
- Per-state CSV:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/per_state_table.csv`.
- Full-cache preflight summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/full_cache_preflight_summary.json`.
- Full-cache preflight rows:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/full_cache_preflight_rows.jsonl`.
- Reproducibility check:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/reproducibility_check.json`.
- Prompt inspection:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_audit3b_20260805_001/representative_prompt_inspection.json`.
- Local result report:
  `research/results/raw_text_teacher_audit3b_20260805_001.md`.
