# RCMF Porting Checklist

A new benchmark port must provide and freeze:

1. Successful authoritative training trajectories and their license/provenance.
2. Exact task, state, action, and post-action observation boundaries.
3. Complete-transition extraction with a human-readable raw ledger.
4. Task, episode, replay, and lineage leakage identifiers.
5. Approved train, heldout-selection, and evaluation split manifests.
6. Prompt profiles and byte-stable state rendering.
7. A complete-transition renderer with no arbitrary truncation or subsampling.
8. Optional selector supervision available without evaluation leakage.
9. A same-world action executor and authoritative evaluator.
10. Secret-aware audit redaction that preserves raw hashes.

Then verify:

- the adapter satisfies `ReproducibleBenchmarkAdapter`;
- generic-core import and vocabulary audits pass;
- mock and benchmark adapters traverse the same DAG;
- arm differences remain on the explicit allowlist;
- production add/remove does not scan unrelated memories;
- whole-bank read shape and complexity do not depend on memory count;
- query prompts contain no raw memory text;
- no retrieval, top-k, or runtime per-memory scoring enters the deployment path;
- stage outputs, attempts, and resume identities are content-addressed;
- every complete trajectory has a reconstructible Git-safe audit index.

Migration is confined to one compact adapter, prompt assets, environment
execution/evaluation, leakage definitions, and optional supervision. It is not
merely a prompt replacement. Writer/field/reader mathematics and orchestration
remain unchanged.

Template files:

- `rcmf/pipeline/template_adapter.py`
- `configs/pipeline/benchmark_adapter_template_14b.yaml`

No second benchmark is implemented or run in EXP-037A.
