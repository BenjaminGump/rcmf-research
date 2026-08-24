# EXP-029A On-Policy Fixed Memory-Reader Adapter Pilot

## Identity

- Run UUID: `fixed_memory_reader_adapter_8a_20260824_001`
- Global seed: `25101`
- Starting commit: `be93219b61ff30b50006f0801e93c14c288595d4`
- Archive branch: `archive/v4-structured-compiler-live-specificity-failed`
- Working branch: `research/v4-fixed-memory-reader`
- Initial source commit: `bb0e036d836dc5acf0afe9cbf5c9e9ab516488a5`
- Recovery commits: `d78f7a548e9368f0b5ac639603fcbb4c67744856`,
  `fcc72a143ed1921eaa1cc6b7ad769fa5c4971e02`,
  `fecfc3f8cfca75d517ecbe96b8ce6d85a95a7c2d`, and
  `475e4583058790ef7941b7ab62398a7f02c849ef`
- Frozen parent PairMLP SHA256:
  `8af2d1068ee0059c79edf740920f9d506b8a880459ff53656b449f767e5dffda`
- Preflight contract SHA256:
  `c6163d5ffb8602a69911512ee60020567646f51a84aac31f181db9b38460a24e`

Qwen3-8B, the EXP-025C-R selector, the clean corpus, AppWorld 0.1.0,
layers `[7,14,21,28]`, the last four user-token positions, the canonical
prompt, and all three demonstrations remained frozen.

## On-Policy Data

The deterministic bare-Qwen collection covered all 37 clean train tasks and
froze 222 live states before paired outcomes were generated:

- model-training split: `174` states from `29` tasks;
- heldout train-validation split: `48` states from `8` disjoint tasks;
- paired T0/T1 conditions: `444/444`;
- replay/history exceptions: `0`;
- test-normal states or outcomes used: `0`.

| Split | POSITIVE | NEUTRAL | HARMFUL | Total |
| --- | ---: | ---: | ---: | ---: |
| Model training | 3 | 165 | 6 | 174 |
| Heldout train validation | 2 | 45 | 1 | 48 |
| Combined | 5 | 210 | 7 | 222 |

The preregistered training-only augmentation added 21 immutable positive
expert states from EXP-028A, reaching 24 model-training positives. Validation
remained purely on-policy. The augmentation manifest SHA256 is
`edc89fb03c5f7ecd4a260e13bc420fb61b0260fdfc1e5707a7f5d0fe7898bf17`.

## Reader Contract

Exactly one fixed reader was implemented: a 256D PairMLP latent controls four
layer-specific, position-shared bottleneck-64 readers. The reader has
`2,162,688` trainable parameters, independent of memory-bank size.

All mandatory implementation checks passed:

- `z=0` gives an exact zero residual;
- zero initialization reproduces bare logits, NLL, and deterministic
  generation on four representative states;
- only layers `[7,14,21,28]` and the locked last four prompt positions are
  directly modified;
- generated tokens are not directly injected;
- every reader layer receives a nonzero gradient;
- Qwen trainable-parameter and gradient counts are both zero;
- the student prompt contains no raw transition text;
- the FP32 reader/BF16 residual boundary and activation-checkpoint
  recomputation path are covered by regression tests.

The final focused suite passed `10/10` locally and `10/10` on Lambda.

## Training

Training used `195` correct states: 174 on-policy model-training states plus
21 training-only expert positives. Every positive contributed one
transition mismatch and one state mismatch, producing `243` units:

- correct: `195`;
- transition mismatch: `24`;
- state mismatch: `24`.

Positive and bare-preservation groups each received total weight `121.5`.
Qwen remained frozen for all `972` backward updates.

| Checkpoint | Policy KL | Teacher-token CE | Ground-truth CE | Max layer ratio |
| --- | ---: | ---: | ---: | ---: |
| u1 | 0.039514 | 0.076885 | 1.491668 | 0.000069 |
| u2 | 0.039239 | 0.076643 | 1.492157 | 0.000456 |
| u4 | 0.038568 | 0.076199 | 1.485369 | 0.004515 |

The bounded objective improved smoothly and remained inside the ratio budget.
The scientific decision therefore comes from heldout live generation, not
numerical instability.

## Heldout Live Validation

Each checkpoint executed all `48 x 4 = 192` R1/R2/R3/R0 conditions on the
eight heldout train tasks. Across u1/u2/u4 this is `576/576` fresh same-world
generations and executions.

