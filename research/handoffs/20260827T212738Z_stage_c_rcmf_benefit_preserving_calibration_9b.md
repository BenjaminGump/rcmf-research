# EXP-031B Structured Handoff

## Identity

- Milestone: EXP-031B RCMF benefit-preserving calibration
- Run UUID: `rcmf_benefit_preserving_calibration_9b_20260827_001`
- Seed: `25101`
- Immutable EXP-031A source: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- EXP-031B branch root: `f8b26bb036463683f833e810e6756b6e87cc82ec`
- Formal first37 source: `49f03a2b758f93069b768e3af79fbf1f6282befd`
- Strict export source: `6988045d375ec0b5a991aaaab01edd91ee5bd2f8`
- Branch: `research/v5-rcmf-benefit-preserving-calibration`
- Status: complete
- Decision: `benefit_preserving_calibration_stop_route`

## Verified Scientific Result

- Stage 8A: 22 candidates, 112 states, 2,464 cached rows; exact G100/bare and zero/bare equivalence passed.
- Calibration SHA256: `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`.
- Raw-field CV/p90:p10: `0.803213` / `12.2912`; Route D proceeded.
- Stage 8B: 308/308 corrected exact-prompt critical conditions, zero exceptions. L1/Q50/Q90 preserved 6/6 gain critical actions and 2/2 retained critical actions.
- Stage 8C: 882/882 new conditions, 294 zero reuses, zero exceptions. L1 and Q90 eligible; L1 selected first.
- Stage 8D L1 correct: 7/37; L1 shuffle: 5/37; original bare: 8/37.
- L1 correct minus bare: `-1`; L1 correct minus shuffle: `+2`.
- Original gains preserved: 2/6; retained successes preserved: 2/2.
- Lost original gains: `325d6ec_3`, `634f342_1`, `634f342_2`, `634f342_3`.
- Entire exact-set-migration benefit family lost.
- Recovered prior losses: `325d6ec_1`, `8749218_1`; equivalent new gain: `fd1f8fa_1`.
- Q90 complete first37 was not run after the preregistered L1 stop.

The machine finalizer also reports the old mechanical label `rcmf_full_field_live_memory_specific_signal` because correct exceeds shuffle. That label is descriptive only. The benefit-preservation gates control EXP-031B and require STOP_ROUTE.

## Runtime And Ledger

