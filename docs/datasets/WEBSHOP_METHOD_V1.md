# WebShop RCMF Method V1

Status: source candidate; scientific outcomes pending.

## Scope

This profile adapts RCMF to the frozen AgentBench-FC WebShop runtime. The
formal population is exactly `webshop-std` indices `[0,200)`. Indices
`[200,500)` are excluded. Construction uses replay-validated, exact-reward-1.0
`AGENT_GENERATED` trajectories from the frozen train population at indices
`[1500,12000)`. The first construction population is `[1500,2500)` and may
expand only through the already frozen contiguous 250-task rule.

No standard-200 outcome may influence construction, representations,
addressing, writer/reader training, validation, method revision, or freezing.

## Hypothesis and mechanism

The hypothesis is that complete WebShop transitions can be independently
compiled into an additive, reversible field and that a state-conditioned read
from this whole-bank field can improve frozen Qwen3-8B behavior on unseen
WebShop tasks.

Each authoritative memory retains the complete goal, visible pre-action state,
opaque `search[...]` or `click[...]` action, post-action observation,
provenance, parent trajectory, task, and lineage. Frozen Qwen3-8B produces
mean/final-token views for five state sections and five transition sections.
Three independently trained low-rank addressing members produce a 960-value
query/key decomposition. The writer maps the four complete transition sections
to eight 256-value payload slots.

Each memory contributes independently:

```text
A += rho * outer(key, payload)
B += rho * mu * payload
```

The frozen package records every memory's ID, task, key, payload, and weight so
one contribution can be added, removed, or restored without scanning or
recompiling unrelated memories. Freeze runs an explicit remove/restore numeric
audit against the compiled field.

The deployed state is fixed at `A[960,8,256]` and `B[8,256]`, independent of
memory count. Query-time read is a contraction of the query with `A`, followed
by the existing standard field cross-attention reader at Qwen layers
7, 14, 21, and 28. Production performs no top-k, nearest-neighbor search,
per-memory scoring, memory-bank iteration, or raw-memory prompt insertion.

## Training and controls

Addressing supervision treats the aligned replay transition as positive and
four deterministic cross-task transitions as negatives (two same-action-family
and two cross-action-family when available). Construction tasks are partitioned
by a frozen SHA-256 ordering for selector train/validation diagnostics.

Writer/reader training uses at most 384 SHA-256-selected construction states
for two fixed epochs. The queried task's own memories are subtracted from the
training field. Qwen and addressing remain frozen. The terminal completed epoch
is used; there is no metric-selected checkpoint.

Before standard-200 outcomes are visible, three conditions must be frozen
together:

- `B0`: exact zero-field bare Qwen3-8B;
- `RCMF-C`: correct key/payload association;
- `RCMF-S`: matched deterministic key/payload derangement preserving key,
  payload, weight, count, and field-shape multisets.

The fixed validation population is `[500,550)`, 50 tasks, under all three
conditions. The sign-based mechanism gate is `PROCEED` only when the correct
field has strictly positive paired mean reward against both bare and shuffled
controls. This is a directional mechanism check, not an effect-size threshold.
If it fails, the method is revised using train/validation evidence only.

## Failure modes

- sparse exact-success construction corpus;
- state/transition representation truncation or lineage mismatch;
- selector decomposition or calibration failure;
- writer or reader receiving no gradients;
- failure to beat both controls directionally on fixed validation;
- runtime nondeterminism or replay mismatch;
- any standard-200 access before the source, package, conditions, and ordered
  task manifest are frozen.

## Reproducibility contract

The executable configuration is
`configs/datasets/webshop_method_v1.json`. Every Lambda phase is bound to the
source commit, configuration hash, construction corpus and manifest hashes,
task catalog, runtime identity, and Qwen snapshot. Representation items,
training checkpoints, evaluation tasks, and summaries are written atomically
under one persistent run root and validate exact identity on resume.
