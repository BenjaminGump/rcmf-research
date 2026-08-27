# EXP-031B RCMF Benefit-Preserving Calibration

## Status

- Run UUID: `rcmf_benefit_preserving_calibration_9b_20260827_001`
- Global seed: `25101`
- Immutable EXP-031A source: `57d2a3479ff292dd8f89bdd0ea9f9417abc42a48`
- EXP-031B working root: `f8b26bb036463683f833e810e6756b6e87cc82ec`
- Formal first37 implementation: `49f03a2b758f93069b768e3af79fbf1f6282befd`
- Strict Git-safe exporter: `6988045d375ec0b5a991aaaab01edd91ee5bd2f8`
- Branch: `research/v5-rcmf-benefit-preserving-calibration`
- Scientific decision: `STOP_ROUTE`
- Decision branch: `benefit_preserving_calibration_stop_route`
- Second first37 candidate pair run: `false`

EXP-031B tested whether outcome-independent calibration of the immutable EXP-031A complete field could preserve the six original gains and both retained successes while reducing harm. It did not retrain any model and did not use retrieval, per-memory runtime scoring, raw-memory prompting, or a hard memory gate.

## Immutable Freeze

The archive branch `archive/exp031a-rcmf-joint-full-bank-57d2a347` and annotated tag `exp031a-rcmf-joint-full-bank-verified-57d2a347` both identify the immutable `57d2a347...` source.

The independent Lambda archive is:

`/lambda/nfs/rcmf-persist/project/archives/exp031a_rcmf_joint_full_bank_57d2a347`

Verified controls:

- repository bundle SHA256: `5518db06ae689c907edbea2663b21f968d2df9fad8fb9bea7a37aa32b7e264ff`
- artifact manifest SHA256: `7fc45583b4beee9cce10f95bc6a824f8b50bae8ace90232e71b43095f5d1bf6a`
- independent snapshot: 11,497 regular files, 3,163,808,663 bytes, 308 directories, zero hard-linked source files
- restoration-smoke SHA256: `f7aa0eab85a727f87cd0584a44d4ad00d7eadf22d7bbf1eceb22b53c1a8748de`
- restoration smoke: D0 and D1 exact token identity passed; checkpoint and deployment-field hashes matched

Immutable scientific identities remained:

- checkpoint: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`
- deployment field: `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`
- parent attempt ledger: `cc1cd9ec3a88d4856c2675c8d7a1c44d21197f8cafb92e6c77f5a2fc97ad8856`
- parent Git-safe audit index: `6075662dcd3897f3147d26d7067e30f4a05d1ce7b478f9a5fc600af08b0d1109`

## Preexisting Scratch Provenance

The preflight preserved all three previously untracked roots before removing their checkout copies:

| Host/root | Files | Dirs | Bytes | Symlinks | Root manifest SHA256 |
|---|---:|---:|---:|---:|---|
| Local `.codex_tmp/` | 844 | 164 | 1,024,839,260 | 0 | `aa1b2ec378fe24ed3b974baa98c66e3cadc48da2f10e10b54cfe3b9cf9e136e7` |
| Lambda `.codex_tmp/` | 71 | 12 | 42,231,169 | 8 | `3a11afee78a9465172db69864f6a08fca48c64ee369840393f21299368b0938a` |
| Lambda byte path `b' \\'` (`IFw=`) | 3 | 0 | 7,077 | 0 | `c624ceabf35a51c210ad10e7316ff47e52a65f352e7ac7c3897d4262b1a7c75c` |

Raw archive roots:

- `C:\gbz\.rcmf_quarantine\exp031b_preflight_20260827T013801Z\local\raw_roots\.codex_tmp`
- `/lambda/nfs/rcmf-persist/quarantine/exp031b_preflight_20260827T013801Z/lambda/raw_roots/.codex_tmp`
- `/lambda/nfs/rcmf-persist/quarantine/exp031b_preflight_20260827T013801Z/lambda/raw_roots/<byte-path-b64:IFw=>`

The content was moved only after destination-manifest verification. No unique untracked file was discarded. Local and Lambda `.git/info/exclude` each received exactly `.codex_tmp/`; tracked `.gitignore` was unchanged and the malformed byte path was not ignored.

The complete 63-script and 176-bundle classification is in [the machine-readable provenance index](../archives/exp031a_preexisting_scratch_provenance.json). Counts are:

- 5 formal EXP-031A audit/export scripts
- 1 formal EXP-031A launch wrapper
- 36 historical pre-EXP-031A scripts
- 21 temporary debugging/test utilities
- 176/176 bundles verified
- 175 bundle tips reachable
- 1 unreachable historical tip byte-equivalent in its four changed files to a reachable commit

Eight EXP-031A-associated scratch sources were preserved under `research/archives/exp031a_run_critical_sources/`. The immutable ledger directly records committed child commands, not every wrapper or read-only helper basename. The audit found no missing scientific semantics and reached `exp031a_formal_execution_provenance_sufficient`; a clean EXP-031A rerun is not required.

The three orphaned `exp025dg-preflight-001` files passed the secret scan and were copied exactly to `research/archives/exp025dg_preflight_001_orphaned_evidence/`. Their SHA256 values are `369ef47a...` (`attempts.jsonl`), `2544fe79...` (`heartbeat.json`), and `9dc3fcc4...` (`run_manifest.json`). This preservation changes no historical decision.

## Candidate Contract

The outcome-independent candidate set contained 22 candidates over 112 states, producing 2,464 cached diagnostic rows:

- exact controls: original, bare, key-payload shuffle
- global scales: `1.00`, `0.75`, `0.50`, `0.25`
- layer scales over layers `[7,14,21,28]`: `L1-L4`
- four leave-one-layer-out diagnostics
- residual trust-region caps: `C50/C75/C90`
- pre-RMS confidence scaling: `Q50/Q75/Q90`
- positive normalized-kernel diagnostic: `E-positive`

No candidate formula used a candidate outcome or first37 result. The locked calibration SHA256 is `f1d0b1b8553f008423d4c00a4637e0f9d1c01444820f6652ac519a39710b7a8c`. Raw-field CV was `0.803213` and p90/p10 was `12.2912`, so the preregistered Route-D spread gate passed.

G100/bare and zero/bare equivalence passed with zero logit error and exact deterministic token/code identity. Trust-region caps and confidence temperatures were locked before candidate diagnostics.

## Critical Gate

The corrected exact-prompt critical replay completed `308/308` conditions over 14 states with zero exceptions. The three candidates advanced to heldout live evaluation were `L1`, `Q50`, and `Q90`. Each preserved all six original gain critical actions and both retained-success critical actions. The earlier invalid exact-prompt harness output remains immutable and excluded.

## Heldout Live Gate

The heldout run completed 882 new conditions plus 294 reused zero controls over 98 states and eight heldout train tasks, with no exceptions.

| Candidate/control | Exact API | Signature | Execution | Successor | Observation |
|---|---:|---:|---:|---:|---:|
| L1 zero | 0.6429 | 0.3980 | 0.9286 | 0.4082 | 0.4768 |
| L1 correct | 0.6735 | 0.4388 | 0.9694 | 0.4082 | 0.4863 |
| L1 key shuffle | 0.6224 | 0.3980 | 0.9592 | 0.3776 | 0.4584 |
| L1 state shuffle | 0.6327 | 0.3673 | 0.9286 | 0.3469 | 0.4311 |
| Q50 correct | 0.6837 | 0.4490 | 0.9388 | 0.4286 | 0.4918 |
| Q90 correct | 0.6837 | 0.4490 | 0.9490 | 0.4184 | 0.4877 |

L1 and Q90 were eligible for sequential first37. L1 was selected first by the preregistered heldout rule; Q90 remained the preregistered second candidate. Q50 failed only its execution gate.

## Sequential First37

Only L1 was run. Every condition used the complete 499-memory field, fixed Qwen, fixed selector, the exact AppWorld harness, seed `25101`, and no runtime retrieval or hard memory gate.

| Condition | Success | Steps | Exceptions | Generated tokens | Prompt tokens |
|---|---:|---:|---:|---:|---:|
| Original D0 bare | 8/37 | historical | historical | historical | historical |
| Original D1 field | 8/37 | historical | historical | historical | historical |
| Original D2 shuffle | 5/37 | historical | historical | historical | historical |
| L1 correct | 7/37 | 1,071 | 0 | 158,651 | 13,772,485 |
| L1 shuffle | 5/37 | 923 | 0 | 96,163 | 11,076,053 |

L1 correct success IDs:

`0d01c76_3`, `325d6ec_1`, `325d6ec_2`, `8749218_1`, `8749218_2`, `8749218_3`, `fd1f8fa_1`.

L1 shuffle success IDs:

`0d01c76_2`, `325d6ec_1`, `325d6ec_2`, `325d6ec_3`, `d6ac34d_3`.

Observed deltas:

- L1 correct minus D0: `-1`
- L1 correct minus L1 shuffle: `+2`
- original gains preserved: `2/6` (`0d01c76_3`, `325d6ec_2`)
- original gains lost: `325d6ec_3`, `634f342_1`, `634f342_2`, `634f342_3`
- retained successes preserved: `2/2` (`8749218_2`, `8749218_3`)
- recovered original D1 losses: `325d6ec_1`, `8749218_1`
- equivalent new gain: `fd1f8fa_1`
- cross-app-import family preserved: yes
- Spotify state-machine family preserved: yes, partially
- exact-set-migration family preserved: no

The mechanical EXP-031A boundary `correct > shuffle` remains true, but it cannot override the benefit-preservation contract. L1 failed the minimum 10/37 success, `+2` over bare, 5/6 gain preservation, and all-three-family gates. Losing four original gains triggers the preregistered stop. Q90 was therefore not run.

## Runtime And Attempts

- append-only attempts: `33`
- ledger rows: `66`
- all attempts closed: `true`
- failed attempts retained: `6`
- accepted H100-active time: `8.2973 h`
- preserved invalid H100-active time: `0.8716 h`
- total accounted H100-active time: `9.1689 h`
- total run wall span: `17.1925 h`
- raw Lambda artifact size: `1,331,710,009 bytes`

Failed attempts were four fail-closed gain/loss redaction recoveries, the initial padded Flash-only cached-forward attempt, and one exact-prompt smoke. No failed attempt was rewritten.

## Git-Safe Audit

The first Stage-8E export completed its internal historical redaction check, but a separate stricter scanner rejected one credential-assignment pattern. That export was never transferred or committed. It was manifest-verified and moved intact to:

`/lambda/nfs/rcmf-persist/quarantine/exp031b_preflight_20260827T013801Z/lambda/failed_git_safe_export_v1`

Quarantined failed-export roots:

- audit: 161 files, 282,067,745 bytes, root manifest `465bdd0b6d5b116ba8247bfc2b37d9687d47363f46c7dc2d44435222d060629c`
- results: 7 files, 2,126,897 bytes, root manifest `cd00edfc0eb5ea8f1abcbb8ea70cc9eb68068572b1105719c2692f23e70bb22b`
- quarantine summary SHA256: `8ee0bcaeca2043f2957ddc11ccb3222698b0e191567b0b3d6487f935d6e60e42`

The replacement exporter applies both historical and strict EXP-031B redaction recursively, then scans every JSON/JSONL/text artifact before atomic publication. The replacement export and an independent local scan passed:

- audit index SHA256: `ac2aad339874f22a179644ee8a5b0758e0556ed378dc673fc4dcd5d306a59976`
- audit verification SHA256: `0de8d11149ab0ed0e2211406f1c6e54336d19286e20dc2303a7655e904498b17`
- result summary SHA256: `cbbe959bf3928dc1d2c886964c2ff59c98aefff322c4c936e98d6eac8795da1f`
- strict audit scan: 40 JSON, 120 JSONL, 3,224 parsed rows after final index/verification writes
- strict result scan: 3 JSON, 4 JSONL, 1,296 parsed rows after the final attempt refresh
- raw JWT matches: `0`
- registered sensitive-observation leaks: `0`

## Verification

- focused EXP-031B tests, local: `44 passed`
- focused EXP-031B tests, Lambda: `44 passed`
- complete repository suite: `671 passed, 1 skipped`
- exact G100/bare and zero/bare deterministic equivalence: passed
- duplicate/row accounting and atomic result gates: passed
- AppWorld exceptions in formal critical, heldout, and first37 conditions: `0`

The Windows `apply_patch` helper repeatedly failed with `helper_unknown_error`. Guarded UTF-8 PowerShell edits were used only after failed helper attempts. One malformed local intermediate edit was restored exactly from the current Git blob before reapplication; it was never committed or synchronized. Syntax checks, `git diff --check`, focused tests, and the complete suite passed afterward.

## Decision

VERIFIED:

- The field still has a live correct-vs-shuffle signal under L1 (`7/37` versus `5/37`).
- L1 does not preserve the original benefit set: it retains only `2/6` original gains and loses the entire exact-set-migration family.
- The immutable no-retraining, no-retrieval, no-hard-gate method contract was preserved.

INFERENCE:

- Outcome-independent scale/cap/confidence calibration can alter the task-level benefit set substantially even when critical one-step and heldout gates pass. Those proxy gates were insufficient to preserve the original complete-trajectory benefits.

UNVERIFIED:

- Q90 complete first37 behavior is unknown because the preregistered stop fired after L1.
- No statistical generalization claim follows from the single exposed first37 seed.

Reached branch: `benefit_preserving_calibration_stop_route`.

Recommended next action: freeze EXP-031A/031B, stop this calibration route, and use the next 48 hours for manuscript integration, complexity/audit tables, limitation wording, and claim review. Do not run Q90, a new calibration family, retraining, retrieval, a hard gate, broader evaluation, or a V5 tag without a new reviewed contract.

## Artifacts

- [Machine summary](exp031b_rcmf_benefit_preserving_calibration/summary.json)
- [Per-task first37 table](exp031b_rcmf_benefit_preserving_calibration/first37_per_task.jsonl)
- [Candidate matrix](exp031b_rcmf_benefit_preserving_calibration/candidate_matrix.json)
- [Git-safe audit index](../audits/rcmf_benefit_preserving_calibration_9b_20260827_001/index.json)
- [Audit verification](../audits/rcmf_benefit_preserving_calibration_9b_20260827_001/verification.json)
- [EXP-031A scratch provenance](../archives/EXP_031A_PREEXISTING_SCRATCH_PROVENANCE.md)
- [Machine-readable provenance](../archives/exp031a_preexisting_scratch_provenance.json)
- [Structured handoff](../handoffs/20260827T212738Z_stage_c_rcmf_benefit_preserving_calibration_9b.md)
- Raw Lambda root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_benefit_preserving_calibration_9b_20260827_001`
