# EXP-025B Incremental Clean-Cache Report

The replay-valid structural lineage is
`f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`.
The replay-valid contract lineage is
`5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`.

Final recomputation counts are `35/2/17` state/memory/source-transition
representations and `2,781/162/324/696` raw-teacher, Stage-C1, Pair-5D, and
transition-teacher rows. The final Qwen-scoring total is `3,963`; `3,658` was
the direct invalidation preflight, before the downstream condition cascade.

All cache validation passed: unchanged rows are byte/tensor identical, new
rows carry reconciled lineage, duplicate/truncation/leakage errors are zero,
and superseded transition IDs are absent. No checkpoint was retrained or
declared clean by inference.
