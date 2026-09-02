# EXP-037A Preflight and Runtime Authorization Handoff

## Scope

EXP-037A builds one reusable, event-driven RCMF pipeline, reruns the complete
three-demo training/evaluation path from authoritative inputs, and launches the
same one-demo pipeline only after exact `THREE_DEMO_REPRODUCTION_PASS`.

## State

- Branch: `research/v6-rcmf-reproducible-3d-gated-pipeline`
- Starting SHA: `53a1c583574d249d63b91a28fbb20ae17a7037b3`
- Scientific source SHA: `02ef94726ea0fe566f7eea4fa137fb91da92977f`
- Run UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`
- Seed: `25101`
- Preflight authorization: all checks passed
- Formal launch UTC: `2026-09-02T20:23:02.803442+00:00`
- Persistent sessions: `exp037a_repro_14b`, `exp037a_watchdog_14b`
- Recommended hard cap: `160 h`
- Approved hard cap: `200 h`

## Verification

Local tests passed `21` focused and `810` full with `2` skipped. Lambda tests
passed `21` focused and `812` full. The H100 technical smoke passed in
`45.994 s` with approximately `17.9 GB` peak allocated memory. Fresh corpus
rebuild counts, lineage checks, arm diff, initialization identity, scheduler,
resume, retry, hard-cap enforcement, and read-only monitor behavior all passed.

The sole preflight repair after the initial implementation derives
`teacher_section_tokens` and section SHA directly from authoritative raw
transition text and the locked tokenizer. It does not consume historical
derived data or change scientific semantics.

## Runtime contract

The expected complete wall time is `47.5 h` if 1D launches; conservative time
is `92 h`; expected H100-active time is `39 h`. Expected/conservative storage
is `46/90 GiB`. No local Lambda hourly rate was available for a cost estimate.

The approval packet was committed and pushed before launch. The Lambda
checkout at the scientific source SHA then wrote the machine-readable runtime
authorization and launched persistent tmux sessions for the parent scheduler
and 20-minute read-only watchdog. The first health check found the worker at
`S05_transition_representations` with an active H100 process. Ordinary
recoverable infrastructure failures close their attempt and exact-resume
without changing scientific parameters.

## Sources of truth

- Report: `research/results/EXP_037A_PREFLIGHT.md`
- Git-safe index: `research/results/exp037a_reproducible_pipeline_preflight/index.json`
- Runtime authorization: `research/results/exp037a_reproducible_pipeline_preflight/runtime_authorization.json`
- Authorization plan: `research/plans/EXP_037A_CONDITIONAL_RUNTIME_AUTHORIZATION.md`
- Pipeline config: `configs/pipeline/rcmf_appworld_repro_14b.yaml`
- Stage DAG implementation: `rcmf/pipeline/stage_graph.py`
- AppWorld adapter: `rcmf/benchmarks/appworld/pipeline_adapter.py`
- Parent launcher: `scripts/launch_rcmf_reproducible_pipeline_14b.sh`
- Raw Lambda root: `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`

## Evidence status

**VERIFIED:** every preregistered preflight and conditional authorization gate
is true; no scientific stage ran before this boundary.

**INFERENCE:** the run should normally complete well below the 200-hour anomaly
ceiling.

**UNVERIFIED:** the 3D reproduction gate and conditional 1D outcomes are not
known yet.
