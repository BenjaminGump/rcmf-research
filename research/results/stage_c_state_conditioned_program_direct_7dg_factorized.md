# EXP-025D-Direct Factorized Behavioral Program

The single r16 observation-excluded factorized model used an independent
private decoder initialized from the same clean decoder as PairMLP. It was not
initialized from the trained PairMLP. Trainable components were the state
controller, static head, conditional basis head, and private no-bias decoder;
Qwen, selector, and representations stayed frozen.

u8 A-validation Spearman/Huber was `0.322743/0.550671`. u16 improved to
`0.407772/0.248198`, a `54.9280%` Huber improvement with `+0.085029`
Spearman. All update-continuation checks passed and u16 was selected.

Selected correct Spearman on A/B/C/D/E was
`0.407772/0.294679/0.441162/0.418466/0.298059`. Huber reduction versus zero
was `+3.27%/-16.67%/-10.77%/-27.72%/-22.57%`. A beat static-only,
transition-shuffle, and memory-swap. B beat transition-shuffle and memory-swap
but failed positive Huber reduction. E beat transition-shuffle, failed
positive Huber reduction, and did not beat memory-swap. C/D were diagnostic
and showed positive rank correlation but negative Huber reduction.

The maximum applied ratio was `1.000000119` within tolerance. Observation
shuffle left the static program, conditional basis, and pair latent unchanged.
The selector hash remained exactly
`c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`.

Selected checkpoint SHA256:
`9433518d828930dfc31e63d18f5477ba563b8870cb4a91ec3665f6890c5e90ff`.
Trained private decoder SHA256:
`3cc630a13c6cc50c5a9a4a68d97f73816d320786e5d632c81f38010fff0db9bb`.

Decision: `direct_behavior_factorized_program_failed`. The H1-H4 one-step
phase was not unlocked, so no compiled-program generation or AppWorld action
execution exists for this run.
