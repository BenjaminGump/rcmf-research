# Matched-Shuffle Anomaly: Deferred Investigation

Status: `DEFERRED_UNTIL_AFTER_2026-09-25_SUBMISSION`.

This document is a restartable preregistration sketch. It does not authorize a
shuffle experiment and is not part of the portable-v2 release gate.

## Sealed Evidence

Formal 14n run:
`rcmf_reproducible_1d_continuation_from_14k_o08_20260907_003`, source
`98f917d03ab4a3e525cab4eb8ef5e4f0e7bf9a9f`. Epoch 1 checkpoint SHA256 is
`c3bc33f38dd0ea58e368c38f582775c355902bb9a01671f21e4e485add9daf2c`.
Formal one-demo correct is `8/57`; matched shuffle is `18/57`. Correct-only
tasks are `6171bbc_2`, `6bdbc26_3`; shuffle-only tasks are `396c5a2_1`,
`396c5a2_2`, `396c5a2_3`, `6bdbc26_1`, `6bdbc26_2`, `6c2c621_2`,
`b119b1f_3`, `d4e9306_2`, `d4e9306_3`, `fac291d_1`, `fac291d_2`,
`fac291d_3`. Exact descriptive McNemar p is `0.012939453125`.

R19 forced epoch-2 diagnostic source is
`2a7f371f378eab42e42629a4fd5275e18b2814ce`; checkpoint SHA256 is
`1db373ba6399aac1adcd6fd721a05e79f3a9acfc39d8a03f2e0a5d8c49ea1457`.
Correct is `16/57`; shuffle is `19/57`. Correct-only tasks are `6171bbc_3`,
`d4e9306_1`, `df61dc5_1`, `fac291d_2`; shuffle-only tasks are `23cf851_2`,
`396c5a2_2`, `57c3486_3`, `6171bbc_2`, `b119b1f_3`, `df61dc5_2`,
`fac291d_3`; both has 12 tasks and neither 34. Exact descriptive McNemar p is
`0.548828125`.

The frozen 499-row permutation SHA256 is
`96f1f3784c73e45bc9036b40aa83f43eca8e03b39995cd6f9fc29cd7790c7db2`.
It is bijective, has zero fixed points, no omitted/duplicated memories, and
preserves memory IDs, keys, rho values, and payload set. The deployment
memory-ID/key SHA256 is
`6cbaf15f3e65ba46cdd1a68116b1641b1a1e9f004427a325f5174793d7f81659`;
payload-set SHA256 is
`709b026f6be873e6ab6b86c0e9dccef8841d90dc6a7db9051c90a9291d0e60ff`.
Epoch-1 field reconstruction and condition/task integrity passed. Epoch-2
correct/shuffle used fresh outputs with the same permutation.

Epoch-2 aggregate trajectory differences: correct/shuffle steps
`1339/1441`, prompt tokens `13,335,717/13,400,740`, completion tokens
`130,078/150,480`, context overflows `8/8`, repeated actions `357/589`,
completion actions `37/33`, execution exceptions `0/0`, and task wall time
`6381.45/7232.86 s`.

## Competing Hypotheses

1. Chance benefit from the single frozen permutation.
2. Harmful task-specific correct key/payload binding.
3. Generic payload-distribution bias that survives shuffling.
4. Trajectory/context interaction, including repeated-action dynamics.
5. A subtler implementation/control defect outside existing identity,
   permutation, field, condition, and task-output audits.

## Future Design

After submission, preregister `K` independent permutations before outcomes,
with `K` and seeds fixed from global seed `25101` using a documented
counter-based derivation. Each permutation must be a bijection over all 499
rows, have recorded SHA/fixed points/cycle structure, preserve keys/rho/payload
sets, and use identical model/prompt/tasks/generation. Never select the best or
worst permutation. Evaluate every permutation on the same ordered 57 tasks and
report all per-task binary rows, aggregate success, correct-vs-permutation
discordances, distribution across permutations, and task-level sensitivity.

Primary uncertainty question: is correct-field performance outside the
predeclared permutation distribution, and is the observed reversal stable?
Decision rules and multiplicity handling must be frozen with the value of `K`.
No single favorable permutation may establish specificity. A minimal planning
estimate is roughly one 57-task condition per permutation at the measured
`~2 h` H100 trajectory time, plus audit/finalization; exact expected and
conservative costs require a new preflight and explicit authorization.

Authoritative records:

- `research/results/EXP_037A_R18_FORMAL_14N_TERMINAL_RESULT.md`
- `research/results/EXP_037A_R19_EPOCH2_SENSITIVITY.md`
- `research/results/exp037a_r19_epoch2_sensitivity/formal_epoch1_shuffle_integrity.json`
- `research/results/exp037a_r19_epoch2_sensitivity/final_result.json`
- `research/audits/exp037a_r19_epoch2_forced_dev_20260908_001/index.json`
- raw roots recorded in those reports

