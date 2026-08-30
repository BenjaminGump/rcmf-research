# EXP-035A One-Demo Component-Swap Attribution

## Scope

EXP-035A is a frozen evaluation-only diagnostic. It crosses the immutable old
and fresh selector packages with the immutable old and fresh writer/reader
packages. It does not train, calibrate, align, rescale, retrieve, gate, or
change a prompt.

## Frozen Cells

Each cell runs a correct binding and one common preregistered 401-memory
key-payload shuffle on the same ordered eight heldout-train tasks:

- `OO`: old selector plus old writer/reader.
- `OF`: old selector plus fresh writer/reader.
- `FO`: fresh selector plus old writer/reader.
- `FF`: fresh selector plus fresh writer/reader.

The first letter denotes the selector and the second the writer/reader.
Scientific fields contain only the 401 model-training memories. The historical
499-memory fields are used solely as native reconstruction anchors.

## Preregistered Hypotheses

- Selector hypothesis: fresh selector geometry reduces matched
  correct-minus-shuffle trajectory specificity under both readers.
- Writer/reader hypothesis: fresh writer/reader reduces specificity under
  both selectors.
- Coadaptation hypothesis: either cross-cell loses specificity while `OO`
  retains it, with a stable interaction that is not attributable to one
  component.
- Inconclusive: directions conflict, a leave-one-task-out deletion reverses
  the attribution, the effect concentrates in one task, or native anchors do
  not reproduce.

## Fixed Evaluation

- Prompt: `full_demo_first_only`.
- Seed: `25101`.
- Tasks: the immutable eight heldout-train tasks from the EXP-031A 29/8 split.
- Conditions: eight per task, 64 complete trajectories when determinism holds.
- Controls: correct and a single common EXP-031A 401-memory shuffle mapping.
- Prohibited splits: official dev, first37, `test_normal`, `test_challenge`.

The primary per-task outcome is
`d[S,W,t] = success_correct[S,W,t] - success_shuffle[S,W,t]`. The analysis
reports all four cell means, selector and writer/reader marginal contrasts,
their interaction, 100,000 task-grouped bootstrap replicates, exact McNemar
tests, and leave-one-task-out sensitivity. No fixed effect threshold or p-value
alone determines the diagnosis.

## Stop Contract

The experiment ends after component identity validation, native and cross-cell
field checks, smoke, the frozen heldout trajectories, analysis, Git-safe audit,
and structured handoff. Prompt transport, EXP-035B, retraining, calibration,
adapters, and further evaluation require separate approval.
