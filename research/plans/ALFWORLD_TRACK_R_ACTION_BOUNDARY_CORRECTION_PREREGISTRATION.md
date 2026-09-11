# ALFWorld Track R Action-Boundary Correction Preregistration

Status: `PREREGISTERED_BEFORE_CORRECTED_MODEL_EXECUTION`

Effective UTC date: `2026-09-11`

## Trigger and evidence firewall

This correction is triggered only by the existing six-task-per-condition TRAIN
sanity logs and CPU replay evidence. It is not selected from a `valid_unseen`
score, task outcome, family result, or model comparison.

- Source under audit: `12d4b1ac0ff8d1afb374010dc1a65404ef7ff269`.
- Bare TRAIN log SHA-256:
  `ec98528327156d7f07507189c2ba0c0215d12598db3886f40ec753144789123a`.
- RCMF TRAIN log SHA-256:
  `93c92b40f534ad1604e88e609171bc02efb62ccd4f8f73ef69d27a67c6bc8fcd`.
- Across 588 recorded steps, every decoded first line began with the prompt
  transcript marker `>` and every actual environment feedback string was
  exactly `Nothing happens.`. Reward, `done`, and `won` were always false.
- On the same reset of TRAIN task
  `alfworld:trial_T20190906_164825_242073`, the recorded command
  `> go to fridge 1` was not admissible and returned `Nothing happens.`;
  removing exactly one transcript marker produced the admissible command
  `go to fridge 1` and returned the real fridge state.
- Replaying all already-generated TRAIN first lines with this one-marker
  normalization recovered substantive feedback for 53 bare and 50 RCMF
  non-think steps. These replay outcomes are engineering evidence only, not a
  model score or tuning signal.

The invalid first complete `valid_unseen` attempts remain preserved and
scientifically ineligible because of their independently discovered task-order
mismatch. Their scores are not inputs to this correction.

## Hypothesis and mechanism

The ReAct notebook appends `\n>` to the prompt, requests a newline-stopped
completion, strips the returned completion, and sends that completion as the
action. The pinned demonstrations render transcript turns as `> action`, but
the prompt-owned marker is not part of the text command. Qwen may echo that
marker at the start of its decoded continuation. Treating the echoed marker as
an opaque TextWorld command prevents both environment interaction and the
notebook's `think:` observation rule.

The prospective extraction rule is therefore:

1. take the first decoded line only;
2. strip surrounding whitespace;
3. if and only if the result begins with `>`, remove exactly one leading `>`
   and following whitespace;
4. reject the result if it is then empty;
5. otherwise preserve every remaining byte as the opaque ALFWorld action;
6. identify `think:` only after this normalization; still call the environment
   exactly once, then expose `OK.` to the prompt as in the pinned notebook.

No later decoded line may be searched, retried, or substituted. No action
grammar ranking, task-dependent repair, model/prompt change, or outcome-based
fallback is allowed.

## Contract and identity consequences

This is a behaviorally relevant action-extraction change. It must not reuse
the old generation identity
`6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9`
or Track R execution-lock identity
`055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de`.
The Harness must publish a versioned generation/action contract and Track R
execution lock before any corrected model run. Raw generation settings remain
unchanged.

The analyzer must also reject a result whose embedded benchmark-lock file or
content identity differs, even when its task set and order are correct. The
real 134-task CPU closure at `12d4b1a` already proves correct order acceptance,
wrong physical order rejection, wrong embedded order rejection, and recovery
of exact manifest order `c49e3fab...` from reversed real adapter input; it also
exposed the missing embedded-lock guard.

## Frozen elements

The following remain unchanged: all 134 tasks; manifest order
`c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8`;
task-set identity `2410f2c2...`; Track R prompt bytes; Qwen revision; tokenizer;
chat template; context; greedy decoding; 512-token maximum; 49-action cap;
microbatch scheduling; evaluator; TRAIN corpus; ledger; training configuration;
terminal checkpoint; and both memory conditions. No model is retrained.

The not-yet-started order-only corrective UUIDs are not reusable under the new
contract. They will be recorded as superseded before execution, and new matched
bare/RCMF UUIDs will be declared after the Harness publishes the corrected lock.

## Pre-model validation gate

Before any corrected model execution:

- unit tests must cover marker-present, marker-absent, double-marker,
  marker-only, whitespace, think, and opaque-command cases;
- a real TRAIN reset must prove current-vs-normalized command behavior;
- the existing TRAIN logs must replay without accessing evaluation outcomes;
- the complete real 134-task order and embedded-lock closure must pass;
- the Harness and RCMF repositories must be clean and pushed;
- WebShop must explicitly hand back an empty H100.

Only after that gate may one bounded matched TRAIN model sanity be run on
Lambda under the new contract. If it proves structural interaction without a
new defect, both full 134-task conditions must be rerun under new identities.
No success threshold or performance search is permitted.
