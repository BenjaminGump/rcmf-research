# EXP-026A Final Scientific Report

## Outcome

- Run UUID: `direct_injection_channel_capacity_7dh_20260822_001`
- Global seed: `25101`
- Starting commit: `93745a1e92c650af372970b7f2c242047d3675ff`
- Final experiment-source commit:
  `f3640575a494189d32dc2b443bcd6e4bf99d6e69`
- Branch: `research/v4-direct-behavior-program`
- Frozen selector SHA256:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42b01255a9e623956611f`
- Clean replay lineage:
  `5f15f47422b561c295a166681eb5d62698d9c708d4559278fcf7b823383a28a1`
- Primary states/tasks: `32 / 9`
- Passing K values: none
- Reached branch: `input_embedding_channel_behavioral_capacity_failed`

No tested additive last-user embedding channel retained the preregistered raw
transition benefit. K=4 came closest, with action-signature/successor retention
of `50.00%/63.64%`, but it missed the required `70%` retention on one primary
metric and `50%` on the other. K=8 and K=16 were weaker. The conditional
widened PairMLP experiment was therefore not triggered.

## Integrity And Attempts

Qwen3-8B, the selector, the clean corpus, the 32 state/F3-transition pairs,
the canonical prompt, three demonstrations, observation exclusion, and the
AppWorld 0.1.0 same-world bridge remained frozen. Each diagnostic intervention
was a free `K x d_model` tensor injected directly into `last_user_k`; no shared
decoder, 128D latent, PairMLP, factorized program, selector update, or full bank
was used.

The append-only ledger contains 11 unique closed attempts and 22 start/end
events. Four attempts stopped cleanly before scientific duplication:

| Attempt | Result |
|---|---|
| `exp026a-preflight-001` | Rejected a selector-hash typo in the new config |
| `exp026a-preflight-002` | Rejected an incomplete bounded parent cache; the missing frozen pair was reconstructed from immutable clean inputs |
| `exp026a-teacher-001` | Stopped before model load on replay-config unwrapping |
| `exp026a-analyze-001` | Stopped after immutable outputs on a bootstrap result-key mismatch |

The successful phases were preflight, policy-teacher completion, free-DeltaE
training, teacher-forced summary, one-step preflight, 184-condition generation
and execution, and CPU-only final analysis. No completed generation, training
update, or condition was repeated.

- Attempt ledger SHA256:
  `e4529c7d81123b528d0cfd1d42298f49d86cef5daf83b5b7a406488c860b7bae`
- Preflight SHA256:
  `150a7a3cc68aca3b8661c4e312741788c4880255c58a1b86efba5612f3213afa`
- Teacher summary SHA256:
  `b629f55d65576e1e8ea74a17b0a566830dc260a8f83b7eb53f157bab7e48cef2`
- Training summary SHA256:
  `4c3e8f075833b5f54841ecacabf46a5261cef5a6daceb951991cbca51b108ded`
- One-step analysis SHA256:
  `2f6dfb471220ed838796b4574c48ff4a2f24c2aee1fadb0dc00fbeb617bdcdba`

The final focused Lambda test run passed `9/9`; an earlier combined relevant
suite passed `12/12`. Python compilation and source synchronization checks
passed. Ruff was unavailable in the Lambda environment and is recorded as an
environment limitation, not silently skipped.

## Feasibility And Runtime Preflight

| K | Feasible states | Parameters per pair | Total free parameters |
|---:|---:|---:|---:|
| 4 | `32/32` | `16,384` | `524,288` |
| 8 | `32/32` | `32,768` | `1,048,576` |
| 16 | `28/32` | `65,536` | `1,835,008` |

The four preregistered K16-missing rows had too few eligible tokens and were
kept as explicit missing K16 measurements:

- `appworld:trace:229360a_1:step:26:line:338`
- `appworld:trace:2a163ab_1:step:13:line:33`
- `appworld:trace:7d7fbf6_2:step:9:line:402`
- `appworld:trace:82e2fac_3:step:10:line:20`

K16 retained the required minimum `28/32` feasible states. Policy-teacher rows
were `17` reused and `15` newly generated. Maximum planned backwards were
`1,472`; actual optimization stopped at `736` u8-equivalent pair updates plus
the K4-only u8-to-u16 continuation. Expected H100 time was `4.2954` hours,
below the six-hour launch threshold.

## Teacher-Forced Capacity

| K/checkpoint | Policy KL | Zero KL | KL reduction | Teacher CE | Top-1 | GT NLL | Max ratio |
|---|---:|---:|---:|---:|---:|---:|---:|
| K4 u4 | `0.225945` | `0.337537` | `+0.111593` | `0.233960` | `0.963130` | `0.309951` | `1.000000119` |
| K4 u8 | `0.161562` | `0.337537` | `+0.175976` | `0.169536` | `0.969658` | `0.260150` | `1.000000119` |
| K4 u16 | `0.297942` | `0.337537` | `+0.039595` | `0.305494` | `0.961699` | `0.439669` | `1.000000119` |
| K8 u8 | `0.355502` | `0.337537` | `-0.017964` | `0.362772` | `0.959252` | `0.433983` | `1.000000119` |
| K16 u8 | `0.540739` | `0.384371` | `-0.156368` | `0.548751` | `0.931687` | `0.687052` | `1.000000119` |

K4 continued to u16 because u4-to-u8 policy KL/CE improved by
`28.49%/27.54%`; u16 then degraded, and its preregistered frozen checkpoint was
still evaluated. K8 improved only `2.07%/2.69%` from u4 to u8 and K16 worsened,
so neither continued. Every checkpoint respected the ratio budget within the
locked floating-point tolerance.

Final checkpoint SHA256 values:

- K4: `d6dcfad61745dfe48e7798b0a2e46d05200cc06623256a949fbb52a4184f0668`
- K8: `d01978882d61eb721177247f522b3e5b9189789d1133d09fe077785b94fb82a4`
- K16: `bac8bf649391d9e75047368265a84950cea0ea7110cd83dda9377467cb00b087`

## One-Step Capacity Audit

All `184/184` conditions generated and executed once: O4/S4 and O8/S8 for 32
states each, plus O16/S16 for 28 feasible states each. Same-world and namespace
checks were `184/184`; infrastructure and execution exceptions were zero.

| K | Condition | Exact API | Action signature | Execution | Semantic successor | Normalized observation |
|---:|---|---:|---:|---:|---:|---:|
| 4 | C0 bare | `0.78125` | `0.31250` | `0.93750` | `0.43750` | `0.441709` |
| 4 | F3 raw | `0.87500` | `0.68750` | `1.00000` | `0.78125` | `0.770394` |
| 4 | O4 direct | `0.78125` | `0.50000` | `0.90625` | `0.65625` | `0.655017` |
| 4 | S4 shuffled | `0.71875` | `0.40625` | `0.90625` | `0.46875` | `0.484268` |
| 8 | O8 direct | `0.75000` | `0.40625` | `0.84375` | `0.62500` | `0.628538` |
| 8 | S8 shuffled | `0.78125` | `0.31250` | `0.93750` | `0.46875` | `0.473536` |
| 16 | C0 bare | `0.75000` | `0.25000` | `0.92857` | `0.39286` | `0.395002` |
| 16 | F3 raw | `0.85714` | `0.64286` | `1.00000` | `0.75000` | `0.745728` |
| 16 | O16 direct | `0.64286` | `0.25000` | `0.82143` | `0.42857` | `0.452806` |
| 16 | S16 shuffled | `0.67857` | `0.21429` | `0.89286` | `0.39286` | `0.415699` |

Primary task-grouped bootstrap contrasts use seed `25101` and 2,000 samples:

| Contrast | Action-signature delta (95% CI) | Successor delta (95% CI) |
|---|---:|---:|
| O4 - C0 | `+0.18750 [0.06452, 0.31250]` | `+0.21875 [0.09375, 0.34286]` |
| O4 - F3 | `-0.18750 [-0.26667, -0.09677]` | `-0.12500 [-0.27273, 0.00000]` |
| O4 - S4 | `+0.09375 [-0.03333, 0.25806]` | `+0.18750 [0.03333, 0.34375]` |
| O8 - C0 | `+0.09375 [0.00000, 0.18182]` | `+0.18750 [0.09935, 0.28571]` |
| O8 - S8 | `+0.09375 [0.00000, 0.22222]` | `+0.15625 [0.03571, 0.27027]` |
| O16 - C0 | `0.00000 [-0.20000, 0.20000]` | `+0.03571 [-0.15385, 0.21429]` |
| O16 - S16 | `+0.03571` | `+0.03571` |

| K | Signature retention | Successor retention | Positive tasks | Gate |
|---:|---:|---:|---:|---|
| 4 | `0.5000` | `0.6364` | `6/9` | Failed 70%/50% retention conjunction |
| 8 | `0.2500` | `0.5455` | `5/9` | Failed retention, execution, and task count |
| 16 | `0.0000` | `0.1000` | `2/9` | Failed all primary checks |

K4 demonstrates a real direct-channel effect over bare and shuffled controls,
but the preregistered question was whether the channel could carry most of the
raw episodic effect. It cannot: neither primary retention metric reaches 70%,
and action-signature retention is exactly 50%, not the required companion
retention after a 70% pass. Increasing K does not repair the result.

## Runtime And Artifacts

- End-to-end wall span: `1.84862` hours
- Successful GPU-process elapsed time: approximately `1.5297` H100 hours
- Qwen generation time inside the formal one-step phase: `0.12160` H100 hours
- Teacher/training/formal condition counts: `32 / 92 / 184`
- Formal generations/executions: `184 / 184`
- Artifact size: `135,221,819` bytes
- Artifact root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/direct_injection_channel_capacity_7dh_20260822_001`
- Machine report: `direct_channel_report.md`
- Preflight: `preflight.json`, `runtime_preflight.md`
- Teacher: `teacher_cache/summary.json`
- Training: `training/summary.json`, `training/K*/checkpoint_u*.pt`
- Teacher forced: `teacher_forced/summary.json`
- Behavior: `one_step/condition_manifest.json`,
  `one_step/generation_summary.json`, `one_step/analysis.json`

