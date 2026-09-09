# RCMF x Neutral Harness V1 Integration Freeze Handoff

Decision: `RCMF_HARNESS_V1_INTEGRATION_BASE_FROZEN`

## Authority

- Starting RCMF records: `543de32a91e20796ca6441b65b3a9e41f271c412`
- Branch: `integration/rcmf-neutral-harness-v1-final`
- Frozen source: `4e56702f467635bda120d118a5367c58e593ecef`
- Archive: `archive/rcmf-neutral-harness-v1-integration-4e56702`
- Portable V2.1 ancestor: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Final Harness source/records: `827ed6f394804834e93444c9bb02c435e9e238a3` /
  `3c197d15c80c85ad478fc4c502a7b3e9f6aad7cf`
- Final Harness tag/archive: `harness-v1.0.0` /
  `archive/harness-v1-source-827ed6f`
- Records SHA: the pushed records-only descendant containing this handoff;
  use the exact branch HEAD reported in the final task response.

## Result

The existing RCMF plugin was semantically compatible with Final Harness V1.
The only necessary prospective executable change was an exact final-release
lock and mismatch validator. RCMF mathematics and lifecycle behavior did not
change. The lock and real checkouts validate, Final Harness passes 69 tests,
RCMF focused passes 47 locally and on Lambda, RCMF full passes 1089 plus three
skips locally and 1092 on Lambda, and the actual Final Harness runner/result
bundle accepts the RCMF plugin output.

The lock's schema identities use SHA256 of Git blob LF bytes. Windows checkout
CRLF bytes differ, so the validator normalizes CRLF to the release's stated
Git-blob basis. Actual Git objects match all seven release identities.

## Ownership

RCMF owns method checkpoint, field, and reader state. Harness owns benchmark
truth, base prompt, environment, evaluator, task results, semantic
finalization, and comparison. No raw memory enters the shared prompt, no
runtime retrieval exists, and the Harness imports no RCMF implementation.

## Dataset Routing

- ALFWorld remains `STOP_ALFWORLD_SPLIT_LEAKAGE` at readiness records
  `c7b3ddd2a063554b6c586f62b9f3db305897b63e`.
- WebShop remains `STOP_WEBSHOP_DATA_IDENTITY_UNRESOLVED` at readiness records
  `40d6318892e03d7e775ed83704dae89917f7b324`.

No benchmark lock was created. Begin future dataset work from this final
integration records branch, in an isolated worktree, after independently
verifying the source archive and current blocker.

NO RCMF OR BASELINE TRAINING WAS RUN

NO SCIENTIFIC BENCHMARK EVALUATION WAS RUN

NO ALFWORLD OR WEBSHOP BENCHMARK LOCK WAS FROZEN

FORMAL_14N_AND_R19_RESULTS REMAIN UNCHANGED
