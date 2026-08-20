# EXP-025D-Fast Runtime Record

The bounded manifest has A/B/C/D/E `128/24/24/24/32`, or `232` logical and
`224` unique scoreable pairs. There are no over-context rows or truncations.

Preflight estimates:

| Scenario | H100 hours |
| --- | ---: |
| Best | 4.7635 |
| Expected | 5.6761 |
| Conservative | 14.7903 |

Expected work was below the 12-H100-hour review threshold, so automatic launch
was permitted unchanged. The 180-condition one-step component remained
conditional and was never launched because the tensor gate failed.

Actual GPU-attempt duration was `28,777.03 s` (`7.9936` H100 hours), with
`29,807.58 s` (`8.2799 h`) from first GPU start to final scientific stop.
Final artifact size before CPU finalization was `5,650,447,990` bytes. The
larger-than-projected storage is primarily resumable Qwen teacher/target rows,
optimizer checkpoints, and retained failed-attempt provenance.

Resume points preserve exact optimizer and update state. The final primary
checkpoint is u64 over all 224 pairs; the repeat checkpoint is u16 over all 16
stability pairs. All nine attempts are closed after CPU finalization.

