# EXP-024R Package And Data Verification

- `appworld verify tests`: 138/138 passed in 102.060 seconds; log SHA256
  `a30969366eb4f11f23e11b144e5bc42bbcffc2c589a8251f4f33a7a5fa1cd72b`.
- `appworld verify tasks`: 147/147 passed in 118.247 seconds; log SHA256
  `2bf19f5fe2452a12d0dcc85685501fd0a885a037510fc3e9cda2350c98d92dd9`.
- All nine relevant task directories exist in the isolated 0.1.0 root.
- All immutable source trajectories report code/data/evaluation 0.1.0.
- EXP-024R post-run validation passed all checks, including source/config/data
  hashes, unique state keys, attempt start/end pairing, version triple, and
  zero Qwen/memory-condition work.