| Checkpoint | R1 signature | R1 successor | R1 execution | R0 signature / successor / execution | R2 signature / successor | R3 signature / successor | Positive tasks | Class |
| --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- |
| u1 | 0.1875 | 0.1458 | 0.9375 | 0.1875 / 0.1458 / 0.9375 | 0.1875 / 0.1458 | 0.1875 / 0.1458 | 0/8 | CLEAR_FAILURE |
| u2 | 0.1875 | 0.1458 | 0.9167 | 0.1875 / 0.1458 / 0.9375 | 0.1875 / 0.1458 | 0.1875 / 0.1458 | 0/8 | CLEAR_FAILURE |
| u4 | 0.1875 | 0.1458 | 0.9375 | 0.1875 / 0.1458 / 0.9375 | 0.1875 / 0.1458 | 0.1875 / 0.1458 | 0/8 | CLEAR_FAILURE |

R1 raw-policy KL was `0.088364/0.087291/0.087313` at u1/u2/u4, versus
the fixed R0 value `0.087626`. The u4 maximum live layer ratio was only
`0.005052`, yet its generated behavior remained identical to zero and both
memory-specific shuffles on action signature and semantic successor.

No checkpoint met even the PARTIAL gate. Checkpoint selection returned
`selected=null`, and the conditional first37 development audit was not run.

## Decision

Reached branch:

`fixed_memory_reader_failed`

The fixed reader interface is technically valid, and bounded training reduces
its teacher-policy loss, but the correct state-memory program does not improve
heldout live one-step behavior and is indistinguishable from both shuffles.
This is a one-seed bounded negative result, not a proof that every possible
reader architecture is impossible. Under the preregistered submission policy,
however, neural compiled-memory architecture work stops here.

Do not run another reader, adapter, generic PairMLP, factorized field, full
bank, Stage C2, end-to-end RCMF, Qwen training, or V4 tagging for the
submission. The next 48 hours should lock the paper around clean provenance,
replay-valid selection, raw-transition causal benefit, validated free carrier
capacity, and the sequence of controlled amortization failures.

## Runtime And Recovery

- Expected required H100 time before optional first37: `4.4214 h`.
- Accounted active attempt time, including four stopped infrastructure
  attempts: `4.3516 h`.
- End-to-end wall span: `7.5943 h`.
- Durable Lambda artifact size: `1,140,767,963` bytes.
- Attempts: `11` closed, `7` successful and `4` failed.

The four failed attempts were bounded infrastructure recoveries: an incorrect
DecisionExample field access, an FP32/BF16 reader boundary, hook lifetime
through activation-checkpoint recomputation, and a double-prefixed live-bridge
condition key. No failed attempt produced an accepted scientific validation
row, and no model, data, or scientific parameter changed during recovery.

## Artifacts

- Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/fixed_memory_reader_adapter_8a_20260824_001`
- Append-only ledger SHA256:
  `889c0f422c7b3df3139b2d178c5a1eb6f9c82382904f59d448be9fbd84cc6396`
- Frozen on-policy manifest SHA256:
  `1759e74c57fa694ad108038ad9783352ee665d82dbf9752a0b08335ccbf51f60`
- Paired outcome summary SHA256:
  `64cb3e31caf344158665869342f0f1d86d4133c4c4190b78c73b648cce072adc`
- Implementation validation SHA256:
  `5fb24917f1cbcba7ed227a61d8f53928e6c64562e8c02adda81b9a243208149a`
- Training summary SHA256:
  `6e51dda0b5418b236b84f1fc0a7ee04403b5049289f3ba6cd5ef6ca61a2220b8`
- Validation report SHA256s u1/u2/u4:
  `c0f1ca72da86a12c7d31567ae1ddc3660b4644694185afabb5e7287794cd24de`,
  `6b3e8a0b38e06c33c91fdd2d5ff243133e64f31d5bba427bf6774d72754f64b0`,
  `63117ce2f4f866eaa29202aacfe64e391ca4a48dd328702cf45f60abc254e19c`
- Checkpoint selection SHA256:
  `fdbceddf9fdc2537afa5feb7250d7287e972e39f9aa87dc245ffaf88e4ff9996`
- GitHub-safe machine summary:
  `research/results/exp029a_fixed_memory_reader/summary.json`