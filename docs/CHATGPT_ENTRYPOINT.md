# ChatGPT Entrypoint

- Document role: compact project bootstrap cache; not a primary source.
- Generated-from commit: `ca799ffa5692678081d69f4994e470267def4ec5`.
- Canonical source SHA: `ea152c7393056d9f8502bdef87b0b0c34d1f1d89`.
- Last verified UTC: `2026-09-08T13:44:52Z`.
- Latest relevant handoff: `research/handoffs/20260908T134452Z_rcmf_portable_v2_m1.md`.

Independently verify the latest pushed GitHub branch, commit, and canonical
manifest before relying on this cache.

## Authority Order

1. Sealed primary artifacts and source code at the specified commit.
2. The canonical version manifest and `docs/PIPELINE.md`.
3. The relevant `tasks/<dataset>/STATE.md`.
4. `docs/HISTORY.md` and `docs/FAILURE_MODES.md`.
5. Conversation summaries or remembered context.

## Documents To Read

- `AGENTS.md`
- `docs/PIPELINE.md`
- `docs/SCIENTIFIC_STATUS.md`
- the relevant `tasks/<dataset>/STATE.md`
- the relevant `tasks/<dataset>/CHATGPT_CONTEXT.md`

Read `docs/HISTORY.md` or `docs/FAILURE_MODES.md` only when a current question
requires historical detail.

## Project Summary

RCMF tests whether a ledger of complete memories can be compiled independently
into reversible contributions to one fixed-dimensional whole-bank field, then
read through a fixed-size state-conditioned interface that improves a frozen
model. The intended contribution is the feed-forward writer, reversible field,
fixed-size whole-bank read, and frozen-policy reader framework.

Adding/removing one memory cannot retrain or scan unrelated memories. Core
add/remove cost and read shape/complexity are independent of bank size. Raw
memories remain authoritative. Production forbids runtime retrieval/top-k,
FAISS, per-memory scoring, raw-memory query prompts, result-tuned gates,
truncation/subsampling, and evaluation-led selection.

Portable canonical v2 is an engineering base with one adapter contract and a
prospective final-configured-epoch deployment policy. AppWorld provides sealed
historical scientific evidence. ALFWorld and WebShop have pinned prompt sources
and readiness plans but no scientific RCMF result.

Verified: formal AppWorld 14n completed; its one-demo epoch-1 result is negative,
and R19 epoch 2 is post-hoc mixed/inconclusive. Unverified: cross-dataset
scientific validity, ALFWorld/WebShop environments, replay corpora, and formal
results. The matched-shuffle anomaly is deferred.

Current blocker/next decision: each dataset must independently seal its
environment, data, official trajectory replay, split/leakage, prompt-token, and
adapter conformance before any scientific preflight or approval request.

## Review Protocol

Check commit identities and strict manifests first; separate VERIFIED,
INFERENCE, and UNVERIFIED; never convert remembered chat context into an input.
Route architecture questions to `docs/PIPELINE.md`, method boundaries to
`docs/ADAPTER_CONTRACT.md`, current claims to `docs/SCIENTIFIC_STATUS.md`, and
dataset execution decisions to that dataset's task state/context.

## Copy-Ready First Message

```text
Independently verify the latest pushed GitHub state and canonical portable-v2
source. Read AGENTS.md, docs/PIPELINE.md, docs/SCIENTIFIC_STATUS.md, and the
relevant tasks/<dataset>/STATE.md plus CHATGPT_CONTEXT.md. Treat bootstrap text
as a cache, separate VERIFIED/INFERENCE/UNVERIFIED, and do not authorize or run
science unless I explicitly request it.
```
