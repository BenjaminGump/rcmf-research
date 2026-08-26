# EXP-031A Direct Joint Full-Bank RCMF

## Decision

EXP-031A verified a live memory-specific effect from the complete reversible
RCMF field, but did not improve first37 task success over bare Qwen.

The exact outcome is:

`rcmf_full_field_live_memory_specific_signal`

This label covers a preregistered decision-tree boundary that was described in
the Phase-8 interpretation rules but omitted from branches E-G: D1 equals D0
while D1 is greater than D2. The run does not qualify for
`rcmf_full_field_preliminary_positive`, because that branch requires D1 > D0.

## Verified Scope

- Run UUID: `rcmf_joint_full_bank_9a_20260826_001`.
- Branch: `research/v5-rcmf-joint-full-bank`.
- Starting/archive SHA: `ebe6cabe8c3c133b40cca80837204f6916edaf7c`.
- Source implementation SHA: `6781787c6f61b18d0b037ecc2ec70ef5b9dc4d61`.
- Formal GPU execution SHA: `f6808b6457bf2f91db9466e1f1b2f831edf39a76`.
- Seed: `25101` only.
- Qwen3-8B remained frozen and gradient-free.
- Every nonzero scientific forward used the complete task-legal whole-bank
  RCMF field. No selected-memory, retrieval, top-k, FAISS, per-memory runtime
  scoring, or raw memory text entered the query prompt.
- EXP-030A checkpoints, slots, and policy gate were not used.

## Architecture

Each memory contains complete goal, pre-action state, action, and post-action
observation sections. Mean and final-token pooling produce eight aligned 4096D
views. Four section-specific writers each use
`LayerNorm(4096) -> Linear(512) -> GELU -> Linear(256)`, sharing parameters
between mean/final views and adding a learned pooling-type embedding. The
result is `C_i in R^(8 x 256)`.

The frozen selector was exactly decomposed into `q(s), k_i in R^960` within
`2.8611e-6` maximum error. Its intercept is global rather than memory-specific,
so `mu_i=0`; the write weight is `rho_i=1/T_parent`.

The reversible field is:

- `A = sum_i rho_i outer(k_i, C_i)`, shape `[960,8,256]`;
- `B`, shape `[8,256]`, zero in this run;
- read `RMSNorm(B + einsum(q(s), A))`, shape `[8,256]`.

Four independent readers at Qwen layers `[7,14,21,28]` use standard 8-head
cross-attention (`attention_dim=512`, eight heads x 64) from Qwen hidden states
to the same eight field slots, followed by a zero-initialized `512 -> 4096`
output projection. Writers have `8,949,760` trainable parameters; readers have
`17,860,608`; total trainable parameters are `26,810,368`.

## Source And Algebra Validation

All 499 memories pass complete-section and replay-lineage provenance. The
source cache has shape `[499,8,4096]`; maximum complete render length is 35,636
tokens. Token subsampling, arbitrary truncation, dropped suffixes, and
`full_transition_global` use are all zero.

The model-training bank contains 401 memories from 29 tasks. Heldout validation
contains 98 states/memories from eight disjoint train tasks. The field occupies
7,872,512 float32 bytes and its shape is independent of bank size.

Explicit sum/contraction, add/remove/replace, parent remove/restore, insertion
order, same-task subtraction, zero bank, shuffled key-payload control,
fixed-shape, no-read-loop, and no-add-scan tests pass. The full-path smoke used
392 legal memories and passed writer/reader gradient and frozen-Qwen checks.

## Joint Training

Training used 366 scoreable model-training states with labels
`105 POSITIVE / 235 NEUTRAL / 26 HARMFUL`. Each epoch contained 576 units; two
locked epochs completed exactly 1,152 backwards. Training took 7,372.685 s.

- Epoch 1 SHA256: `93e27abb909ef1ffa1789aa209623ee4e1b32b7461130eb4816323346228ce71`.
- Epoch 2 SHA256: `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`.

Every training forward used the complete task-legal field, with same-task
subtraction by precompiled task deltas. No runtime retrieval occurred.

## Heldout Validation

Teacher-forced epoch-2 target NLL for V0/V1/V2/V3 was
`0.172739/0.099314/0.097154/0.101218`; policy KL was
`0.143515/0.069306/0.066183/0.071993`. Live behavior, not these proxy values,
selected the checkpoint.

Epoch 2 was selected on all 98 heldout-train states:

| Condition | Exact API | Signature | Execution | Successor | Obs. similarity |
|---|---:|---:|---:|---:|---:|
| L0 zero | 0.642857 | 0.397959 | 0.928571 | 0.408163 | 0.476828 |
| L1 correct full field | 0.683673 | 0.448980 | 0.959184 | 0.428571 | 0.485183 |
| L2 key-payload shuffle | 0.653061 | 0.418367 | 0.948980 | 0.408163 | 0.481623 |
| L3 state-query shuffle | 0.663265 | 0.397959 | 0.928571 | 0.397959 | 0.461909 |

L1 improves both primary metrics over L0 and both shuffles, has no execution
loss, and is positive on exactly `4/8` heldout tasks. It is classified STRONG
with selection score `0.0586734694`.

## Instant Addition