- Attempts: 33
- Ledger rows: 66
- All attempts closed: yes
- Failed attempts retained: 6
- Accepted H100-active: 8.2973 h
- Preserved invalid H100-active: 0.8716 h
- Total accounted H100-active: 9.1689 h
- Wall span: 17.1925 h
- Raw artifact size: 1,331,710,009 bytes
- Raw artifact root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_benefit_preserving_calibration_9b_20260827_001`

## EXP-031A Freeze

- Archive branch: `archive/exp031a-rcmf-joint-full-bank-57d2a347` at `57d2a347...`
- Annotated tag: `exp031a-rcmf-joint-full-bank-verified-57d2a347` targeting `57d2a347...`
- Archive root: `/lambda/nfs/rcmf-persist/project/archives/exp031a_rcmf_joint_full_bank_57d2a347`
- Bundle SHA256: `5518db06ae689c907edbea2663b21f968d2df9fad8fb9bea7a37aa32b7e264ff`
- Independent artifact manifest: `7fc45583b4beee9cce10f95bc6a824f8b50bae8ace90232e71b43095f5d1bf6a`
- Restoration smoke SHA256: `f7aa0eab85a727f87cd0584a44d4ad00d7eadf22d7bbf1eceb22b53c1a8748de`
- Checkpoint/deployment field: `d11e9d8e...` / `5fe48fc2...`

## Preflight Quarantine And Provenance

Exact raw archives:

- `C:\gbz\.rcmf_quarantine\exp031b_preflight_20260827T013801Z\local\raw_roots\.codex_tmp`
- `/lambda/nfs/rcmf-persist/quarantine/exp031b_preflight_20260827T013801Z/lambda/raw_roots/.codex_tmp`
- `/lambda/nfs/rcmf-persist/quarantine/exp031b_preflight_20260827T013801Z/lambda/raw_roots/<byte-path-b64:IFw=>`

Root manifest SHA256 values:

- local `.codex_tmp`: `aa1b2ec378fe24ed3b974baa98c66e3cadc48da2f10e10b54cfe3b9cf9e136e7`
- Lambda `.codex_tmp`: `3a11afee78a9465172db69864f6a08fca48c64ee369840393f21299368b0938a`
- anomalous `b' \\'`: `c624ceabf35a51c210ad10e7316ff47e52a65f352e7ac7c3897d4262b1a7c75c`

All contents were preserved byte-for-byte and destination-verified before the checkout roots were removed. No unique untracked file was discarded. `.git/info/exclude` contains exactly the local-only `.codex_tmp/` addition on both hosts; tracked `.gitignore` is unchanged.

Script classification:

- 5 formal EXP-031A audit/export
- 1 formal launch wrapper
- 36 historical pre-EXP-031A
- 21 temporary debug/test

The eight EXP-031A-associated scratch sources and exact execution-evidence fields are preserved in `research/archives/exp031a_run_critical_sources/` and indexed in `research/archives/exp031a_preexisting_scratch_provenance.json`. Committed child commands are directly recorded; wrapper/helper execution is explicitly marked unconfirmed when no immutable command exists.

Bundle classification:

- 176/176 verify
- 175 reachable tips
- 1 unreachable historical tip semantically duplicated byte-for-byte in its four changed files by a reachable commit

Final provenance decision: `exp031a_formal_execution_provenance_sufficient`. Scientific semantics missing from Git: no. Clean EXP-031A rerun required: no.

Orphaned EXP-025D-G evidence was preserved exactly under `research/archives/exp025dg_preflight_001_orphaned_evidence/`; all three files are Git-safe. This is historical preservation only.

## Failed Export Preservation

Stage-8E attempt 001 passed the inherited scanner but failed a later strict scan. No part was transferred or committed. It was moved, after pre/post manifest equality, to:

`/lambda/nfs/rcmf-persist/quarantine/exp031b_preflight_20260827T013801Z/lambda/failed_git_safe_export_v1`

- audit root: 161 files, 282,067,745 bytes, manifest `465bdd0b6d5b116ba8247bfc2b37d9687d47363f46c7dc2d44435222d060629c`
- result root: 7 files, 2,126,897 bytes, manifest `cd00edfc0eb5ea8f1abcbb8ea70cc9eb68068572b1105719c2692f23e70bb22b`
- summary SHA256: `8ee0bcaeca2043f2957ddc11ccb3222698b0e191567b0b3d6487f935d6e60e42`

Attempt 002 used recursive strict redaction and pre-publication scans. Final audit hashes:

- index: `ac2aad339874f22a179644ee8a5b0758e0556ed378dc673fc4dcd5d306a59976`
- verification: `0de8d11149ab0ed0e2211406f1c6e54336d19286e20dc2303a7655e904498b17`
- machine summary: `cbbe959bf3928dc1d2c886964c2ff59c98aefff322c4c936e98d6eac8795da1f`
- raw JWT matches: 0
- registered leaks: 0

## Verification

- Focused local: 44 passed
- Focused Lambda: 44 passed
- Complete repository: 671 passed, 1 skipped
- Formal critical/heldout/first37 exceptions: 0
- No runtime retrieval, per-memory scoring, hard gate, retraining, prompt truncation, or post-hoc candidate change

Implementation note: `apply_patch` repeatedly failed with the Windows helper `helper_unknown_error`. Narrow guarded UTF-8 PowerShell edits were used. A malformed uncommitted local intermediate was restored from the exact current Git blob before reapplication. Nothing malformed reached Lambda or Git.

## Artifacts For Review

- `research/results/EXP_031B_RCMF_BENEFIT_PRESERVING_CALIBRATION.md`
- `research/results/exp031b_rcmf_benefit_preserving_calibration/summary.json`
- `research/results/exp031b_rcmf_benefit_preserving_calibration/first37_per_task.jsonl`
- `research/audits/rcmf_benefit_preserving_calibration_9b_20260827_001/index.json`
- `research/audits/rcmf_benefit_preserving_calibration_9b_20260827_001/verification.json`
- `research/archives/EXP_031A_PREEXISTING_SCRATCH_PROVENANCE.md`
- `research/archives/exp031a_preexisting_scratch_provenance.json`

## Recommended Next 48 Hours

Freeze this route. Do not run Q90, add a candidate, retrain, introduce retrieval or a hard gate, expand first37, or tag V5. Use the next 48 hours for paper tables, claim-boundary review, complexity reporting, failure analysis, reproducibility links, and explicit limitations. Any new scientific run requires a separately reviewed milestone.

## Termination

Lambda was intentionally not terminated. Final tmux/process/GPU/mount status is recorded after final Git synchronization in the closing response.
