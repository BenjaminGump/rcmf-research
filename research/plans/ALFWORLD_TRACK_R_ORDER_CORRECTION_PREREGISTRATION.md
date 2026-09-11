# ALFWorld Track R Execution-Order Correction Preregistration

Status: `PREREGISTERED_AFTER_INVALID_ATTEMPT_AND_BEFORE_CORRECTED_OUTCOMES`

Date: 2026-09-11 (Asia/Singapore), 2026-09-10 UTC

## Trigger and invalidation

Post-run identity verification found that the first full bare and RCMF
attempts used the exact 134-task set in lexicographically sorted task-ID order,
whose canonical list SHA-256 is
`2410f2c2a92346d63bc00e403a51122c8123d3978c3c29f8c86864556ead5534`.
The frozen Harness execution lock instead requires the original sealed
task-manifest order, whose canonical list SHA-256 is
`c49e3fab674d64878b529d5ab12b9ab2e6cc971ed71513cb16ea2c28103fd7c8`.

The adapter's ordinary task listing sorted each split, and the formal runner
checked the task set but did not fail closed on the ordered list. The analyzer
also checked the set but not the order. Consequently, both first arms and their
paired/P00-P11 descendants are classified
`INVALID_EXECUTION_ORDER_MISMATCH`. Their outputs remain preserved and cannot
be used as scientific evidence or as inputs to configuration choices.

This defect was discovered by comparing frozen and emitted identity hashes,
not by examining success, reward, action, or family outcomes.

## Outcome-independent repair

The repair is fixed before corrected outcomes:

1. Read the already sealed task manifest through its exact identity validator.
2. Extract `valid_unseen` task IDs in their existing manifest order.
3. Require the resulting hash to equal the execution lock's existing
   `deterministic_evaluation_order_sha256` value `c49e3fab...`.
4. Reindex the adapter tasks to that exact order without adding, dropping, or
   changing a task.
5. Store that exact ordered hash in every episode/run identity.
6. Make the paired analyzer reject any arm whose physical row order or embedded
   run order differs from `c49e3fab...`.
7. Add focused regressions and rerun the complete source test suite.

No lock, task, prompt, model, tokenizer, chat template, generation parameter,
batch size, evaluator, memory, architecture, training setting, or checkpoint
changes. The existing terminal checkpoint remains eligible because evaluation
order does not enter corpus construction or training.

## Corrective run identities

- Corrected bare full 134-task run:
  `511d5d8a-3203-43c5-9464-db896934b685`.
- Corrected RCMF full 134-task run:
  `d5cea7e7-ca8a-4b9d-b13b-97664274badf`.
- Corrected paired-analysis/P00-P11 evidence identity:
  `cea3800b-77e2-4b24-bbb7-efa2cdaa1b8a`.

Each formal arm must use a new empty output root. Resume is allowed only within
the same arm identity after strict existing-row identity validation. No row
from an invalid first attempt may be copied or reused.

## Frozen scientific contract

- Track: `alfworld_upstream_react_valid_unseen_reference_v1`.
- Role: `UPSTREAM_PROTOCOL_REFERENCE`.
- Population: all 134 official `valid_unseen` tasks.
- Task-set SHA-256: `2410f2c2...` (canonical sorted set).
- Evaluation-order SHA-256: `c49e3fab...` (canonical manifest-order list).
- Lock identity: `055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de`.
- Prompt/model/chat/generation/evaluator: unchanged from the original
  preregistration at commit
  `efe9f8a0e9002c852804faabb303f45ef14fc876`.
- Checkpoint SHA-256:
  `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
- Generation batch size: six, preserving consecutive manifest-order groups.
- Primary endpoint and paired analysis: unchanged.

The corrected bare and RCMF arms must both complete all 134 tasks. If either
corrected arm exposes a distinct genuine implementation defect, invalidate all
scientifically affected conditions and apply the existing prospective repair
rule. Poor performance is not a defect.

## Outcome firewall

The invalid outcomes are exposed but scientifically ineligible. They cannot
change the repair or any scientific setting. The only permitted difference in
the corrective source is fail-closed enforcement of the already frozen order
plus tests/records. Corrected results complete the original hypothesis whether
RCMF wins, ties, or loses.

## Compute and coordination

The first complete attempts measured 6.80 and 6.94 hours, respectively, so
each corrected arm remains confidently below the 18-hour single-run approval
gate. ALFWorld will wait for WebShop's explicit H100 handback and independently
verify an empty GPU process inventory before launch. The workload will not be
reduced or split.
