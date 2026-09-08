# Pipeline Failure Modes

| Failure family | Symptom and cause | Affected evidence | Repair/prevention | Regression evidence |
|---|---|---|---|---|
| RNG device restoration | Resume crashes because `torch.load(map_location=cuda)` maps CPU RNG bytes to CUDA | 14h D10 | Canonicalize CPU and CUDA RNG states to CPU `uint8`; validate and restore, never ignore | R9 cross-process equivalence and actual D09 one-unit smoke |
| Stage-manifest identity | Executable exits 0 but strict completion rejects missing run UUID/root/config/contract | 14g S00 | One verified identity payload feeds success and failure manifests | R7 real S00-S04 and all-stage round trips |
| Stale historical counts | One-demo O08 expects three-demo `366/98` instead of sealed `324/83` | 14k O08 | Derived populations come from strict upstream outcomes; historical values stay reproduction-only | R14B count-ownership diagnostics |
| Prompt-profile propagation | 1D O06 silently receives legacy `full_demo` | 14j O06 | Arm profile explicitly overrides only replay prompt profile; effective config/hash sealed | R12A/R12B six-state and full isolated O06 validation |
| Parent/local path ownership | Continuation O13 assumes local O00 state cache although config names parent-owned cache | 14l O13 | Resolve logical input through explicit owner/path/hash; never make a missing local copy appear | R16 diagnostic and later formal 14n completion |
| Schema-version dispatch | New continuation version falls into full-run compatibility path | pre-science 14m C00 | Dispatch on validated semantic continuation mode, not literal version suffix | R18 C00/C01 production path and 14n |
| Checkpoint pointer | Pointer path exists but recorded SHA/epoch boundary is stale or wrong | R10 audit lead | Verify pointer SHA and checkpoint SHA before deserialize; distinguish intermediate from epoch boundary | checkpoint lifecycle and mutation tests |
| Wrong-run monitoring | Old terminal run is reported as current continuation | 14k/14l monitoring transition | Monitor binds exact UUID/root/source and labels stale snapshots | monitor identity checks and task-specific bridge roots |
| Attempt-ID collision | Retry/result paths may collide or overwrite prior attempt evidence | pre-R10 infrastructure | Run-bound unique IDs and append-only attempt ledger | scheduler retry/collision tests |
| Context preflight/runtime mismatch | Static scoreable state fails live context because profile/count renderer differs | 14j O06 forensic | Same arm-resolved renderer and runtime-equivalent tokenization; profile provenance checked before generation | R12A 2x2 diagnosis; R12B 499-state audit |
| Portable record closure gap | Malformed records survive because `None`, wrong versions, duplicate IDs, or cross-split lineage are accepted | V2.0 adaptation preflight | V2.1 exact typed schemas and whole-manifest task/trajectory/transition/state closure | strict positive/negative schema tests |
| Capability self-declaration | Adapter claims runtime/token support without a factory or exact counter | V2.0 adaptation preflight | Derive capabilities from selected phases and exercise bounded probes before model load | V2.1 capability prerequisite/probe tests |
| Placeholder phase success | A runner can write a nominal pass without executing bounded work | V2.0 executor boundary | Bind an adapter-owned executor and require nonempty work/output with strict identity and hashes | V2.1 executor manifest/no-op rejection tests |
| Producer-owned terminal count | Checkpoint repeats its own expected unit count and self-certifies completion | V2.0 checkpoint policy | Derive expected units from a strict upstream manifest and validate pointer/content hashes before load | V2.1 checkpoint ownership/mutation tests |
| Literal release-gate zero | Unevaluated property is displayed as machine-verified zero | V2.0 release packet | Label each gate machine-computed, test-bound, manual, or not evaluated and resolve evidence paths | V2.1 dynamic release validator tests |

General prevention: downstream values and paths must flow from strict typed
upstream manifests; no directory search, guessed owner, implicit dataset
fallback, or existence-only completion. A scientific/source bug invalidates the
authorization; preserve the root and freeze a new source rather than patching
in place.
