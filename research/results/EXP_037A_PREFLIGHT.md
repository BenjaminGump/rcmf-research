# EXP-037A Reproducible Pipeline Preflight

## Status

**VERIFIED:** all nine conditional runtime-authorization gates passed. The
frozen three-demo reproduction run is authorized to launch automatically after
this Git-safe approval package is committed and pushed.

This is a preflight record, not a scientific result. No formal EXP-037A stage
had run when this packet was sealed.

## Frozen identities

- Run UUID: `rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001`
- Starting commit: `53a1c583574d249d63b91a28fbb20ae17a7037b3`
- Base implementation commit: `13641e3a260540da9e14b2568ae47bcfeb38f50d`
- Scientific source commit: `02ef94726ea0fe566f7eea4fa137fb91da92977f`
- Global seed: `25101`
- Pipeline stages: `57`
- Resolved 3D config SHA256: `80fe0ae194676c4d4ed8af9769fbaf5ec28746aa285d11e2f615ea8de36170d6`
- Resolved 1D config SHA256: `16a250baaf99615dd109c1b901f293d0b7aa6ff2623055ed060b5ed97683052e`
- Stage DAG SHA256: `42b8e5f5cc84749832c45387061bd28b8d109ac7859923292e09f8daf2abb97f`
- Authoritative-source manifest SHA256: `e57e7d4f26a5dc278afe048a32cc44aaf0ecc1371e5415edbe9e39e8edba8dc0`
- Initialization manifest SHA256: `a932641bf91b264ddab09a1e0b34062c036d0dad10c8a39740b916f0467b2a46`

The two resolved arms have 21 path/profile/run-identity differences and zero
differences outside the frozen allowlist. The only scientific intervention is
`full_demo` versus `full_demo_first_only` on task-conditioned paths.

## Authoritative data

The shared CPU rebuild used the identity-reconciled corpus and clean replay
lineage only. It produced `499` train transitions, `499` selector-train states,
`139` selector-validation states, `150` signature classes, `310,433` legal
pairs, and `7,929` illegal pairs. All expected count gates passed.

Transition teacher-section token metadata was freshly derived from canonical
raw transition text with the locked Qwen tokenizer, no special tokens, and no
truncation. No historical derived artifact was loaded.

## Environment

- Host: Lambda H100
- GPU: NVIDIA H100 80GB HBM3
- Python: `3.11.15`
- PyTorch: `2.11.0+cu128`
- Transformers: `4.57.6`
- CUDA: `12.8`
- `PYTHONHASHSEED=25101`
- `CUBLAS_WORKSPACE_CONFIG=:4096:8`
- AppWorld legacy root: `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root`
- Runtime Python: `/home/ubuntu/venvs/rcmf-py311/bin/python`

The writer initialization has `8,949,760` parameters and SHA256
`6f60556e6969011e58853ca08d2e70e4570fb69927ec0bb60a10c16cbde7f4ae`.
The reader initialization has `17,860,608` parameters and SHA256
`d9813fe66df24e67912f8505de38c8df1fd850c370b8cdf512df029b0e6ca319`.
Nine CV selector snapshots and three final-member snapshots with seeds
`25071, 25072, 25073` were created from the shared initialization contract.

## Verification

- Local focused tests: `21 passed`
- Local full tests: `810 passed, 2 skipped`
- Lambda focused tests: `21 passed`
- Lambda full tests: `812 passed`
- Source/config identity: passed
- Leakage and lineage checks: passed
- Scheduler, resume, retry, hard-cap, and 20-minute read-only monitor tests: passed
- Git-safe secret scan: passed; 14 raw JWT-regex candidates were all seven
  duplicated dot-separated configuration-key paths, and credential-assignment
  matches were zero

The bounded H100 technical smoke completed in `45.994` seconds with peak GPU
allocation `17,903,331,840` bytes. It exercised both prompt profiles, four raw
transition readouts, one selector update, three final-member constructions,
paired causal generation, a policy-teacher score, two writer/reader backward
updates, reversible 401-memory field construction, and one heldout-train
AppWorld task under D0/D1/D2 for one step in fresh worlds.

The smoke field had shapes `A=[960,8,256]`, `B=[8,256]`; its reversible rebuild
maximum absolute error was `0.0`, and its deterministic shuffle had zero fixed
points. All three AppWorld conditions completed with zero infrastructure
exceptions. The mock PASS path traversed all 57 stages and launched 1D with a
maximum transition delay below one millisecond; the fail-gate path stopped
after 37 stages without launching 1D.

## Workload and authorization

Per arm, the frozen upper-bound workload is `638` state-representation
forwards, `37,800` selector CV updates, at most `22,680` final-selector updates,
`928` paired causal generations, `464` policy-teacher forwards, `464` zero
forwards, `1,152` writer/reader backwards, `784` heldout teacher-forced
conditions, `784` heldout one-step conditions, and `64` heldout complete
trajectories. The shared phase has `499` transition-representation forwards.

The complete 3D Dev arm has `171` trajectories. The conditional 1D Dev arm has
`114`; the maximum is `285` Dev trajectories.

- Expected wall time if 1D launches: `47.5 h`
- Conservative wall time: `92 h`
- Expected H100-active time: `39 h`
- Expected storage: `46 GiB`
- Conservative storage: `90 GiB`
- Cost estimate: unavailable because no hourly rate is configured locally
- Recommended hard cap: `160 h`
- User-approved hard cap: `200 h`

The recommendation is
`max(2 * 47.5, 1.25 * 92, 160) = 160` hours, so the approved 200-hour anomaly
ceiling is valid. The parent orchestrator uses atomic stage outputs,
append-only attempts, hash validation before skips, parent-only restart, and
resume at the first incomplete stage.

The 20-minute monitor is read-only. Only the event-driven parent scheduler may
advance stages. Exactly `THREE_DEMO_REPRODUCTION_PASS` may launch the 1D arm.

## Artifacts

Git-safe, content-addressed evidence is indexed in
`research/results/exp037a_reproducible_pipeline_preflight/index.json`.

Raw preflight artifacts and initialization tensors are under:

`/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14b_20260903_001/preflight`

## Interpretation

**VERIFIED:** the implementation, identity, leakage, tests, technical smoke,
runtime, storage, scheduler, resume, and authorization gates pass.

**INFERENCE:** the historical throughput anchors make the 160-hour recommended
ceiling conservative enough for ordinary variance while retaining the
user-approved 200-hour hard stop.

**UNVERIFIED:** scientific reproduction performance remains unknown until the
formal 3D arm completes.
