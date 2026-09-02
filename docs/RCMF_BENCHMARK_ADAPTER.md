# RCMF Benchmark Adapter

`ReproducibleBenchmarkAdapter` is the only benchmark-facing protocol used by
the generic pipeline. A benchmark implementation supplies:

- `benchmark_identity` and `list_splits`;
- successful authoritative trajectories and canonicalization;
- complete transition extraction;
- task, episode, replay, and lineage keys;
- state and transition rendering;
- optional selector supervision;
- paired causal teacher conditions;
- action execution and authoritative task evaluation;
- audit redaction.

The AppWorld implementation delegates to the repository's existing prompt,
transition, procedural-supervision, bridge, and evaluator helpers. App/API
parsing and procedural concepts remain inside that adapter and its historical
AppWorld sources. They are not presented as benchmark-generic.

The mock adapter provides a deterministic integration fixture. The template
adapter deliberately raises `NotImplementedError`; it documents the required
surface without pretending that ALFWorld or WebShop has been ported.

Adapters may produce benchmark-specific supervision tensors. They may not
change the writer, reversible field, fixed-size read, reader mathematics,
atomic artifact conventions, or event scheduler.
