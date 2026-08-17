# EXP-024R2 All-45 Identity Provenance Audit

Identity matches for 40/45 states. The five mismatches are all fixed
`b0a8eae_2` states at steps 6, 7, 12, 17, and 18.

For every affected state, decision state text, raw successful trajectory, and
EXP-024R replay contract share one complete-query hash. Task ID and instruction
match AppWorld 0.1.0. All four supervisor identity fields differ from both the
legacy capsule and historical 0.1.0 backup; those official snapshots agree
with each other. No matching immutable task snapshot was found.

Decision: `source_query_task_identity_snapshot_unresolved`. Raw identity values
are retained only in immutable Lambda source artifacts and are absent from the
Git record.
