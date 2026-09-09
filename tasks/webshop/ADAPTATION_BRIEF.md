# WebShop Adaptation Brief

- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`
- Portable V2.1 source: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Final RCMF Harness V1 integration source:
  `4e56702f467635bda120d118a5367c58e593ecef`
- Integration archive: `archive/rcmf-neutral-harness-v1-integration-4e56702`
- Final Harness source: `827ed6f394804834e93444c9bb02c435e9e238a3`
- WebShop readiness source/records: `2072ae59b79171facabb2b580cda0c2ae460cd8e` /
  `40d6318892e03d7e775ed83704dae89917f7b324`
- Current decision: `STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED`
- Last verified UTC: `2026-09-09T09:58:34Z`

Start from the final RCMF Harness V1 integration records branch in a dedicated
`adapt/webshop-v1` worktree and isolated server/process namespace. Read
`AGENTS.md`, `docs/PIPELINE.md`, `docs/ADAPTER_CONTRACT.md`,
`tasks/webshop/STATE.md`, and the exact readiness records before editing.
Preserve the Portable V2.1 core and Final Harness lock.

Source, code license, ReAct prompt, action/reward semantics, and the seed-233
split algorithm are verified. The separately linked 1,643-session archive has
a sealed byte/hash and structural summary but remains inspection-only. The IL
archive remains `UNKNOWN_PROHIBITED`; no oracle was implemented or used.

The blocking input is a primary or maintainer-signed identity/terms manifest
for the complete product file, instructions, Lucene index, and 50-session setup
sample. Without it, no exact ordered task manifest or admitted replay
population can be constructed. The next bounded task is to obtain and validate
that manifest, then rerun the readiness probe before choosing replay IDs. Do
not infer provenance from filenames or solve the blocker in shared docs.

After that gate passes, implement only the WebShop adapter, dataset profile,
prompt asset, task records, and tests. Preserve continuous reward and exact
reward `1.0` success semantics, unique server ports, and evaluation isolation.
Stop before freezing a benchmark lock, running Qwen/training, generating an
oracle, or any execution plausibly exceeding 18 hours.
