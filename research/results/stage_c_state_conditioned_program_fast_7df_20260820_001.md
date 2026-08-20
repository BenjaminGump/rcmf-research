# EXP-025D-Fast Final Scientific Record

Date: 2026-08-20  
Branch: `research/v4-fast-program-pilot`  
Run UUID: `state_conditioned_program_fast_7df_20260819_001`  
Starting/archive SHA: `a1e8bad323b05c741bfb914d0563f9e4a89726b6`  
Source SHA: `21784e4f1e37a59e3d0895ea78fbc43351b7b4a4`  
Final record SHA: commit containing this report  
Decision: `state_transition_representations_insufficient`

## Scope and manifests

The bounded manifest contains logical A/B/C/D/E counts `128/24/24/24/32`,
`232` logical occurrences, and `224` unique pairs. Every pair is scoreable;
over-context, truncation, and class-substitution counts are zero. A covers all
37 train tasks, 29 train parents, 108 states, and 76 transitions.

The EXP-025C-R selector ensemble remained frozen at SHA256
`c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`.
Qwen3-8B, K=4, `last_user_k`, the three demonstrations, and the canonical
prompt were unchanged.

## Method contracts

- The primary program consumes exactly six vectors: token mean and final token
  for source goal, pre-action state, and complete action.
- Post-action observation and full-transition-global views are excluded.
- Observation shuffling leaves the primary static program, conditional basis,
  and pair latent unchanged. The action-plus-outcome diagnostic remains
  observation-sensitive by construction.
- Student prompts contain no raw transition text.
- Incremental `add_fast`, `remove_fast`, `replace_fast`, and
  `remove_parent_fast` match explicit sums and `audit_rebuild()` while keeping
  fixed read shapes. Parent-normalized weights are the default.
- The toy adapter smoke confirms field operations do not import AppWorld.

## Runtime and prefix path

The preflight projected `4.763/5.676/14.790` best/expected/conservative H100
hours and `1.33 GB`. Automatic launch was allowed because the expected case
was below the 12-hour review threshold.

Exact prefix-KV equivalence failed on the fixed short/median/long/near-limit
pairs. Representative token counts were `7,345/10,089/24,774/27,819`; maximum
logit differences were `0.8125/8.453125/1.199219/0.75`. The timebox was
respected and the run used the full-forward path.

Across four GPU attempts, measured allocation time was `28,777.03 s`
(`7.9936` H100 hours). First-GPU-start to scientific stop was `29,807.58 s`
(`8.2799` wall hours). Final artifact size before finalization was
`5,650,447,990` bytes.

## Decoder and pair targets

The direct row-repair plus SVD path failed its heldout gate (Spearman
`0.4941`, Huber reduction `2.26%`). The preregistered fallback trained a clean
no-bias linear decoder on 64 calibration pairs with 16 grouped-heldout pairs.
Its SHA256 is
`71f5974e59d563453398bb16ba35e54f29b8c46405d0d0594294c4b8299a6a26`.

Heldout decoder metrics at u8/u16/u32:

| Update | Spearman | Sign | Huber | Reduction vs zero |
| ---: | ---: | ---: | ---: | ---: |
| 8 | 0.669610 | 1.0000 | 0.000570 | 99.665% |
| 16 | 0.876471 | 1.0000 | 0.000436 | 99.744% |
| 32 | 0.938235 | 1.0000 | 0.000788 | 99.537% |

The decoder gate passed. Canonical pair targets continued to u64 because u32
was materially improving:

| Update | Utility Spearman | Sequence Huber |
| ---: | ---: | ---: |
| 8 | 0.941404 | 0.139228 |
| 16 | 0.964580 | 0.107582 |
| 32 | 0.979619 | 0.073989 |
| 64 | 0.989921 | 0.035762 |

All 224 primary rows have exactly 64 updates. The 16-row second-seed subset
has exactly 16 updates. Repeat stability passed: latent-z cosine mean/minimum
`0.965701/0.834383`, decoded-effect cosine mean/minimum
`0.965719/0.834468`, utility Spearman `0.997059`, and sign agreement `1.0`.
Latent coordinates were stable, so the selected program target was
`latent_z`. Strict final ratio projection reports maximum `1.0` for primary
and repeat targets.

## Tensor-space program results

The A-only grouped split contains 75 training and 53 validation pairs with
zero task and parent overlap.

| Model | Cosine | MSE | MSE reduction vs zero | Huber |
| --- | ---: | ---: | ---: | ---: |
| zero | 0.000000 | 0.073477 | 0.000000 | 0.165673 |
| free-ID | 0.029174 | 0.073086 | 0.005316 | 0.165130 |
| PairMLP observation-excluded | 0.214108 | 0.073463 | 0.000180 | 0.167881 |
| static-only observation-excluded | 0.174397 | 0.075337 | -0.025320 | 0.170185 |
| shuffled transition | 0.140584 | 0.080964 | -0.101896 | 0.180103 |
| factorized r16 observation-excluded | 0.169363 | 0.079321 | -0.079540 | 0.175456 |
| factorized r16 action-plus-outcome | 0.158854 | 0.082057 | -0.116772 | 0.180261 |

PairMLP failed both latent gates: cosine is below `0.25`, and MSE reduction is
far below `10%`. The primary factorized model also failed and did not beat
static-only. Adding outcome views did not rescue the result.

## Scientific decision

The stable behavioral pair targets show that the failure is not pair-target
nonidentifiability. The current independent frozen state/transition multiview
representations do not support amortizing those pair effects, even with the
PairMLP upper bound on this bounded dataset.

Reached branch: `state_transition_representations_insufficient`.

Per the preregistered stop, B/C/D/E teacher-forced Qwen metrics, H1-H4
generation, and AppWorld execution were not run. The compiled transition
program is not validated. Program/full-bank integration, injector training,
Stage C2, end-to-end RCMF, full AppWorld evaluation, and V4 tagging remain
blocked.

## Next review

A separately reviewed 3-5 day representation-repair milestone should first
test a bounded pair-aware frozen-Qwen information upper bound against these
same canonical targets. Only if that upper bound passes should it distill a
field-compatible, observation-excluded representation and rerun B/C/D/E plus
the conditional one-step audit. Do not start full-bank integration from this
branch.

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_fast_7df_20260819_001`

