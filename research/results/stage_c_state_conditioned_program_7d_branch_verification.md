# EXP-025D Branch and Input Provenance

Status: source preflight implementation; no Qwen scoring or training has started.

## Branch Freeze

- Starting branch: `research/v4-identity-reconciled-corpus`
- Starting commit: `f05a40dfc095fa3ec655441642b0548ef382cf10`
- Archive branch: `archive/v4-selector-behaviorally-validated`
- Program branch: `research/v4-state-conditioned-transition-program`
- Local, GitHub, and Lambda branch comparisons were identical to the starting
  commit before EXP-025D source work.
- The only permitted pre-existing working-tree entry was untracked
  `.codex_tmp/`.
- No RCMF V4 tag was created or moved.

## Immutable Inputs

- Structural lineage:
  `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`
- Replay-validated lineage:
  `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Frozen selector ensemble:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Parent artifacts: EXP-025A, EXP-025B, EXP-025C, and EXP-025C-R remain
  immutable.

The Lambda CPU preflight will re-hash these inputs and compare every selected
B/D/E class and exemplar with the frozen EXP-025C diagnostics before any GPU
work is eligible.