After checkpoint selection, 98 heldout-task memories were feed-forward
compiled and added to the 401-memory field without retraining or optimizer
steps, producing the 499-memory deployment field.

- Mean/max compilation: `1.0050/2.3378 ms` per memory.
- Mean/max field add: `0.07019/0.10528 ms` per memory.
- Total compile plus add: `0.110829 s`.
- Field shape before/after: `[960,8,256]` plus `[8,256]`, unchanged.
- Production add scanned no existing record.
- Audit-rebuild max absolute error: `3.8147e-6`.
- Parent remove/restore max absolute error: `9.5367e-7`.
- Deployment field SHA256:
  `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`.

## First37 Development Result

All three conditions used the exact matched AppWorld 0.1.0 harness. D1 and D2
used only eight slots read from the fixed 499-memory field.

| Condition | Success | Steps | Prompt tokens | Generated tokens | Exceptions |
|---|---:|---:|---:|---:|---:|
| D0 bare/zero | 8/37 | 872 | 10,519,521 | 113,617 | 0 |
| D1 correct field | 8/37 | 924 | 11,114,918 | 104,275 | 0 |
| D2 shuffled field | 5/37 | 995 | 12,181,104 | 114,462 | 0 |

Thus `D1-D0=0` and `D1-D2=+3`. D1 retains two D0 successes, gains six, and
loses six. These task-ID changes are single-seed exposed-development
diagnostics, not statistical evidence. Mean field-read latency was `0.243 ms`
for D1 and `0.229 ms` for D2; no memory-count-dependent read occurred.

**VERIFIED:** the correct complete field has a live memory-specific effect over
the key-payload-shuffled full-bank control while matching bare task success.

**INFERENCE:** the full reversible field carries behaviorally relevant
state-memory correspondence, but the current training is not yet an absolute
end-to-end improvement over bare Qwen.

**UNVERIFIED:** robustness across seeds, unexposed tasks, benchmarks, or a
larger evaluation. Every official AppWorld test-normal task is historically
exposed, so this run cannot support a final statistical test claim.

## Runtime And Attempts

The append-only ledger has 34 attempt IDs and 68 start/end rows; all attempts
are closed. Twenty-eight completed and six preserved implementation/audit
failures. The final attempt is `audit-export-008` with normal completion.
Ledger SHA256:
`cc1cd9ec3a88d4856c2675c8d7a1c44d21197f8cafb92e6c77f5a2fc97ad8856`.

Accounted H100-active attempt time is `8.874528 h`; scientific wall span is
`11.797571 h`. The Lambda artifact root is 2,374,788,750 bytes. Focused tests:
`23 passed`.

Implementation-only recoveries fixed a signature-manifest parser, strict CUDA
determinism, query-shuffle legality, live-history serialization, a preflight
selector-SHA typo, and post-run audit export/redaction. Accepted scientific
parameters and completed scientific rows were not rewritten. The Windows
sandbox `apply_patch` helper failed during final audit hardening; deterministic
PowerShell UTF-8 writes were used and regression-tested.

## Detailed Audit

The committed Git-safe audit contains 183 files (223,851,504 bytes), including
2,791 first37 step tensors, 784 heldout condition tensors, reconstructible
message/trajectory records, raw model outputs, executed code, observations,
36 task comparisons, and a 41,620,341-byte lossless tensor bundle.

- Audit index SHA256:
  `6075662dcd3897f3147d26d7067e30f4a05d1ce7b478f9a5fc600af08b0d1109`.
- Indexed hash mismatches: `0`.
- Registered sensitive observations: `166`; leaks: `0`.
- Raw JWT matches: `0`.
- Raw unredacted artifacts remain only on Lambda.

## Next 48 Hours

Freeze this checkpoint and run no new component study. First, review the
omitted decision-tree boundary and lock the paper claim as a single-seed live
memory-specific field effect, not an end-to-end improvement. Then complete
constant-write/read complexity benchmarks, ablation tables, audit-derived
failure analysis, limitations, and manuscript integration. Any broader
correct-vs-shuffled-vs-bare evaluation or portability run requires a separately
reviewed preregistration; do not create a V5 tag automatically.

## Artifacts

- Lambda root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_joint_full_bank_9a_20260826_001`
- Attempts: `attempts.jsonl`
- Source provenance: `data/source_representation_audit.json`
- Selector decomposition: `data/selector_decomposition_audit.json`
- Training summary: `joint_training/training_summary.json`
- Heldout live summary: `heldout_validation/live_full_field/validation_summary.json`
- Checkpoint selection: `heldout_validation/live_full_field/checkpoint_selection.json`
- Instant-add report: `deployment_field/instant_add_report.json`
- First37 summary: `first37/final_summary.json`
- Git-safe audit index: `research/audits/rcmf_joint_full_bank_9a_20260826_001/index.json`
- Machine summary: `research/results/exp031a_rcmf_joint_full_bank/summary.json`
## Final System Status

The persistent NFS mount is present. Ten historical tmux panes are idle bash
shells. No Qwen, AppWorld, training, generation, or EXP-031A worker remains.
The H100 reports `0 MiB`, `0%`, and no compute process. Two old inert log-sink
`cat` processes and standard Jupyter/VS Code services remain. The Lambda
instance is safe to terminate after final Git synchronization.