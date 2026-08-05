# Codex Session Handoff

## Session Metadata

- Date: 2026-08-05.
- User request: Milestone 3C only, generate the complete all-legal raw-text
  teacher cache for all 638 decision states, validate/report it, and stop
  before RCMF student training or full AppWorld evaluation.
- Lambda project path: `/lambda/nfs/rcmf-persist/project`.
- Starting branch: `workflow/research-loop`.
- Starting commit requested by user:
  `7daef027909b1db6f5ae2111ee6bd42321235b8b`.
- Source implementation commit used on Lambda:
  `80bebb05d97ec7d156b87850a7f1fd2811874d8a`.
- Ending branch: `workflow/research-loop`.
- Final record commit: created after this handoff is written.

## 1. Requested Goal

Generate the complete all-legal raw-text teacher cache, preserving the formal
leakage definition and no-truncation contract, reusing compatible cached rows
from Milestones 3 and 3B, validating all rows, producing comprehensive reports,
creating a future task-grouped student split manifest, and stopping.

## 2. Files Changed

- `scripts/run_raw_text_teacher_full_cache.py`
- `tests/test_raw_text_teacher_full_cache.py`
- `research/CURRENT_STATE.md`
- `research/NEXT_EXPERIMENTS.md`
- `research/DECISIONS.md`
- `research/experiments.jsonl`
- `research/results/raw_text_teacher_full_cache_20260805_001.md`
- `research/handoffs/20260805T170500Z_raw_text_teacher_full_cache.md`

## 3. Implementation Summary

VERIFIED:

- Added `raw_text_memory_teacher_full_cache_v1`.
- Reused the existing frozen-Qwen teacher definition from the pilot/audit
  scripts. No teacher-definition change was made.
- The script computes L0 once per state and Lj_text for every legal scoreable
  state-memory pair.
- Legal memories exclude same task, episode, replay, and lineage.
- Over-context rows are recorded and masked with null utility and
  `valid_for_loss=false`.
- Existing cached rows are reused only after compatibility validation of model
  identity, renderer version, target hash, memory hash, and scoring definition.
- Output is resumable and idempotent through unique pair keys, existing-row
  validation, progress checkpoints, and a final duplicate-free JSONL rewrite.
- Added validation, deterministic reproducibility repeats, per-state,
  per-memory, stratified, overlap, missingness, audit-comparison,
  representative-inspection, and future-split reports.

## 4. Validation Before Long Run

Local:

```powershell
python -m py_compile scripts\run_raw_text_teacher_full_cache.py scripts\run_raw_text_teacher_audit3b.py scripts\run_raw_text_teacher_pilot.py
python -m pytest -q tests\test_raw_text_teacher_full_cache.py tests\test_raw_text_teacher_audit3b.py tests\test_raw_text_teacher_pilot.py
python -m pytest -q
```

VERIFIED:

- Targeted local tests: 9 passed.
- Full local tests before the run: 52 passed.
- Lambda tests at source commit `80bebb0`: 52 passed.

## 5. Lambda Command

```bash
cd /lambda/nfs/rcmf-persist/project
tmux new-session -d -s raw_teacher_full_cache_20260805 \
  '/home/ubuntu/venvs/rcmf-py311/bin/python scripts/run_raw_text_teacher_full_cache.py \
    --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
    --data runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
    --pilot-dir runs/teacher/raw_text_pilot_20260805_001 \
    --audit3b-dir runs/teacher/raw_text_audit3b_20260805_001 \
    --output-dir runs/teacher/raw_text_full_cache_20260805_001 \
    --progress-interval-s 300 \
    > runs/teacher/raw_text_full_cache_20260805_001/run.log 2>&1'
```

## 6. Results

VERIFIED:

- Artifact directory:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001`.
- States: 638.
- Memories: 46.
- Legal pairs: 28,710.
- Scoreable pairs: 27,054.
- Over-context pairs: 1,656.
- Reused compatible cached pairs: 1,080.
- Newly scored pairs: 26,002.
- Newly generated over-context rows: 1,628.
- Failed pairs: 0.
- Retried pairs: 0.
- Runtime: `36,949.37` seconds.
- Actual H100 hours: `10.26`.
- Utility counts positive/neutral/negative: 13,426 / 4,861 / 8,767.
- Utility mean/std/min/max:
  `0.085425` / `0.335507` / `-1.996295` / `2.333721`.
- Utility percentiles: p05 `-0.352794`, p25 `-0.056140`,
  p50 `0.009323`, p75 `0.179088`, p95 `0.750906`.

## 7. Validation and Diagnostics

VERIFIED:

- `validation.json` passed with error count 0.
- No duplicated state-memory keys.
- No missing legal keys.
- No unexpected legal keys.
- No illegal same-task, same-episode, same-replay, or same-lineage pairs.
- No truncation was used.
- All scored rows had finite L0, Lj_text, and utility.
- All over-context rows had null utility and `valid_for_loss=false`.
- Reproducibility passed for deterministic positive, neutral, and negative rows.
- Representative inspection selected 30 rows and found 0 obvious issues.

Overlap diagnostics:

- Utility vs shared API count correlation: `0.133437`.
- Utility vs normalized target exact-substring-in-memory correlation:
  `0.074342`.
- Utility vs shared code-token count correlation: `0.158156`.
- Utility vs code-token Jaccard correlation: `0.138547`.
- Exact target substring present rows had mean utility `0.125319`; absent rows
  had mean utility `0.069831`.

Missingness:

- States with no positive valid memory: 113.
- States with no negative valid memory: 94.
- Largest missing-memory contributor:
  `076f5673-6565-5f20-aada-6f16a0f8d4b0`, over-context for 602 legal states
  and valid for 0.
- Top over-context states were late `afc0fce_1` steps with 45 over-context
  memories and 0 valid memories.

Audit comparison:

- The 24-state audit was not fully representative.
- Full positive/neutral/negative proportions:
  `0.496267/0.179678/0.324056`.
- Audit3B positive/neutral/negative proportions:
  `0.346008/0.115970/0.538023`.
- Full mean utility minus audit3B mean utility: `0.037879`.

## 8. Deviations and Workarounds

VERIFIED:

- No deviation from the Milestone 3C teacher definition.
- No student training or full AppWorld evaluation was launched.
- The documented Lambda GitHub bundle sync fallback remains necessary because
  Lambda still has no GitHub private key/deploy key.
- The script's progress ETA was conservative/unstable early because reused
  cached rows are completed immediately; final runtime is the source of truth.

## 9. Next Gate

- Review teacher-label quality, missingness, and overlap diagnostics with the
  user and ChatGPT.
- Do not launch RCMF student training until review approves how to transform
  this cache into training labels.
- Likely next implementation task is a review-gated student-label compiler that
  uses the task-grouped split manifest and explicitly handles over-context and
  all-missing states.

## 10. Artifact References

- Cache JSONL:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/teacher_cache_full_rows.jsonl`.
- Summary:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/summary.json`.
- Validation:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/validation.json`.
- Report:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/report.md`.
- Reproducibility:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/reproducibility_check.json`.
- Representative inspection:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/representative_inspection.json`.
- Student split manifest:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/student_split_manifest.json`.
- Run log:
  `/lambda/nfs/rcmf-persist/project/runs/teacher/raw_text_full_cache_20260805_001/run.log`.
