# EXP-024R2 JWT-Semantic Replay and Identity Provenance Audit

## Outcome

- Run UUID: `appworld_semantic_replay_6h2_20260817_001`
- Starting commit: `ae80e2ba536d6b74a48bc1f658f4e5d6d39f7787`
- Final executable and validator source commit:
  `ad6ce7f110d147f05abac8ce9b1080ea2f151cde`
- Scientific preflight source commit:
  `8577c8ccab090e3f74626c9e21480f7fd9aba9a6`
- Decision: `source_query_task_identity_snapshot_unresolved`
- JWT semantic-contract gate: passed
- All-45 identity gate: failed, `40/45`
- Repeated semantic-v2 sentinel: not run by gate
- Full semantic-v2 replay: not run by gate
- Semantic replay validated: false
- EXP-024A generation remains blocked: true

No Qwen model was imported or run, no raw-memory condition was executed, and
no training or AppWorld task evaluation occurred.

## Authentication Provenance

The immutable AppWorld 0.1.0 capsule was not modified.

- Legacy Python:
  `/home/ubuntu/venvs/appworld-0.1.0-replay-py311-click817/bin/python`
- Package/code/data/evaluation: `0.1.0/0.1.0/0.1.0`
- Generator: `appworld.apps.api_lib.login`
- Generator source SHA256:
  `fad5c75442cef395fdd4e6af85ddfd179a66f5bec6f959ad966c3ca8d18a3c04`
- Validator: `fastapi_login.LoginManager._get_payload`
- fastapi-login source SHA256:
  `9e8fd11183fcce53824ce4e2029559c252e454ee6266496533efddaf5e702726`
- Algorithm: `HS256`
- Subject schema: `<app_name>+<username>`
- Clock: `datetime.now(timezone.utc)`
- Expiration: `random.randrange(600, 1800)` seconds
- Secret provenance SHA256:
  `0917b13a9091915d54b6336f45909539cce452b3661b21f386418a257883b30a`

Only the hash and source provenance of the fixed secret are recorded. No raw
secret, JWT, credential, supervisor value, or personal identity value is in
the Git record. Source inspection found no generated `iat`, `nbf`, or `jti`
claim.

## Semantic Contract

The prospective normalization is versioned separately as
`appworld_observation_semantic_normalization_6h2_v1`. Locked v1 remains
unchanged.

Semantic v2 first performs every v1 transformation. It then handles a value
as a JWT only when both expected and actual values are valid three-segment
JWTs under the explicitly allowed field `access_token`. It retains the header
algorithm/type, all stable claims, temporal-claim presence, and a stable
semantic identity hash. It may omit only the numeric `exp` value and the
consequent signature bytes.

Adversarial tests prove the contract does not hide a changed subject, app,
permission/scope, token type, non-token response field, malformed token,
arbitrary external timestamp, or unapproved dynamic claim.

## JWT Audit

- Sentinel JWT pairs: `11`
- Allowed temporal claims: `exp` only
- Header matches: `11/11`
- Stable-claim matches: `11/11`
- Expected tokens accepted by installed AppWorld validator: `11/11`
- Actual tokens accepted by installed AppWorld validator: `11/11`
- Actual tokens accepted by subsequent recorded authenticated calls: `11/11`
- Non-temporal mismatches: `0`
- Expiration deltas: ten at `191` seconds and one at `834` seconds

This validates the schema-limited JWT equivalence component. It does not by
itself validate replay equivalence for the immutable states.

## Identity Provenance

The all-45 identity-only audit covered all 45 fixed states and all 9 tasks.

- Identity matches: `40/45`
- Identity mismatches: `5/45`
- Mismatch task: `b0a8eae_2`
- Mismatch states: steps `6`, `7`, `12`, `17`, and `18`
- Mismatch fields on every affected state: supervisor first name, last name,
  email, and phone number
- Matching historical task snapshots found: `0`
- EXP-020 manifest coverage: `18/45`; the other 27 fixed one-step states are
  explicitly recorded as absent, not treated as identity failures

For all five states, the decision `state_text`, raw successful trajectory, and
EXP-024R replay contract agree exactly on their complete query hash. Their task
ID and instruction also match the official 0.1.0 task. However, all four
supervisor identity hashes differ from both the reconstructed legacy capsule
and the immutable historical 0.1.0 backup. Those two official task snapshots
agree with each other, and neither matches the source query identity.

The exact audited cause is
`raw_successful_trajectory_query_supervisor_inconsistent_with_official_0_1_0_task_bundle`.
Because no matching immutable snapshot was found, the formal branch is
`source_query_task_identity_snapshot_unresolved`.

## Replay Results

