# EXP-024R2 JWT Semantic-Normalization Specification

`appworld_observation_semantic_normalization_6h2_v1` first applies locked v1,
then handles only valid JWT pairs in explicitly named `access_token` fields.
It preserves header algorithm/type, all stable claims, temporal-claim
presence, and non-token fields. Only `exp` values and consequent signatures
may differ.

The 11 immutable sentinel pairs all match on header and stable claims. Both
expected and actual tokens validate through installed AppWorld 0.1.0, and all
actual tokens support subsequent recorded authenticated calls. Ten `exp`
deltas are 191 seconds and one is 834 seconds. Non-temporal mismatches are
zero. Adversarial tests reject stable-claim, token-type, permission, response,
malformed-token, and external-timestamp changes.
