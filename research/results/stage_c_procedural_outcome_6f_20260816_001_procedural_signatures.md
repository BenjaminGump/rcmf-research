# EXP-022 Procedural Signature Extraction

- Query actions: `638/638` parsed by AST; fallback `0`; dropped `0`.
- Transition actions: `148/148` parsed by AST; fallback `0`; dropped `0`.
- Query target/source trajectory mismatches: `0`.
- Raw credential/value leakage findings: `0` after scanning signature payloads.
- Stable signature cache SHA-256:
  `9f58eeba6b5a2c60f37ebb2b0755d47399cec9678089c3c05e0c1338bba12633`.

The canonicalizer records ordered AppWorld API calls, app/API identity, action
type, keyword names, value-source roles, control flow, pagination,
conditionals, AST calls, and normalized assignment/dataflow. Tests cover
multi-call code, variable reuse, documentation calls, completion, malformed
fallback, stable hashes, and removal of literal credentials. Query state-stage
features are extracted from current task-local history only and report no
future-target access.

The full 148-transition action-type distribution is API documentation 61, API
other 12, authentication 8, Python reasoning 12, read/query 41, and
write/mutation 14. Detailed app/API counts are in the Lambda
`final_exp022_summary.json`.
