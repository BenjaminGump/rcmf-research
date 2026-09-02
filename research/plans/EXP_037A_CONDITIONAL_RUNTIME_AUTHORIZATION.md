# EXP-037A Conditional Runtime Authorization

## Scope

The user granted a preauthorized ceiling of **200 wall-clock hours** for the
single EXP-037A umbrella run
`rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`.

This authorization becomes active only after the frozen implementation,
resolved arm configurations, source/data manifests, full tests, scheduler and
resume tests, leakage checks, technical smoke, and runtime/storage/cost
preflight all pass and are persisted. No scientific GPU stage may start before
that boundary.

## Automatic Launch Contract

When every preflight check passes and

```
max(2 * expected_total_wall_hours,
    1.25 * conservative_total_wall_hours,
    160 hours) <= 200 hours
```

the pipeline must write `runtime_authorization.json` and immediately launch
the persistent parent orchestrator in tmux. It must run the complete three-demo
reproduction arm. After the sealed development comparison, only
`THREE_DEMO_REPRODUCTION_PASS` may launch the complete one-demo arm, and the
event-driven parent must do so within 60 seconds excluding validator compute.

The 20-minute watchdog is read-only and is not a scheduler. Frontend, SSH,
Git, chat, and user availability are not stage dependencies.

## Early Stop Conditions

Before 200 hours, the scientific DAG may stop only for immutable identity
failure, leakage or data corruption, inability to establish exact resume,
a required semantics-changing repair, persistent hardware failure, a computed
recommended ceiling above 200 hours, or reaching the approved 200-hour cap.
Recoverable infrastructure failures close their attempt and resume from the
last hash-validated atomic checkpoint without changing scientific parameters.

This authorization does not permit changing the model, selector candidates,
CV folds, writer/reader architecture, losses, epochs, prompt profiles, gate,
data, evaluator, or any other scientific semantics.