At final inspection there was no active EXP-026A process. The H100 reported
`0 MiB` allocated and `0%` utilization. Only older idle tmux sessions remained.
The run and persistent artifacts are safe to terminate.

## Scientific Interpretation

VERIFIED:

- Free per-pair direct embedding interventions produce some one-step behavioral
  improvement at K4 and K8, but no K passes the locked channel-capacity gate.
- K4 retains only `50.00%/63.64%` of raw action-signature/successor gain.
- K8 and K16 do not improve the result; K16 is feasible for exactly `28/32`.
- No decoder, latent bottleneck, PairMLP, factorized program, selector update,
  full bank, Stage C2, end-to-end RCMF, or V4 tag was created.

INFERENCE:

- The dominant submission-path bottleneck is the additive last-user embedding
  intervention itself, not merely the 128D decoder or amortized program family.
- Further r16/r64/PairMLP objective work is not justified before the deadline.

UNVERIFIED:

- Other intervention sites or mechanisms, full-trajectory behavior, and a
  deployable compiled transition program.

Decision: record `input_embedding_channel_behavioral_capacity_failed`. Freeze
the compiled-program route for the submission. Center the paper on the clean
identity-reconciled corpus, signature-balanced selector, and causally validated
raw-transition intervention, while reporting the additive compiled-channel
result as a bounded negative. Any selector-plus-raw-transition partial
end-to-end audit requires a separately reviewed milestone.
