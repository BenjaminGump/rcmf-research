# EXP-025D Pair-Manifest Preflight

Date: 2026-08-18  
Branch: `research/v4-state-conditioned-transition-program`  
Run UUID: `state_conditioned_transition_program_7d_20260818_001`  
Source commit: `cd5341412842137be0a250ff2f744c4f309e2b30`

## Frozen inputs

- Structural lineage: `f3389f8ddcc2de5f7b7807a6a8ef37ca38d3df3cde4155f01220240e65140dbb`
- Replay lineage: `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Selector ensemble: `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`
- Train/validation states: `499/139`; train transitions: `499`; parents: `29/8`.
- The selector, ordered score tensors, class rankings, and selected class IDs are frozen.

## Exact pair accounting

Over-context pairs remain explicit missing measurements. No truncation, same-class
substitution, cross-class fallback, changed selector choice, or zero imputation is
allowed.

| Cell | Logical | Scoreable | Over context | States | Tasks | Parents |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A train-state/train-parent | 640 | 607 | 33 | 475 | 37 | 29 |
| B validation-state/train-parent | 139 | 135 | 4 | 135 | 9 | 23 |
| C train-state/heldout-parent | 112 | 112 | 0 | 112 | 37 | 8 |
| D validation-state/heldout-parent | 112 | 112 | 0 | 112 | 9 | 7 |
| E validation-state/deployment selection | 139 | 135 | 4 | 135 | 9 | 25 |

The five manifests contain `1,142` logical pair occurrences, `1,101` scoreable
occurrences, and `41` over-context occurrences. Deduplication by pair ID yields
`1,007` unique logical pairs, of which `970` are scoreable and `37` are explicit
over-context missing pairs.

The scoreable A manifest covers all `37` train tasks and all `29` train parents.
Its roles are P1 frozen strict-B top `473`, P3 high-tier oracle `1`, P4 same-intent
hard negative `69`, P5 unrelated Tier-0 `63`, and P6 alternate same-signature
parent `1`. It includes `460` exact-API pairs and Tier 0/1/2/3/4 counts
`92/39/111/83/282`.

Scoreable held-out Tier-3/4 counts are B `84`, C `60`, D `51`, and E `83`.
Their exact-API counts are B `105`, C `88`, D `76`, and E `104`.

## Frozen-selection validation

The ID-keyed EXP-025B `teacher_section_tokens` manifest is used only to choose
the immutable context-compatible exemplar inside the already selected class.
The resulting transition IDs exactly reproduce EXP-025C diagnostics:

- B: `139/139`, zero mismatches;
- D: `112/112`, zero mismatches;
- E: `139/139`, zero mismatches.

No B/C/D/E supervision or behavior enters selection, weighting, decoder choice,
or epoch selection.

## Decoder split

The deterministic A-only grouped split contains `192` calibration pairs and
`64` held-out inversion pairs, each from a distinct state. State overlap is
zero. The split SHA256 is
`e5876e6d6325611668c5dd5010ad208aece1c26adaf5edb969f00e7ddc5a3872`.

Lambda artifact root:
`/lambda/nfs/rcmf-persist/project/runs/stage_c/state_conditioned_transition_program_7d_20260818_001`

