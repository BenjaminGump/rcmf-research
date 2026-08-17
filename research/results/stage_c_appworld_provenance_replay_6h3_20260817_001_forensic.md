# EXP-024R3 b0a8eae_2 Forensics

The source query is identical across the raw trajectory, decisions, manifests,
and replay contract. Its supervisor fields differ from both agreeing official
0.1.0 snapshots. Recorded actions and observations contain five official-
identity references, zero source-query-identity references, and no mixed step.

Classification: `source_query_header_only_corruption`.

One task spec matches the source-query identity, but it is behaviorally
incoherent with the trajectory and is not an exact historical snapshot.
