# EXP-028A AppWorld-Structured Gate and Compiler Rescue

## Identity

- Run UUID: `appworld_structured_gate_compiler_7hr_20260823_001`
- Global seed: `25101`
- Starting commit: `78f8e2e674709775b8a2a9b92297d255ec0e73c3`
- Final source commit: `52357e234ebf516bb507cb9cffbb2f6cedcb5f3e`
- Archive branch: `archive/v4-generic-memory-specific-amortization-failed`
- Working branch: `research/v4-appworld-structured-rescue`
- Lambda root: `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_structured_gate_compiler_7hr_20260823_001`
- Attempt ledger: `research/results/exp028a_appworld_structured_gate_compiler/attempts.jsonl`
- Attempts: `22` total, `13` successful, `9` failed, all closed

## Scope And Frozen Inputs

Qwen3-8B, the EXP-025C-R selector, clean replay lineage, canonical prompt,
three demonstrations, carrier layers `[7,14,21,28]`, four last-user token
positions, AppWorld 0.1.0 bridge, and seed `25101` remained frozen. No generic
PairMLP, rank sweep, Qwen training, selector retraining, full bank, Stage C2,
or V4 tag was created.

## Runtime Preflight

- Initial panel: `256` states, `246` scoreable
- Maximum scoreable panel: `472` of `499` train states
- Initial/maximum paired T0+T1 conditions: `492/944`
- Structured compiler backward units: `1,276`
- Heldout-train one-step validation conditions: `440`
- Locked one-step conditions: `180`
- Expected/conservative H100 time: `6.1283/11.9489` hours
- Actual attempt-duration accounting: `10.0378` hours, approximately `10.02`
  GPU-attributed process hours
- End-to-end wall span: `13.8011` hours
- Git-safe redacted result bundle: `233,107` bytes

The expected work was below the authorized 18-H100-hour threshold.

## Paired Train-Side Causal Outcomes

The deterministic panel exhausted every scoreable clean train state without
using first37 outcomes. The final accounting is:

- Labeled states: `464`
- Paired T0/T1 conditions: `928`
- Generated conditions: `770`
- Reused conditions: `158`
- Over-context states: `27`, retained as explicit missing rows
- Strict semantic-v3 replay-missing states: `8`, retained as explicit missing rows
- Full accounting: `464 + 27 + 8 = 499`
- POSITIVE/NEUTRAL/HARMFUL: `129/300/35`
- Train-task rows: `366` (`105/235/26`)
- Heldout-train-task rows: `98` (`24/65/9`)

The requested 40 HARMFUL examples were not available after deterministic
exhaustion. This was an expansion trigger, not a license to invent, impute, or
resample outcomes. Every observed class remained nonempty.

## Structured Features And Gate

The shared deployment feature vector contains `186` features. The
machine-readable leakage audit found `0` violations, and all features were
available before generation. Ground-truth target action/observation,
procedural tier against the target, task/state/transition IDs, first37
outcomes, and free ID embeddings were excluded.

The selected gate used epoch `200`, temperature `2.0`, and threshold `0.60`.
On the eight heldout clean-train tasks:

| Metric | Bare | Gated |
|---|---:|---:|
| Semantic successor | 0.408163 | 0.459184 |
| Action signature | 0.397959 | 0.469388 |
| Execution | 0.928571 | 0.938776 |

- Activation: `11/98 = 0.112245`
- Harmful activations: `0/11`
- Active positive prevalence: `0.636364`
- Total validation positive prevalence: `0.244898`
- Positive-prevalence lift: `+0.391466`
- Gate checkpoint SHA256:
  `7c49ace41b81763df4457c976d8800ccb1af11559b016fb98e04cf851098416d`

The preregistered train-side causal gate passed. First37 outcomes did not
alter its model, calibration, or threshold.

## Gated Raw First37 Diagnostic

- Matched bare: `8/37`
- Always-on raw memory: `5/37`
- Gated raw memory: `8/37`
- Gate activation: `0/872` turns
- Retained/gained/lost versus matched bare: `8/0/0`

This is a single-seed diagnostic. With zero activations, it provides no
task-level causal evidence for memory use.

## Structured Compiler

The frozen generic PairMLP checkpoint was
`8af2d1068ee0059c79edf740920f9d506b8a880459ff53656b449f767e5dffda`.
The structured correction network, scalar beta, and shared decoder were the
only trainable components. Training used `366` states, including `105`
POSITIVE states and `576` training units per round.

| Checkpoint | Loss | Policy KL | Teacher CE | GT CE | Max ratio | Beta |
|---|---:|---:|---:|---:|---:|---:|
| u2 | 0.119261 | 0.066897 | 0.091371 | 0.590309 | 0.238870 | 0.040640 |
| u4 | 0.080557 | 0.038422 | 0.062193 | 0.531298 | 0.399310 | 0.056360 |

Heldout-train one-step validation selected u4 with score `0.0535714`:

