# ALFWorld Track R Action-Dialect Correction Preregistration

Status: `PREREGISTERED_BEFORE_ACTION_DIALECT_IMPLEMENTATION_OR_RETEST`

Effective UTC date: `2026-09-11`

Starting RCMF records commit:
`db4d6eb748635a6b75612f660fcd51b2e9a65cbf`.

Harness preregistration commit:
`8e425c3` on branch `dataset/alfworld-action-dialect-correction-v1`.

## Trigger and evidence firewall

The completed action-boundary-v2 bare arm remains immutable. It completed all
134 tasks with 16 official successes and no typed infrastructure failure. The
complete CPU reconstruction checked all 6,215 generated steps and found no
error in first-line decoding, one-marker removal, environment stepping, or
done/won/evaluator correspondence. It also found that all 549 model actions
beginning with `put` received exactly `Nothing happens.`.

That evaluation observation triggered protocol inspection. It does not choose
a prompt, model, generation setting, checkpoint, task, or competing repair.
The defect is independently established by exact upstream source and TRAIN:

- pinned ReAct commit `6bdb3a1...` teaches
  `put <object> in/on <receptacle>` in the exact prompt asset, and its notebook
  sends the newline-stopped first action directly to `env.step`;
- pinned ALFWorld commit `aaba687...` defines the TextWorld `PutObject` action
  as `move {o} to {r}` with `You move ...` feedback;
- the ReAct README delegates to ALFWorld installation instructions and does not
  pin an exact compatible ALFWorld revision;
- on the pre-existing fixed six-family TRAIN selection, same-state fresh resets
  over all five placement-requiring families made the ReAct `put` command
  inadmissible and inert in 5/5, while the official `move` command was admissible
  and exactly reproduced official observation/reward/done/won in 5/5.

The preserved v2 bare result is therefore
`INVALID_ACTION_DIALECT_MISMATCH`, not an eligible scientific result. RCMF-C
UUID `2a86c6d6-3095-40a8-be12-0b2ba33d67a0` remains not started and is
superseded before execution.

## Hypothesis and mechanism

Hypothesis: the approved upstream ReAct prompt and pinned ALFWorld deployment
interface become protocol-compatible through one exact, method-neutral action-
dialect bridge at the environment boundary.

After the frozen v2 first-line/one-marker normalization, match only:

`^put ([a-z][a-z0-9]* [1-9][0-9]*) in/on ([a-z][a-z0-9]* [1-9][0-9]*)$`

and translate it to:

`move {object} to {receptacle}`

The match is case-sensitive and anchored. Native `move` and every nonmatching
action pass byte-for-byte. Do not translate malformed `put`, `place`, `insert`,
`drop`, task-dependent alternatives, or outcome-dependent retries. Preserve
raw model text, raw first line, normalized model action, actual environment
command, bridge flag, observation, reward, done, won, and evaluator result.
The prompt history retains the normalized model action. The environment is
stepped exactly once.

This rule belongs to the Harness-owned ALFWorld execution contract and applies
identically to bare and RCMF. It is not an RCMF memory behavior.

## Frozen elements

Unchanged: approved E+A prompt bytes; Track R prompt asset/profile; 134-task set;
frozen `c49e3fab...` manifest order; Qwen/model/tokenizer/chat/context identity;
greedy generation settings; 512-token per-turn maximum; 49-action cap;
microbatch scheduling; evaluator; TRAIN corpus and ledger; RCMF architecture,
training configuration, and checkpoint
`6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
No model or checkpoint is retrained.

Behaviorally relevant changes require a new Harness action/generation identity,
v3 execution lock, RCMF source binding, episode audit fields, source commit, and
archive. Historical locks and runs remain valid history but ineligible for the
new comparison.

## Pre-model gate

Before any model retest:

- implement only the exact bridge at the dataset adapter/environment boundary;
- test exact match, native move pass-through, malformed and alias pass-through,
  case sensitivity, one-step execution, separate model/environment action
  recording, and content-addressed lock rejection;
- rerun the five-family same-state TRAIN probe with Harness-owned code where
  practical;
- close all 134 real task IDs/order/embedded identities on CPU;
- prove approved prompt bytes and generic Harness code are unchanged;
- publish both source commits and archives; and
- confirm no conflicting GPU owner before later model execution.

## First-30 improvement gate

The next model run is bare only and contains exactly the first 30 tasks in the
unchanged frozen manifest order. The immutable v2 reference is 13/30 official
successes: 13/18 `look_at_obj`, 0/12 `pick_and_place`.

The gate passes only if:

1. all 30 rows appear once in exact frozen order with exact source/model/prompt/
   generation/action-bridge/lock/evaluator identities;
2. at least one exact bridge activates;
3. zero matched ReAct `put ... in/on ...` actions reach the environment
   literally;
4. every environment command equals the preregistered transformation or exact
   pass-through;
5. done/won/reward/evaluator correspondence is exact and typed infrastructure
   failures are zero;
6. total official successes exceed 13/30; and
7. `pick_and_place` official successes are at least 1/12.

If it fails, stop after 30 and diagnose; do not run 134 and do not launch
RCMF-C. If it passes, one fresh full-134 bare run under the identical v3
identity may start. RCMF-C remains blocked until that full bare run completes
and passes a complete structural audit.

This is a single fixed repair check requested by the user. It does not authorize
valid-unseen performance search, prompt/model/config tuning, task filtering, or
checkpoint selection.

## Decisions

- Pre-model source gate pass:
  `READY_FOR_ACTION_DIALECT_FIRST_30_BARE_RETEST`.
- First-30 pass: `READY_FOR_CORRECTED_FULL_134_BARE`.
- First-30 fail:
  `STOP_ACTION_DIALECT_CORRECTION_DID_NOT_IMPROVE_FIRST_30`.
- Identity ambiguity:
  `INCONCLUSIVE_ACTION_DIALECT_IDENTITY_UNRESOLVED`.
- Frozen invariant change: `STOP_FROZEN_BENCHMARK_INVARIANT_CHANGED`.

No other outcome threshold is authorized.
