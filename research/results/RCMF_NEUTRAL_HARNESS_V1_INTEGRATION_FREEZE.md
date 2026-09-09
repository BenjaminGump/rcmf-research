# RCMF x Neutral Harness V1 Integration Freeze

Decision: `RCMF_HARNESS_V1_INTEGRATION_BASE_FROZEN`

Verified UTC: `2026-09-09T09:58:34Z`

## Frozen Identities

- RCMF branch: `integration/rcmf-neutral-harness-v1-final`
- RCMF integration source: `4e56702f467635bda120d118a5367c58e593ecef`
- RCMF implementation ancestor: `ca93ed71a747c5c1ba0cac3d2659636ca936f092`
- RCMF archive: `archive/rcmf-neutral-harness-v1-integration-4e56702`
- Portable V2.1 source: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Final Harness repository: `BenjaminGump/agent-memory-eval-harness`
- Final Harness source: `827ed6f394804834e93444c9bb02c435e9e238a3`
- Final Harness records: `3c197d15c80c85ad478fc4c502a7b3e9f6aad7cf`
- Final Harness tag/archive: `harness-v1.0.0` /
  `archive/harness-v1-source-827ed6f`
- Lock: `configs/harness/neutral_harness_v1.lock.json`
- Lock Git-blob SHA256:
  `369776954ad164c81856427f434ad14055fd633e3d9489246dc9909d8da7ab9b`
- Lock canonical-document SHA256:
  `928771ceadfd4d6613e4fceb97ea3870a756c6a2ba1f2349be6a65ae4c860468`

The final records SHA is the pushed records-only descendant containing this
report; it is reported in the Git handoff rather than self-embedded here.

## Exact Change

The semantic RCMF plugin at `ca93ed71...` already implements the Final Harness
V1 lifecycle. No writer, field, reader, checkpoint, dependency, executor,
adapter, or scientific change was needed. One prospective integration-lock
layer was required because the prior source did not bind the final Harness
source/release/schema identities or reject mismatches.

The new lock records the immutable Harness source, records, tag, archive,
release branch/manifest, protocol, seven schema Git-blob hashes, RCMF plugin
identity, dependency direction, and compatibility naming debt. The validator
checks exact lock fields, real source/records checkout HEADs, release-manifest
content, and cross-platform LF-normalized schema bytes before use.

## Compatibility

The actual Final Harness `HarnessLifecycleRunner` accepted the RCMF plugin.
Checkpoint and deployment field remained method-owned, two episode resets
executed, the Harness-owned base prompt remained unchanged, runtime retrieval
and raw-memory prompting remained false, and a complete RCMF
`ResultEvidenceBundle` was semantically finalized and comparison-eligible.

Same-run dependency closure, typed `SEALED_UPSTREAM`, executor registry proof,
dataset semantic identity closure, and prospective
`terminal_completed_epoch` remain unchanged. The Harness has no RCMF import or
method-specific generic branch.

## Validation

- Final Harness fresh source checkout and `git fsck --full`: PASS.
- Final Harness full suite: `69/69 PASS`.
- Existing RCMF compatibility plus final-lock tests: `47/47 PASS` local and
  Lambda.
- RCMF local full suite: `1089 passed, 3 skipped` in 237.44 seconds.
- RCMF Lambda full suite: `1092 passed` in 35.62 seconds.
- Exact source/records/release/schema checkout validation: PASS.
- Actual Final Harness runner plus RCMF result-bundle probe: PASS.

The first Lambda full invocation used the remote home as current directory,
so 130 relative-path tests failed before exercising code. The same exact suite
was rerun with process-start worktree ownership through `env -C` and passed
1092/1092. This was an invocation deviation, not a waived failure.

## Dataset Routing

- ALFWorld readiness branch/source/records:
  `dataset/alfworld-readiness-v1` /
  `87cf79d4ee47dfc0f74a799605630f9fbbae0f15` /
  `c7b3ddd2a063554b6c586f62b9f3db305897b63e`; decision
  `STOP_ALFWORLD_SPLIT_LEAKAGE`.
- WebShop readiness branch/source/records:
  `dataset/webshop-readiness-v1` /
  `2072ae59b79171facabb2b580cda0c2ae460cd8e` /
  `40d6318892e03d7e775ed83704dae89917f7b324`; decision
  `STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED`.

No dataset benchmark lock exists. Future dataset branches inherit this
integration records branch, then resolve only their own blocker under a
separate reviewed task.

NO RCMF OR BASELINE TRAINING WAS RUN

NO SCIENTIFIC BENCHMARK EVALUATION WAS RUN

NO ALFWORLD OR WEBSHOP BENCHMARK LOCK WAS FROZEN

FORMAL_14N_AND_R19_RESULTS REMAIN UNCHANGED

