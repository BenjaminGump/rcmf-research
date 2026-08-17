# EXP-024R Sentinel Replay

The fixed 13-state, 9-task sentinel completed under exact AppWorld 0.1.0.

- Initial identity: 12/13.
- Complete prior history: 5/13.
- Prior observations: 93/102.
- Target observations: 11/13.
- Complete replay: 3/13.
- Both no-history states: 2/2 complete replay.
- Decision: `appworld_010_execution_semantics_or_normalization_mismatch`.

All 11 normalized differences are time-dependent login JWTs. The expiration
offsets are ten times 191 seconds and once 834 seconds. A separate source
identity mismatch affects `appworld:trace:b0a8eae_2:step:7:line:284`.
The full replay was blocked exactly as preregistered.
