# EXP-016C Pair-Grouped Decoder Split

Status: **validated**

Manifest seed: `20260810`

Manifest SHA256:
`3cde923e362c06d9c355f5acd0071b391c1047e0667c916837e8bb0e7421811b`

## Population

- ordered pairs: 192;
- unique states: 57;
- effective memories: 36;
- folds: 3;
- every pair is held out exactly once;
- every state appears in exactly one held-out fold;
- no state is shared between decoder-train and decoder-held-out rows within a
  fold;
- every training fold covers all 36 memories.

| Fold | Train pairs | Held-out pairs | Held-out states | Train memories | Positive | Neutral | Negative | Random |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 128 | 64 | 17 | 36 | 22 | 19 | 9 | 14 |
| 1 | 128 | 64 | 20 | 36 | 13 | 9 | 28 | 14 |
| 2 | 128 | 64 | 20 | 36 | 13 | 20 | 11 | 20 |

The category distribution is approximate because state grouping takes
priority over pair-level balancing. No pair or state was moved after observing
Qwen metrics.

The full immutable manifest is committed as
`research/results/stage_c_oracle_decoder_5fc_split_manifest_20260810_003.json`
and remains in the Lambda artifact root as `decoder_split_manifest.json`.
