# EXP-025D Runtime and Storage Preflight

Date: 2026-08-18  
Branch: `research/v4-state-conditioned-transition-program`  
Run UUID: `state_conditioned_transition_program_7d_20260818_001`  
Status: `completed_runtime_review_required`  
Source commit: `cd5341412842137be0a250ff2f744c4f309e2b30`  
Final record commit: commit containing this report

## Update schedule

The clean decoder uses `192` calibration and `64` grouped-heldout pairs. The
minimum schedule gives every pair exactly `64` updates: `12,288 + 4,096 =
16,384` pair updates. If both train-only continuation gates require `128`, the
maximum is `32,768`.

Program training uses `607` scoreable A pairs, seven fixed architectures, a
`24 x 64 = 1,536` update tiny overfit, and a `96 x 16 = 1,536` update smoke.

- Minimum: `275,008` program pair updates (one seed, 64 updates per A pair).
- Expected: `352,704` (minimum plus two final primary-model seeds at 64).
- Conservative: `702,336` (all required continuations to 128).

The schedule, pair set, architectures, seeds, controls, and held-out cells were
not reduced to fit a runtime target.

## Runtime estimate

The measured planning anchor is `5,683.34 s / 3,072 = 1.85005 s` per Qwen
pair update from EXP-016A. Expected work contains `970` new sparse-teacher rows,
`614` bare-state rows, `16,384` decoder updates, `352,704` program updates, and
`21,859` evaluation forwards.

| Scenario | H100 scoring/optimization/evaluation | Final report/validation | Wall total |
| --- | ---: | ---: | ---: |
| Best | 144.13 h | 1.00 h | 145.13 h |
| Expected | 201.72 h | 2.00 h | 203.72 h |
| Conservative | 518.42 h | 4.00 h | 522.42 h |

Expected H100 time decomposes into teacher cache `0.814 h`, optimization
`189.67 h`, and evaluation `11.23 h`.

The projected artifact size is `21,462,500,000` bytes (`21.46 GB`, about
`20.0 GiB`). The completed CPU preflight artifact currently occupies
`3,514,019` bytes.

## Resume contract

- `attempts.jsonl` is append-only with parent-attempt and checkpoint identity.
- `heartbeat.json` updates at least every `240 s` during long work.
- Teacher rows and bare-state rows are atomic and skipped only after exact-key
  validation.
- Decoder checkpoints at u16/u32/u64/(u128) include decoder, pair latents, Adam
  state, RNG, hashes, and exact per-pair counters.
- Program checkpoints are atomic per architecture/seed at u16/u32/u64/(u128)
  and include model, Adam, RNG, frozen hashes, and exact per-pair counters.

## Review decision

The expected `201.72` H100 hours exceeds the `12` H100-hour preflight review
threshold. In accordance with the preregistered contract, no Qwen scoring,
training, or GPU work has started. Explicit user approval is required before
the full unchanged EXP-025D run may proceed.