| Condition | Signature | Successor | Execution |
|---|---:|---:|---:|
| V0 zero | 0.397959 | 0.408163 | 0.928571 |
| V1 correct | 0.448980 | 0.459184 | 0.938776 |
| V2 transition shuffle | 0.428571 | 0.428571 | 0.928571 |
| V3 state shuffle | 0.418367 | 0.408163 | 0.938776 |

The selected u4 checkpoint SHA256 is
`95bc2869df1084eb1166cadeb0edfad584814d5fe0c049b3d7beab59b2c4cab3`.
Its raw-policy KL is `0.108665`; maximum live layer ratio is `0.990334`.

## Locked One-Step Audit

All `180/180` S1/S2/S3/S0 conditions generated and executed in fresh
same-world AppWorld workers. Twelve model-generated actions raised execution
exceptions; no replay, harness, or infrastructure condition failed.

Primary 32-state metrics:

| Condition | Exact API | Signature | Execution | Successor | Obs. similarity |
|---|---:|---:|---:|---:|---:|
| C0 bare | 0.781250 | 0.312500 | 0.937500 | 0.437500 | 0.441709 |
| F3 raw | 0.875000 | 0.687500 | 1.000000 | 0.781250 | 0.770394 |
| S0 zero | 0.781250 | 0.312500 | 0.937500 | 0.437500 | 0.441709 |
| S1 correct | 0.875000 | 0.406250 | 0.937500 | 0.531250 | 0.539028 |
| S2 transition shuffle | 0.781250 | 0.375000 | 0.937500 | 0.500000 | 0.505298 |
| S3 state shuffle | 0.781250 | 0.375000 | 0.937500 | 0.500000 | 0.502785 |

S1 improves C0 by `+0.09375` on signature and successor, and beats each
shuffle by `+0.03125` on both. Only `2/9` tasks are positive, so the locked
classification is `PARTIAL_POSITIVE`, not STRONG POSITIVE.

Task-grouped 95% bootstrap diagnostics for S1-C0:

- Exact API: `+0.09375`, CI `[0.028571, 0.187500]`
- Signature: `+0.09375`, CI `[0.000000, 0.235294]`
- Successor: `+0.09375`, CI `[0.000000, 0.235294]`
- Observation similarity: `+0.097319`, CI `[0.019973, 0.221467]`
- Execution: `0.000000`, CI `[0.000000, 0.000000]`

The S1-shuffle intervals include zero. Raw-gain retention is `25.00%` for
signature and `27.27%` for successor.

## Compiled First37

- Compiled: `8/37`
- Matched bare: `8/37`
- Gated raw: `8/37`
- Gate activation: `0/873` turns
- Interpretation band: `COMPETITIVE`

Because the gate was always OFF, this run exercised bare Qwen at every turn.
It does not validate compiled memory at task level.

## Decision

Reached branch: `appworld_structured_compiler_competitive`.

VERIFIED:

- The train-side paired causal gate passes on heldout clean-train tasks.
- The structured compiler has a small positive, memory-specific one-step
  effect, but only a PARTIAL_POSITIVE classification.
- The gate activates on none of the first37 test-normal turns, so gated raw
  and gated compiled runs both equal matched bare at `8/37`.

INFERENCE:

- The structured gate/feature distribution does not transfer operationally to
  the first37 test-normal interaction stream under its locked threshold.
- Building the full incremental bank before reviewing this activation-domain
  mismatch is not justified on the submission critical path.

UNVERIFIED:

- End-to-end compiled-memory benefit, full-bank field behavior, multi-seed
  robustness, and generalization beyond this AppWorld-specific feature set.

Recommended next 48 hours: freeze full-bank construction; perform a
deployment-feature-only gate distribution and calibration-domain audit that
uses no test outcomes, then decide whether the honest submission claim is the
validated train-side gate plus partial one-step compiler result or a bounded
negative end-to-end result. Do not start another compiler architecture.

## Implementation Notes

- Eight rows remain missing because Python 3.11 traceback formatting differed
  while task-state fingerprints and terminal exception semantics matched. The
  strict semantic-v3 replay contract was not relaxed.
- Two training attempts stopped before u2 because checkpoint recomputation
  lost residual hooks. Hooks now remain installed through backward.
- Two u2 validation attempts stopped on residual-ratio enforcement. The final
  versioned method captures bare block-input norms with `use_cache=True` and
  projects live residuals with a fixed `0.99` numerical safety margin. The
  scientific per-layer ratio limit remains `<=1.0`.
- Failed attempts produced no accepted checkpoint or scientific condition and
  are preserved in the append-only ledger.

## Verification And Artifacts

- Focused final regression suite: `51 passed` locally; the phased source suite
  also passed on Lambda before the completed scientific run.
- Git-safe evidence: `research/results/exp028a_appworld_structured_gate_compiler/`
- Full Lambda artifacts: `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_structured_gate_compiler_7hr_20260823_001`
