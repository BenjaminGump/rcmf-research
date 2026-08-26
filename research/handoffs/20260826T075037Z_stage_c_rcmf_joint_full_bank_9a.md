# Structured Handoff: EXP-031A

## Scope

Milestone 9A directly trained and evaluated RCMF from the first scientific
forward: complete raw transition ledger, four feed-forward writers, reversible
whole-bank field, fixed eight-slot state-conditioned read, standard four-layer
cross-attention reader, and frozen Qwen generation. EXP-030A artifacts and all
selected-memory/retrieval shortcuts were excluded.

## Verified

- Branch: `research/v5-rcmf-joint-full-bank`.
- Starting/archive SHA: `ebe6cabe8c3c133b40cca80837204f6916edaf7c`.
- Source SHA: `6781787c6f61b18d0b037ecc2ec70ef5b9dc4d61`.
- GPU execution SHA: `f6808b6457bf2f91db9466e1f1b2f831edf39a76`.
- Run UUID: `rcmf_joint_full_bank_9a_20260826_001`; seed `25101` only.
- Source: 499 complete transitions, eight full-section 4096D views per memory,
  zero token subsampling/truncation, and replay-valid lineage.
- Bank split: 401 memories/29 model-training tasks plus 98 memories/eight
  heldout train tasks; 366/98 scoreable query states.
- Addressing: exact frozen-selector `q/k` decomposition in 960 dimensions,
  maximum error `2.8611e-6`, `mu_i=0`, `rho_i=1/T_parent`.
- Field: A `[960,8,256]`, B `[8,256]`, fixed 7,872,512 float32 bytes.
- Writer/reader parameters: `8,949,760/17,860,608`; Qwen frozen and
  gradient-free.
- Training: exactly two epochs, 1,152 backwards, 7,372.685 s. Epoch 2 selected,
  SHA256 `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`.
- Heldout L0/L1/L2/L3 signature is
  `0.397959/0.448980/0.418367/0.397959`; successor is
  `0.408163/0.428571/0.408163/0.397959`; L1 execution is `0.959184`.
- Epoch 2 is STRONG and positive on `4/8` tasks.
- Instant add: 98 memories, mean compile `1.0050 ms`, mean add `0.07019 ms`,
  no old-record scan or retraining, unchanged field shape, rebuild error
  `3.8147e-6`, remove/restore error `9.5367e-7`.
- First37 D0/D1/D2 is `8/37`, `8/37`, `5/37`; D1-D0 is zero and D1-D2 is +3.
  All conditions have zero execution exceptions.
- Every nonzero scientific forward used the complete task-legal field. Runtime
  retrieval, top-k, selected memory, raw-memory prompt text, and per-memory
  scoring are absent.
- Attempts: 34 IDs, 68 rows, 28 completed, six failed, zero open. Final attempt
  `audit-export-008` completed normally.
- Runtime: `8.874528` accounted H100 hours and `11.797571` scientific wall
  hours. Tests: `23 passed`.
- No V5 tag was created or moved.

## Result

The Phase-8 interpretation is `LIVE_MEMORY_SPECIFIC_SIGNAL`: D1 beats the
key-payload shuffled full bank by three tasks and is not below D0. The formal
decision tree omitted the exact D1==D0 boundary, so the recorded operational
branch is:

`rcmf_full_field_live_memory_specific_signal`

This is not branch G (`rcmf_full_field_preliminary_positive`), because D1 does
not exceed D0.

## Inference

- A complete reversible fixed-size RCMF field has a live memory-specific causal
  effect under paired correct-vs-shuffled whole-bank evaluation.
- The present field does not establish absolute end-to-end improvement over
  bare Qwen; task success is equal at 8/37.
- The contribution is stronger than prior selected-memory proxies because all
  valid scientific paths traverse the actual complete field.

## Unverified

- Multi-seed robustness, significance, portability, and performance on an
  untouched AppWorld test pool remain unverified.
- All official test-normal tasks are historically exposed, so first37 is only
  development evidence.
- The omitted boundary needs user/ChatGPT review before authorizing a broader
  evaluation milestone.

## Recovery And Deviations

No scientific design deviation occurred. Append-only failures preserve one
source-manifest parser issue, one live-history serializer issue, and four
audit-export issues. Additional preflight corrections fixed query-shuffle
legality, deterministic CUDA behavior, a selector-SHA typo, and zero-field
teacher validation before selection. Completed scientific rows and frozen
parameters were not rewritten.

The Windows `apply_patch` sandbox helper returned `helper_unknown_error` during
post-run audit hardening; scoped deterministic PowerShell UTF-8 writes were
used instead and the focused suite passed.

## Audit

- Git-safe audit: `research/audits/rcmf_joint_full_bank_9a_20260826_001/`
- Index SHA256: `6075662dcd3897f3147d26d7067e30f4a05d1ce7b478f9a5fc600af08b0d1109`
- Files: 183; bytes: 223,851,504; indexed hash mismatches: 0.
- Heldout condition tensors: 784; first37 step tensors: 2,791.
- Registered sensitive observations: 166; leaks: 0; raw JWT matches: 0.
- Raw Lambda artifacts:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_joint_full_bank_9a_20260826_001`
- Attempt ledger SHA256:
  `cc1cd9ec3a88d4856c2675c8d7a1c44d21197f8cafb92e6c77f5a2fc97ad8856`.

## Next 48 Hours

1. Freeze checkpoint, field, audit, first37 results, and one-seed claim.
2. Review and explicitly resolve the missing D1==D0/D1>D2 branch.
3. Complete write/read complexity benchmarks, audit-derived divergence tables,
   limitations, and manuscript integration without changing the model.
4. Only after review, preregister any broader paired evaluation or portability
   run. Do not start another component study or create a V5 tag.

## GitHub Review Files

- Scientific report: `research/results/EXP_031A_RCMF_JOINT_FULL_BANK.md`
- Machine summary: `research/results/exp031a_rcmf_joint_full_bank/summary.json`
- Attempt ledger copy: `research/results/exp031a_rcmf_joint_full_bank/attempts.jsonl`
- Audit index: `research/audits/rcmf_joint_full_bank_9a_20260826_001/index.json`

## Termination

All run and export attempts are closed. The persistent NFS mount is present.
Ten historical tmux panes remain as idle `bash` shells; two old inert `cat`
log sinks and standard Jupyter/VS Code services remain, but no Qwen, AppWorld,
training, generation, or EXP-031A worker is active. The H100 reports `0 MiB`
and `0%` utilization with no compute process. The instance is safe to
terminate after the final record commit is synchronized.