# EXP-036C Structured Handoff

## Status

- Milestone: EXP-036C Authorized Test-Normal Execution
- Status: `completed_evaluation_only`
- Run UUID: `rcmf_appworld_testnormal_final_13c_20260901_002`
- Branch: `research/v5-rcmf-appworld-testnormal-execution`
- Starting SHA: `97a5965fdf1e8cc4992b3df818966736ea0c159e`
- Frozen-manifest commit: `88fd34e6b71167083a4b17244d3e332dcdcf2cc8`
- Complete formal execution commit: `591a435`
- Analysis adapters: `02b4215`
- TTFT profiling repair: `10610ac`
- Canonical reversibility repair: `52692aac3488dc6907f2d39184a5789c6bdee0c4`
- Efficiency provenance repair: `b99a87f`
- Global seed and `PYTHONHASHSEED`: `25101`

The complete frozen evaluation finished: 168 tasks, five conditions, 840/840
trajectories, zero infrastructure exceptions, zero optimizer steps, and zero
parameter updates.

## Immutable Identities

- Config: `a178b6c203e329d43cf2a976f7f8261ce93abd70b8123f1c00a965202e805201`
- Test task-list SHA: `990c25609f0777893feec8a72385c0457e5e19f0c17c575159ff263dbe809e83`
- Test manifest: `c76b939420f76596dc7a9a45f8cb5dc9f8b60b05b9d1465b57160d3121cdbde8`
- Prompt initial messages: `90c375658628663fbe5b5110e8efc619b2edab229a6d9a64d4e253d2e559ddbe`
- Retained demo: `32348a5889682499b1cc17b7dced74dd706db12b6e248c1e6c7dfba5e50ed713`
- BEST selector/writer-reader/field:
  `c7ca61bb67e3862204ca38a7c3d9cba432b4d6cdadf42bb01255a9e623956611f`,
  `d11e9d8ea28348148dd8919144c64ea69dbf187864a0e42fbdcc69f32241a5f1`,
  `5fe48fc206c592fdbe899a2b4923b4eccc950210fd4a843de681c77c573e0b5e`.
- FULL1D selector/writer-reader/field:
  `c6e4e2dd533a593730550d2580054da4fc2ac701cefd0d2def1c4a771b4d6300`,
  `357491a6c69d141e4ed476b9810a3c8d11bb29ec27e80491db69355b4956d764`,
  `f7fb2f873425cb3792a12dd84bda0d6d1008061f8235d95df687a78dd2cab169`.
- Shuffle: `4e5a4d8551223c420b063b0d8043a966367ac7043a53891ff7723616b7aa2170`.
- Determinism mode: `hash_seed_only`; logical SHA
  `d43d46e46ce8beef7c2b5946c1657b84e94d4a0783b4180f45186beedfc9eb5c`.
- AppWorld: `0.1.0`; root
  `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0/root`.

## Scientific Results

| Condition | Success |
| --- | ---: |
| B0 | 44/168 |
| BEST-C | 48/168 |
| BEST-S | 42/168 |
| FULL1D-C | 40/168 |
| FULL1D-S | 48/168 |

Primary paired contrasts:

- BEST-C minus B0: +4 tasks / +0.02381; bootstrap 95% CI
  `[-0.04167, 0.08929]`; 17 wins, 13 losses; exact McNemar `p=0.58466`;
  LOO `[0.01796, 0.02994]`.
- BEST-C minus BEST-S: +6 / +0.03571; CI
  `[-0.01786, 0.08929]`; 14 wins, 8 losses; `p=0.28628`; LOO
  `[0.02994, 0.04192]`.
- FULL1D-C minus B0: -4 / -0.02381; CI
  `[-0.08929, 0.04167]`; 13 wins, 17 losses; `p=0.58466`.
- FULL1D-C minus FULL1D-S: -8 / -0.04762; CI
  `[-0.11310, 0.01786]`; 12 wins, 20 losses; `p=0.21533`.

All intervals include zero. LOO directions are stable. Gains and losses are
spread across task families rather than dominated by one family. The complete
success IDs and paired sets are in `paired_analysis.json`.

