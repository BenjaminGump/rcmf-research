# ChatGPT Entrypoint

- Document role: compact project bootstrap cache; not a primary source.
- Development base records SHA: `a3969f56a2020db5dbaed661cab1f0db6acfaee1`.
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`.
- Bootstrap generated at SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Generated-from commit: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Canonical source SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`.
- Last verified UTC: `2026-09-08T17:33:33Z`.
- Latest relevant handoff: `research/handoffs/20260908T173333Z_rcmf_portable_v2_1_m1.md`.

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

Portable canonical V2.1 is an engineering-verified base with strict record
closure, bounded capability proof, dataset/config/executor binding, externally
owned terminal-checkpoint counts, and evidence-derived release gates. A real
bounded AppWorld pilot traversed P00-P11, training, field algebra, Qwen, and the
evaluator. It is not a scientific result. ALFWorld and WebShop have pinned
prompt sources and readiness plans but no scientific RCMF result.

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
