# ChatGPT Project Sources

- Document role: exact list of compact files to add to the RCMF ChatGPT project.
- Generated-from commit: `ca799ffa5692678081d69f4994e470267def4ec5`.
- Canonical source SHA: `PORTABLE_CANONICAL_SOURCE_SHA`.
- Last verified UTC: `2026-09-08T09:43:34Z`.
- Latest relevant handoff: `research/handoffs/20260908T071524Z_exp037a_r19_epoch2_sensitivity.md`.

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

Current blocker/next decision: add exactly these five files to the existing
RCMF ChatGPT project, then open separate ALFWorld and WebShop conversations.

## Copy-Ready First Message

```text
Use the RCMF project sources as bootstrap caches only. Independently verify the
latest pushed GitHub commit and canonical manifest, then follow the authority
order in CHATGPT_ENTRYPOINT. Load HISTORY/FAILURE_MODES only when needed and do
not infer an ALFWorld or WebShop scientific result from engineering readiness.
```