Interpretation:

- VERIFIED: BEST has positive absolute and matched-shuffle point estimates.
- VERIFIED: FULL1D is below bare and below its matched shuffle.
- VERIFIED: no paired interval excludes zero.
- INFERENCE: BEST is the stronger paper-facing development configuration.
- UNVERIFIED: untouched-test generalization or a definitive framework verdict.

## Runtime And Efficiency

- Formal task wall sum: `29.5635h`.
- Measured formal model GPU-active time: `27.2482h`.
- Post-run GPU-session work: approximately `0.433h`.
- Ledger wall span through reversibility: `35.4802h`.
- Formal steps: 24,651.
- Formal prompt/generated tokens: 217,968,877 / 2,320,495.
- Infrastructure execution exceptions: 0.

The active field is fixed at `A=[960,8,256]`, `B=[8,256]`, 7,872,512 bytes.
At N=499, compiled reads take about 0.150 ms versus about 30.29 ms for an
explicit per-memory sum. Raw ledger and deletion/provenance state remain
linear archival storage.

TTFT token-equivalence passed. Formal efficiency and serving identities are
`09d10ef7fe6cb51408af7c378dce69ede856728a9a53e9dce3fb7f7ece35eeeb`
and `3b41f0e2cbcc9ced8dc9d50565a99b59fb619ddf35d9f9f66dac4b551a757838`.

## Reversibility And Provenance

The canonical-cache 499-record audit passed:

- audit rebuild maximum error: `3.814697e-6`;
- worst insertion-order error: `5.245209e-6`;
- maximum per-record remove/restore error: `2.384186e-7`;
- median remove/restore: `0.0790/0.0389 ms`;
- result SHA: `e081be3b7996f5f5032a4b4ccaa455cdf5ec3a079f66f329571e49ca7bddcc97`.

The raw compilation result's aggregate cache-equivalence boolean was
hard-coded true, although its per-record rows show 0/499 matches at `1e-5`.
Commit `b99a87f` fixes future aggregation and the append-only correction record
has logical SHA `97a87cf0c19cbb90facb3398afc7dd8818416012d8de2219c89f963e5885dea6`.
The immutable original result was not rewritten. This has no effect on frozen
fields, behavioral rows, or compilation timing.

## Attempts And Deviations

- Official `_002` ledger: 14 attempt IDs, 28 events, two failed, zero open.
- Failed: `exp036c-efficiency-read-001` (missing prompt manifest) and
  `exp036c-efficiency-pilot-001` (`cache_position` hook assumption).
- Successful replacements: `exp036c-efficiency-read-002` and
  `exp036c-efficiency-ttft-001` after the source repair.
- The requested `_001` root is preserved after a preparation-only metadata
  mismatch; it contains no formal trajectory.
- One shell-quoting formal finalizer error and one raw-reencoding
  reversibility preflight failure occurred outside the ledger. Both were
  superseded by identity-preserving runs and are disclosed in the report.

## Tests

- Focused post-correction tests: 26 passed locally.
- Final local full suite: 789 passed, 2 skipped.
- Final Lambda full suite: 791 passed.

## Artifacts

- Raw Lambda root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/rcmf_appworld_testnormal_final_13c_20260901_002`
- Report: `research/results/EXP_036C_APPWORLD_TESTNORMAL_FINAL.md`
- Machine results: `research/results/exp036c_appworld_testnormal_final/`
- Audit index:
  `research/audits/rcmf_appworld_testnormal_final_13c_20260901_002/index.json`
- Audit index SHA256:
  `682e5e7736706843b07da76e6df59716162de3dec17367a0934d3a05dca564db`.
- Git-safe audit: 1,010 exported files, 1,762,201,416 bytes; strict scan passed
  over 840 JSONL traces, 168 text comparisons, and two JSON assets.
- Audit traces:
  `research/audits/rcmf_appworld_testnormal_final_13c_20260901_002/formal/`

No follow-on experiment, training, calibration, portability study, or paper
automation was started. Lambda was not terminated.