The identity gate requires `45/45` before the fixed sentinel may run. The
observed `40/45` therefore stopped EXP-024R2 before any semantic-v2 action
replay.

### Fixed 13-State Sentinel

| Environment / contract | Identity | Histories | Prior observations | Targets | Complete |
|---|---:|---:|---:|---:|---:|
| AppWorld 0.2.0.dev0, locked v1 | not recorded | 2/13 | 27/102 | 6/13 | 0/13 |
| AppWorld 0.1.0, raw equality | 12/13 | not defined as a gate | 8/102 | 3/13 | not applicable |
| AppWorld 0.1.0, locked v1 | 12/13 | 5/13 | 93/102 | 11/13 | 3/13 |
| AppWorld 0.1.0, semantic v2 repeat 1 | 12/13 identity-only | not run | not run | not run | not run |
| AppWorld 0.1.0, semantic v2 repeat 2 | 12/13 identity-only | not run | not run | not run | not run |

The first three rows are immutable EXP-024A/EXP-024R references. Semantic v2
is prospective and does not retroactively turn locked v1 into a pass.

### Full 45-State Comparison

| Environment / contract | Histories | Prior observations | Targets | Complete |
|---|---:|---:|---:|---:|
| AppWorld 0.2.0.dev0, locked v1 | 2/45 | 81/372 | 23/45 | 0/45 |
| AppWorld 0.1.0, locked v1 | not run after exact sentinel stop | not run | not run | not run |
| AppWorld 0.1.0, semantic v2 | not run after identity stop | not run | not run | not run |

Missing semantic-v2 metrics are gate-controlled `not_run`, not zero.

## Attempts and Recovery

One run UUID and six append-only attempts are preserved:

- `preflight-001`: failed on an incorrect immutable EXP-024R summary key;
- `preflight-002`: failed because a direct JWT validator request included an
  API schema placeholder named `access_token`;
- `preflight-003`: produced and validated `identity_probe.json`, then failed on
  a valid-JWT pair-index mismatch;
- `preflight-004`: reused the validated identity probe, then failed because the
  code assumed all 45 states were present in EXP-020's 18-state held-out
  subset;
- `preflight-005`: completed normally and applied the scientific gate;
- `analysis-001`: completed normally from `preflight_decision.json`.

Every attempt records `scientific_parameter_changed=false`. The failed
attempts occurred before action replay. The validated identity probe was
reused by scientific-request hash; no duplicate probe world or run UUID was
created. Active attempt time was 13.501289 seconds and the end-to-end recovery
span was 1,327.762330 seconds.

## Validation

- Local focused tests: `30 passed`
- Local full suite: `328 passed, 1 skipped`
- Lambda focused tests: `30 passed`
- Postrun validation: `23/23` checks passed
- Qwen imports/forwards/generations: `0/0/0`
- H100 use: `0`
- Artifact size: `341,695` bytes across 30 files

Validator-only fixes after the scientific preflight corrected immutable
parent-manifest list selection and one-shot JSONL generator consumption. They
did not rewrite scientific outputs or change any gate.

## Interpretation

VERIFIED:

- The 11 observed sentinel JWT differences are temporal `exp`-only changes
  under AppWorld 0.1.0's actual generator and validator.
- Semantic v2 is strict enough to preserve all stable identity and response
  fields while accepting those timing-only token changes.
- Five fixed states from `b0a8eae_2` have an unresolved source-query versus
  official-task supervisor identity mismatch.

INFERENCE:

- The source successful trajectory was likely generated from a task snapshot
  or supervisor assignment that is not present in either retained 0.1.0 task
  bundle, or its prompt query captured an inconsistent supervisor identity.

UNVERIFIED:

- The fixed sentinel has not passed semantic v2 because it was not run.
- Full 45-state semantic replay has not been validated.
- EXP-024A generation and raw-transition causal behavior remain untested.

## Artifacts

- Run root:
  `/lambda/nfs/rcmf-persist/project/runs/stage_c/appworld_semantic_replay_6h2_20260817_001`
- Immutable legacy capsule:
  `/lambda/nfs/rcmf-persist/appworld_legacy/0.1.0`
- Auth audit: `appworld_auth_source_audit.json`
- JWT audit: `jwt_stable_claim_audit.json`
- Identity audit: `identity_provenance_audit.json`
- Gate decision: `preflight_decision.json`
- Final summary: `final_exp024r2_summary.json`
- Validation: `postrun_validation.json`
- Attempt ledger: `attempts.jsonl`

Generation, memory-condition execution, procedural field training, behavioral
`p(s,m_transition)`, Stage C2, end-to-end RCMF, and V4 tagging remain blocked.
