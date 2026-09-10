# Reproducible Benchmark Adapter V2.1

The sole authoritative interface for new RCMF ports is
`ReproducibleBenchmarkAdapterV2` in
`rcmf/pipeline/portable_v2/adapter.py`. Existing implementations may be wrapped,
but no second portable protocol may compete with it.

## Identity And Capabilities

`identity()` returns benchmark, benchmark version, adapter protocol version,
environment version, data version, determinism, opaque-action semantics, and
reward semantics. `capabilities()` may declare only methods backed by their
runtime prerequisites. The selected phase graph derives its own required
capability set; `probe_adapter_capabilities()` then exercises those methods on
a bounded fixture before model loading or training.

Required capabilities are stable splits, a successful trajectory source,
reset-and-replay, state/transition rendering, runtime token counting, causal
supervision, interactive runtime, official evaluation, and audit redaction.
The trajectory capability is provenance-neutral: a provider may declare any
admitted provenance class, but every emitted row must be successful,
replay-validated, source-identity matched, and restricted to a declared
training split. `OFFICIAL_TRAJECTORIES` remains readable only as a deprecated
historical capability name and does not satisfy the current phase contract.

Optional capabilities are not globally mandatory. For example, a provenance
phase does not require interactive runtime, while a causal or evaluation phase
does. A token-count claim requires a configured exact counter, and an
interactive-runtime claim requires a configured runtime factory.

## Phase Executor Binding

The dataset profile and pipeline config bind an exact adapter factory and an
exact `PortablePhaseExecutorV2` factory. The portable core owns the P00-P11
semantic DAG and never dispatches on benchmark names. The adapter-owned
executor maps each supported semantic phase to bounded real work and cannot
change dependencies or manufacture a pass manifest: every invocation must
return nonempty work evidence and output artifacts, and the generic wrapper
hashes source/run/config/profile/adapter/dependency/input/output identities.

AppWorld V2.1 provides a legacy-compatibility executor that wraps reviewed
production functions. ALFWorld and WebShop must provide compact adapters and
handler bindings, not edited copies of the full pipeline.

The bounded AppWorld V2.1 pilot exercised every P00-P11 executor boundary,
including real optimizer work, terminal-checkpoint validation, field algebra,
generation, and evaluation. This proves the executable binding for AppWorld;
it does not claim ALFWorld/WebShop executor support or scientific validity.

## Methods

- `list_tasks()` returns arbitrary named splits of validated `TaskRecord`s.
- `trajectory_sources()` declares provider identity, provenance, and admitted
  training splits. Source IDs and training splits are unique and every split
  must exist in `list_tasks()`.
- `successful_trajectories(split)` emits only successful, replay-validated
  `TrajectoryRecord`s.
- `transition_records(task, trajectory)` emits complete opaque-action
  `TransitionRecord`s with stable lineage.
- `decision_states(task, trajectory, profile)` emits replay-addressable states.
- `prompt_profiles()` declares named content-addressed profiles.
- `render_messages()` returns exact structured chat-message arrays; entries may
  include benchmark-required fields such as assistant `tool_calls` and
  `tool_call_id`, and the core adds no prompt content.
- `count_runtime_tokens()` must match the generation backend's exact template,
  including adapter-owned tools where the benchmark uses function calling.
- `build_selector_supervision()` supplies benchmark compatibility evidence.
- `causal_conditions()` builds bare/conditioned conditions without consulting
  outcomes.
- `compare_causal_outcomes()` retains raw reward and maps evidence to canonical
  causal labels; replay/missing errors are typed separately.
- `create_runtime()`, `replay_to_state()`, `validate_action()`, and
  `execute_action()` implement isolated reset/replay and opaque actions.
- `evaluate_task()` returns raw reward, optional binary success, terminal state,
  steps, exceptions, benchmark metrics, and audit references.
- `redact_audit_record()` removes secrets while preserving typed placeholders
  and raw evidence hashes.

## Provenance

Admitted classes are `OFFICIAL_EXPERT`, `OFFICIAL_HUMAN`,
`OFFICIAL_HUMAN_SAMPLE`, `OFFICIAL_MODEL_OR_IL`,
`ORACLE_GENERATED_FROM_TRAIN_METADATA`, and `AGENT_GENERATED`. Providers may not
guess or merge these. `UNKNOWN_PROHIBITED` cannot enter the corpus. A source
must include its repository/archive/session and content identity.

## Error Semantics

Missing capabilities, prompt profiles, ownership, files, hashes, stable IDs,
or replay support raise explicit preflight errors. Replay failure is not
neutral reward. Partial credit is not binary success unless the adapter's
predeclared official semantics say so. Environments that cannot clone a
mid-state must declare and implement reset-and-replay.

## Prompt Contract

Assets are immutable and include upstream repository, full commit, source path,
blob/file hash, extracted text hash, local hash, license, extraction method,
named profile, and content-change flag. The adapter assembles task-family or
action-space examples and returns canonical messages. It must prove static and
runtime token counting match and declare any system prompt. Evaluation tasks
cannot appear in demonstrations.

## Adapter Permissions

Adapters may define task/split identities, trajectory sources, action grammar,
prompt profiles, replay, reward/success mapping, evaluation, and redaction.
They may not change writer/field/reader math, add runtime retrieval, inject raw
memory text, alter terminal checkpoint policy, silently filter states, or tune
on evaluation outcomes.

## Dataset Mapping

ALFWorld requires text-command actions, binary success, task-family prompts,
official expert plans replayed through TextWorld, and train/valid seen/valid
unseen separation. WebShop requires search/click actions, continuous reward,
exact-1.0 full success, server/index/observation-mode identity, and distinct
human/sample/IL/oracle provenance. AppWorld is implemented as a compatibility
facade over its existing validated renderer/replay/evaluator helpers.

Conformance is exercised by real AppWorld-shaped golden fixtures and two
variable mocks: ALFWorld-like reset-and-replay/binary reward and WebShop-like
continuous reward/action grammar. The same generic DAG must accept all without
source edits, including relocated full and continuation roots.

## Neutral Harness Boundary

Final Neutral Harness V1 is pinned by
`configs/harness/neutral_harness_v1.lock.json`, not by a floating branch or tag.
The adapter supplies dataset-semantic evidence to a Harness-owned benchmark
lock; the RCMF plugin supplies method identity and method-owned terminal
checkpoint/field state. The Harness retains ownership of the base prompt,
tasks, environment, evaluator, task-result rows, semantic finalization, and
comparison eligibility.

An adapter cannot mutate Harness-owned benchmark truth or put raw memories in
the shared prompt. Same-run dependencies remain exact-identity closed;
cross-run input requires typed `SEALED_UPSTREAM` evidence. The executor
registry and dataset semantic identity proofs remain prelaunch requirements.
Final Harness source/schema mismatch fails before any dataset execution.

This shared integration does not create an ALFWorld or WebShop benchmark lock.
Their readiness blockers must be resolved in their isolated dataset branches.
