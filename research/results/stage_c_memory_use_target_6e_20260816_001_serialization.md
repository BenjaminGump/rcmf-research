# EXP-021 Teacher Serialization Robustness

## Preflight

- Fixed pairs: 192 = 96 A plus 96 D.
- Locked Template-0 rows reused: 192.
- Alternative templates: canonical JSON and compact tagged.
- Planned new forwards: 384; actual scoreable forwards: 381.
- Alternative over-context rows: 3, masked with no utility and no truncation.
- Projected H100 time: `.119120/.161162/.245246 h`
  best/expected/conservative.
- Projected artifact size: `1,560,576` bytes (`1.488 MiB`).
- Resume design: append-only unique pair-template journal, atomic summary,
  hash validation, and skip only validated rows.

## Results

| Templates | Spearman | Pearson | Sign agreement | Mean abs utility change | Mean top-4 overlap |
|---|---:|---:|---:|---:|---:|
| Locked vs JSON | .833893 | not gate | .867188 | .062984 | .965 |
| Locked vs tagged | .946638 | not gate | .961832 | .024012 | .985 |
| JSON vs tagged | .807114 | .896384 | .847222 | .063570 | .950 |

The gate aggregates to median Spearman `.833893`, sign agreement `.892081`,
and mean per-state top-4 overlap `.966667`. Mean utilities are
`.041215/.055956/.045976` for locked/JSON/tagged. Mean token counts are
`22,365.2/24,212.2/22,364.5`; utility change has only `.070247` Pearson
correlation with length change. No template is systematically signed merely
because it is longer.

## Recovery

Attempt 001 completed all durable scoring rows in `586.428 s` and then failed
while aggregating an aliased combined-token field. Attempt 002 validated and
reused all 384 alternative rows in `15.895 s`; it launched zero new Qwen
forwards. Total serialization wall time is `602.323 s` (`.167312 H100 h`).

Decision: the teacher serialization gate passed. The branch
`raw_nll_teacher_serialization_instability` is ruled out for this audit.

