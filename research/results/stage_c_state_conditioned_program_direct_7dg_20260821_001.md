# EXP-025D-Direct Final Scientific Record

Date: 2026-08-21  
Branch: `research/v4-direct-behavior-program`  
Run UUID: `state_conditioned_program_direct_7dg_20260821_001`  
Global seed: `25101`  
Starting/archive SHA: `343c0a3b91df9b810edeac65736dc954ea7960b1`  
Execution source SHA: `673b254d1429bdd427d1b44597741d8130121c00`  
Final record SHA: commit containing this report  
Decision: `direct_behavior_factorized_program_failed`

## Frozen contract

The EXP-025C-R selector, Qwen3-8B parameters, K=4 `last_user_k` injection,
canonical prompt, three demonstrations, clean corpus, observation-excluded
six-view transition boundary, and incremental field implementation remained
frozen. Student prompts contained no raw transition. PairMLP and factorized
models used independent private no-bias decoders initialized from clean decoder
SHA256 `71f5974e59d563453398bb16ba35e54f29b8c46405d0d0594294c4b8299a6a26`.

Exactly one global seed was used. cuDNN warned that its attention backward
kernel may not be bitwise deterministic; no extra seed or repeat was launched.

## Data and runtime

- Scoreable A/B/C/D/E pairs: `607/135/112/112/135`.
- A task-grouped split: `479` train pairs from `29` tasks and `128`
  validation pairs from `8` disjoint tasks; task/state overlap is zero.
- Unique teacher rows: `970`; reused/new top-64 rows: `132/838`.
- Over-context rows were excluded as missing measurements; none was truncated
  or assigned a target.
- PairMLP/factorized maximum backward counts: `7,664/7,664`.
- Measured GPU-attempt allocation: `8.7561` H100 hours.
- First GPU-attempt to scientific stop: `8.8217` wall hours.
- Successful direct attempt internal time: `8.2741` H100 hours.
- Artifact size before finalization: `1,894,481,569` bytes.

## Old latent-target audit

The saved EXP-025D-Fast PairMLP did not fit the latent target well and its
grouped validation prediction collapsed close to zero. Train/validation cosine
was `0.370794/0.214108`; MSE reduction versus zero was
`0.140510/0.000180`; Huber was `0.162092/0.167881`. The factorized latent
model reached validation cosine `0.169363` and MSE reduction `-0.079540`.

The 128 pair targets had mean norm `3.018957`, effective rank `58.30046`, and
mean nearest-neighbor cosine `0.409839`. No implementation or grouped-split bug
was verified. This audit therefore proceeded to direct frozen-Qwen behavioral
training.

## Direct PairMLP

u8 was selected. u16 reduced validation Huber only `4.5553%`, below the fixed
`5%` threshold, and reduced Spearman by `0.026523`. The selected checkpoint
SHA256 is `80506a5d9b1c3031b5468fb59c0b6d9e01d7d50ddc1fee49115a88eb8b8b429d`.

| Cell | Correct rho | Correct Huber | Zero Huber | Huber reduction | State-shuffle Huber | Transition-shuffle Huber |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A validation | 0.570923 | 0.170581 | 0.256587 | 0.335191 | 0.219790 | 0.173371 |
| B | 0.386353 | 0.190856 | 0.249890 | 0.236242 | 0.229051 | 0.200271 |
| C | 0.478483 | 0.167233 | 0.234543 | 0.286984 | 0.192942 | 0.165957 |
| D | 0.406490 | 0.175110 | 0.240019 | 0.270431 | 0.216182 | 0.176645 |
| E | 0.395259 | 0.188088 | 0.243227 | 0.226698 | 0.224826 | 0.194334 |

The PairMLP gate passed every required A/B/E check. This directly falsifies
the claim that the current frozen representations contain no learnable
pair-specific behavioral information. It instead shows that independently
optimized canonical latent coordinates were not a suitable amortization
target for this model family.

## Direct factorized program

u16 was selected over u8: validation Huber improved `54.9280%` and Spearman
increased by `0.085029`. The selected checkpoint SHA256 is
`9433518d828930dfc31e63d18f5477ba563b8870cb4a91ec3665f6890c5e90ff`.

| Cell | Correct rho | Correct Huber | Zero reduction | Static Huber | Transition-shuffle Huber | Swap Huber |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A validation | 0.407772 | 0.248198 | 0.032694 | 0.322597 | 0.252674 | 0.255886 |
| B | 0.294679 | 0.291551 | -0.166718 | 0.367587 | 0.295795 | 0.304534 |
| C | 0.441162 | 0.259815 | -0.107750 | 0.345142 | 0.266168 | 0.262880 |
| D | 0.418466 | 0.306559 | -0.277232 | 0.379038 | 0.301885 | 0.313666 |
| E | 0.298059 | 0.298123 | -0.225699 | 0.364144 | 0.303467 | 0.297801 |

A passed its correlation, Huber, static, shuffle, and swap checks. B and E
had adequate Spearman and positive transition-specific ordering gaps, but both
had negative Huber reduction versus zero. E also failed the strict memory-swap
contrast by a small margin. C/D correlations were positive but their Huber
reductions were negative. The maximum applied ratio was `1.000000119`, within
the fixed numerical tolerance. Observation-shuffle invariance and the frozen
selector hash both passed.

## Decision

Reached `direct_behavior_factorized_program_failed`. Direct behavioral
training repairs the PairMLP upper bound, but the current r16 field-compatible
factorization does not transfer calibrated utility magnitude to held-out B/E.
The bottleneck is therefore the field-compatible factorization/calibration,
not latent-target distillation and not an absence of pair information.

The factorized teacher-forced gate did not pass, so H1-H4 Qwen generation and
AppWorld execution were not run. There are no one-step compiled-program
metrics. The compiled transition program is not validated.

Do not start a full bank, p(s,m_transition), compiler/injector training,
Stage C2, end-to-end RCMF, full AppWorld evaluation, or V4 tagging from this
result. The next action is an immediate project-scope review of whether one
narrow field-compatible factorization repair is worth the deadline risk, or
whether the paper should retain the validated selector plus raw-transition
causal result and report the compiled-program result as a bounded negative.

Artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_program_direct_7dg_20260821_001`
