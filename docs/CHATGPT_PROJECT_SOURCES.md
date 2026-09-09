# ChatGPT Project Sources

- Document role: exact list of compact files to add to the RCMF ChatGPT project.
- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Generated-from commit: `4e56702f467635bda120d118a5367c58e593ecef`.
- Canonical source SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- RCMF Harness V1 integration source SHA: `4e56702f467635bda120d118a5367c58e593ecef`.
- Integration archive ref: `archive/rcmf-neutral-harness-v1-integration-4e56702`.
- Final Harness source SHA: `827ed6f394804834e93444c9bb02c435e9e238a3`.
- Last verified UTC: `2026-09-09T09:58:34Z`.
- Latest relevant handoff: `research/handoffs/20260909T095834Z_rcmf_neutral_harness_v1_integration_freeze.md`.

Independently verify the latest pushed GitHub state and canonical manifest
before using these bootstrap caches.

## Authority Order

1. Sealed primary artifacts and source code at the specified commit.
2. The canonical version manifest and `docs/PIPELINE.md`.
3. The relevant dataset `STATE.md`.
4. `docs/HISTORY.md` and `docs/FAILURE_MODES.md`.
5. Conversation memory.

## Project Source Files

- `docs/CHATGPT_ENTRYPOINT.md`
- `docs/PIPELINE.md`
- `docs/SCIENTIFIC_STATUS.md`
- `tasks/alfworld/CHATGPT_CONTEXT.md`
- `tasks/webshop/CHATGPT_CONTEXT.md`

This list is intentionally exactly five files. Historical reports are
demand-loaded through `docs/HISTORY.md` and `docs/FAILURE_MODES.md`; do not add
the entire experiment archive as startup context.

## Documents To Read

- `docs/CHATGPT_ENTRYPOINT.md`
- `docs/PIPELINE.md`
- `docs/SCIENTIFIC_STATUS.md`
- one corresponding dataset `CHATGPT_CONTEXT.md`
- the dataset `STATE.md` linked from that context

Verified: the listed caches route to existing repository documents and both
dataset contexts explicitly deny a scientific result. Unverified: whether a
future ChatGPT project has refreshed these files after a newer pushed commit.

Current blocker/next decision: refresh exactly these five project sources,
then handle ALFWorld prompt/split leakage and WebShop data identity only in
their separate conversations and branches.

## Copy-Ready First Message

```text
Use the RCMF project sources as bootstrap caches only. Independently verify the
latest pushed GitHub commit and canonical manifest, then follow the authority
order in CHATGPT_ENTRYPOINT. Load HISTORY/FAILURE_MODES only when needed and do
not infer an ALFWorld or WebShop scientific result from engineering readiness.
```
