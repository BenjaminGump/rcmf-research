# ALFWorld Track R Action-Dialect Corrective State

- Status: `ACTION_DIALECT_V3_FIRST30_PASS_FULL134_BARE_AUTHORIZED_RCMF_C_BLOCKED`.
- Final executable source for this corrective stage is
  `37e3ad1c2fd0c3bbcd54da3c64b5613edff29094`, archived at
  `archive/rcmf-alfworld-action-dialect-correction-37e3ad1`. Harness source
  `ef31ccba6092e9b7dc9f0a9c5f5310c6e20ccc74` is archived at
  `archive/alfworld-action-dialect-correction-ef31ccb`.
- The v3 lock file/portable identities are `8917fa5356...` / `f6103813cd...`;
  generation/action identity is `80400891d4...`; bridge identity is
  `react_put_in_on_to_alfworld_move_to_v1`.
- The actual RCMF adapter TRAIN probe passed all five placement families:
  exactly five bridge activations, exact five official `move` commands,
  observations, rewards, done/won values, and 5/5 official terminal success.
  The Harness-owned independent probe passed the same fixed TRAIN cases.
- The fresh real 134-task CPU closure passed correct set/order, wrong-order
  rejection, reverse-input restoration to `c49e3fab...`, correct embedded-v3
  identity acceptance, and wrong order/lock/generation/bridge/executed-action
  rejection.
- The final local suite at source `37e3ad1` passed 1,120 with three skips;
  Lambda focused tests passed 31. Historical counts remain explicitly distinct:
  1,111 before the order repair, 1,113 at `12d4b1a`, and 1,116 at `91598b6`.
- The exact first-30 bare run UUID
  `de0e3118-e852-4709-8887-304620c27cc6` completed 30/30 in 2,260.963159
  seconds. Audit UUID `0d41c5b9-921f-41dd-a464-657afd7595da` checked 1,000
  steps and passed with zero violations, zero typed failures, nine bridge
  activations, and zero matched `put` sent literally. Official outcomes
  improved from the fixed v2 reference 13/30 and 0/12 placement to 24/30 and
  9/12 placement. Decision: `READY_FOR_CORRECTED_FULL_134_BARE`.
- The next authorized model run is the already-preregistered complete bare UUID
  `dcc9d31d-e8f1-4d29-9eb4-a675b18eeb47`; its full structural audit must pass
  before RCMF-C UUID `fbef9769-9b27-4f0f-a9cd-c8d8fbe5ce23` may start.
- The action-boundary-v2 bare arm UUID
  `6f47ae2c-572d-45d7-95aa-218707b3d919` completed 134/134 with 16 official
  successes and zero typed failures. Its immutable episodes SHA-256 is
  `cd84d190...`; its complete CPU audit SHA-256 is `a8988af2...`.
- The audit reconstructed all 6,215 steps and found no first-line, one-marker,
  environment-step, order, embedded-identity, or done/won/evaluator error. It
  found a separate foundational protocol mismatch: all 549 model actions using
  the upstream ReAct `put ... in/on ...` form returned `Nothing happens.`.
- Independent pinned-source review proves that ReAct commit `6bdb3a1...`
  teaches and directly submits `put ... in/on ...`, while ALFWorld commit
  `aaba687...` accepts `move ... to ...`. Same-state fresh-reset TRAIN probes
  across all five placement families confirmed `put` inert in 5/5 and official
  `move` exact and terminal-successful in 5/5. Evidence:
  `research/results/alfworld/bare_postrun_audit_action_v2.json` and
  `research/results/alfworld/react_put_move_train_probe.json`.
- The v2 bare output is preserved as `INVALID_ACTION_DIALECT_MISMATCH`. The
  preregistered RCMF-C UUID remains not started and is superseded before
  execution. Do not run it.
- The prospective repair translates only the exact anchored upstream lower-case
  `put <object> <id> in/on <receptacle> <id>` form to official
  `move <object> <id> to <receptacle> <id>` at the method-neutral environment
  boundary. Prompt bytes, raw model output, normalized model action, model/
  checkpoint, task set/order, and evaluator remain unchanged; native `move`
  passes unchanged. See
  `research/plans/ALFWORLD_TRACK_R_ACTION_DIALECT_CORRECTION_PREREGISTRATION.md`.
- After source and TRAIN/CPU gates, the next model retest is bare on only the
  first 30 frozen-order tasks. The fixed v2 reference is 13/30 overall,
  13/18 look, and 0/12 placement. A full 134 bare run is allowed only after the
  preregistered structural checks plus >13/30 total and >=1/12 placement
  success. RCMF-C remains blocked until that later 134 bare audit also passes.
- No unaffected model/checkpoint retraining, valid-unseen tuning, prompt/model/
  generation search, task substitution, RCMF-C launch, or WebShop mutation is
  authorized.

---

# Historical Action-Boundary-v2 Corrective Execution State

- Status: `ACTIVE_ACTION_BOUNDARY_V2_FORMAL_BARE_RUNNING_POSTRUN_AUDIT_REQUIRED`.
- The corrective formal bare arm UUID
  `6f47ae2c-572d-45d7-95aa-218707b3d919` started on Lambda at
  `2026-09-11T03:14:15Z` under source `91598b6`, the frozen v2 lock, and the
  complete 134-task manifest order. At `2026-09-11T07:15:38Z`, 84 rows were
  durably present and the process remained active. Thirteen official successes
  were already present in the first 18 rows, so the active arm cannot finish
  all-wrong; this partial fact does not authorize scientific interpretation.
- A user-directed post-bare safety gate was added while that arm was running.
  RCMF-C must not launch automatically when bare finishes. First audit all 134
  rows from model input and raw output through normalized/executed command,
  environment observation and real state progression, to done/won/evaluator.
  An all-wrong arm or a clear basic interaction/parsing/evaluator fault blocks
  RCMF-C. If repair is required, diagnose on TRAIN when possible and run only
  the first 30 tasks in the unchanged frozen manifest order on the next bare
  retest; complete 134 and consider RCMF-C only after the diagnosed fault is
  repaired and the retest improves. This adds no unspecified score threshold
  and authorizes no prompt/model/config search or retraining. See
  `research/plans/alfworld_bare_postrun_safety_gate.json`.
- A pre-rerun TRAIN-only semantic audit found a second structural defect:
  all 588 existing sanity/smoke first-line actions retained a leading ReAct
  transcript marker `>`, all 588 environment responses were `Nothing
  happens.`, and no state/reward/done/won progression was observed. A same-reset
  TRAIN replay proved that removing exactly one marker converts a rejected
  command into the corresponding admissible TextWorld command. The correction
  rule and its versioned Harness lock are preregistered and implemented in
  `research/plans/ALFWORLD_TRACK_R_ACTION_BOUNDARY_CORRECTION_PREREGISTRATION.md`.
- Harness v2 execution-lock identity
  `5cb89b2d602162044daf8bd14c03b822f405086b09bf77de8c06c2b09ae9a72f`
  and RCMF source `91598b6d734cc5a5b3d1ccc1d21a7f2fb1bb2741`
  freeze the exact one-marker action-boundary correction. All old locks,
  results, and source archives remain preserved.
- The complete real 134-task CPU closure passes: correct frozen order is
  accepted, the same set in a wrong order is rejected, reversed adapter input
  is restored exactly to `c49e3fab...`, and wrong embedded order, lock-file,
  or lock-identity claims are rejected.
- Corrected matched TRAIN and full-arm identities are preregistered in
  `research/plans/alfworld_track_r_run_identities_v2.json`. The corrected bare
  full evaluation is currently running; RCMF-C has not started. WebShop
  explicitly returned an empty H100 and
  both matched six-family TRAIN sanity runs completed. Across both conditions,
  all 588 steps applied the exact v2 boundary, no executed action retained the
  marker, and each family produced substantive environment observations. The
  structural gate passes; the current step is completion and mandatory
  post-run audit of the 134-task bare arm. Only after that audit may the
  complete RCMF arm begin under the same frozen settings.
- The order-only corrective
  UUIDs are recorded as `NOT_STARTED_SUPERSEDED_BEFORE_EXECUTION` and will not
  be reused after the action-extraction identity change.
- Blocking defect: the first complete bare and RCMF attempts ran the exact 134
  tasks in sorted task-ID order (SHA-256 `2410f2c2...`) instead of the frozen
  manifest order (SHA-256 `c49e3fab...`). Both attempts and their paired
  analysis are preserved as `INVALID_EXECUTION_ORDER_MISMATCH`; they are not a
  scientific result. The frozen task set, prompt, model, generation, evaluator,
  checkpoint, and outcome firewall remain unchanged.
- Effective UTC date: `2026-09-10`.
- Branch: `adapt/alfworld-v1`.
- Development base records SHA:
  `3560ec75f96575ce87fd06a8eb5c4b4d596615c8`.
- Charter update SHA:
  `154fd80748b977811d473ff3aa0990bad951283a`.
- Preregistration SHA:
  `efe9f8a0e9002c852804faabb303f45ef14fc876`.
- Invalid-attempt executable/scientific source SHA:
  `e9c2f3faf59fec243589eb5e9ba8424067fa677e`.
- Source archive ref:
  `archive/rcmf-alfworld-track-r-full-134-e9c2f3f`.
- Order-repair source/archive:
  `12d4b1ac0ff8d1afb374010dc1a65404ef7ff269` /
  `archive/rcmf-alfworld-track-r-order-correction-12d4b1a`.
- Action-boundary preregistration/source/archive:
  `87337b28cfb16bd585c11d31d44ff87ebadf9e01` /
  `91598b6d734cc5a5b3d1ccc1d21a7f2fb1bb2741` /
  `archive/rcmf-alfworld-action-boundary-correction-91598b6`.
- Harness Track R v2 authority: branch
  `dataset/alfworld-track-r-execution-lock-v1`, source
  `503493f28b7448396c677fa976c9ab3b4be6a36e`, archive
  `archive/alfworld-track-r-action-boundary-correction-503493f`, lock identity
  `5cb89b2d602162044daf8bd14c03b822f405086b09bf77de8c06c2b09ae9a72f`.
- Endpoint: exact Track R
  `alfworld_upstream_react_valid_unseen_reference_v1`, role
  `UPSTREAM_PROTOCOL_REFERENCE`, all 134 official `valid_unseen` tasks.
- Task-set SHA-256:
  `2410f2c2a92346d63bc00e403a51122c8123d3978c3c29f8c86864556ead5534`.
- Invalid first bare attempt: `0/134`; completed 134/134 with zero typed
  failures, but under the wrong order identity.
- Invalid first RCMF attempt: `0/134`; completed 134/134 with zero typed
  failures, but under the wrong order identity.
- Invalid paired diagnostic: absolute delta `0.0`; gains `0`, losses `0`, both correct `0`,
  both wrong `134`; 100,000-replicate paired bootstrap 95% CI `[0.0, 0.0]`
  at seed 25,101; exact two-sided McNemar `p=1.0` with zero discordant pairs.
- Interpretation: no scientific conclusion is permitted from these invalid
  attempts. The order defect is outcome-independent and requires both arms to
  be rerun in the originally frozen manifest order.
- Frozen model/tokenizer: `Qwen/Qwen3-8B` revision
  `b968826d9c46dd6066d109eabc6255188de91218`, bfloat16, frozen backbone,
  thinking disabled.
- Frozen prompt/chat/corrected-generation identities: prompt SHA-256
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a`,
  chat-template SHA-256
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`,
  generation identity
  `c86fad4fad8e7fc562a130d72fb58b96b18b7099152293bf776f057ebff5c59f`.
- Evaluator: `alfworld_official_terminal_won_binary_v1`, success iff
  `environment_done=true AND state.won=true`; partial reward is not success.
- Main Lambda run UUID/root:
  `f8c16300-c5ae-4422-b40b-eadb932ed6ab` /
  `/lambda/nfs/rcmf-persist/project/runs/alfworld/f8c16300-c5ae-4422-b40b-eadb932ed6ab`.
- TRAIN corpus: 3,553 official training games attempted; 3,545 successful
  replay-validated `OFFICIAL_EXPERT` trajectories admitted, eight typed reset
  timeouts excluded, and 21,259 complete transition memories admitted. Corpus
  SHA-256 `35aa8f20e0cb8571d23da9c61f2efc0e93ee7a84bd8385742fd537c78eac795d`;
  ledger SHA-256
  `2fe6efb1ad7b76695460202515684c6f83c15235b4430717c47f4eb86f8f213d`.
- Training: seed 25,101; one writer epoch and one reader epoch; all 21,259
  memories; `terminal_completed_epoch`; elapsed 42.425295 seconds. Final
  checkpoint SHA-256
  `6e03514d5014702b995a366bcf92c093d050b4a1b2c74cf7b97effc82f30a4bd`.
- Invariants passed: independent feed-forward writes, finite contributions,
  float64 add/remove/restore max error `3.469446951953614e-18`, fixed field
  `A[256,128] + B[128]`, fixed read `[1,128]`, Qwen frozen, no deployment
  per-memory state, no runtime retrieval, no raw-memory prompt, and TRAIN-only
  ledger closure.
- Formal bare UUID/runtime:
  `9b9fcadb-a03c-49b1-9a94-1a5cb2c1068a` / 24,480.148056 seconds.
- Formal RCMF UUID/runtime:
  `b9e9f74d-196a-4797-953c-94211ce2907b` / 24,974.68115 seconds.
- No single scientific run exceeded or plausibly exceeded 18 hours after the
  measured sanity extrapolation; both formal arms completed in under 7 hours.
- The first P00-P11 record is invalidated transitively by the execution-order
  defect and must be regenerated after corrected paired analysis.
- No task was removed or substituted; no `valid_unseen` outcome influenced
  method/config selection; no score selected a checkpoint; no post-outcome
  tuning occurred.
- Lambda worktree was clean at executable source and the final GPU process
  inventory was empty before atomic H100 handoff to WebShop.
- Corrective run identities and rules are preregistered in
  `research/plans/ALFWORLD_TRACK_R_ACTION_BOUNDARY_CORRECTION_PREREGISTRATION.md`
  and `research/plans/alfworld_track_r_run_identities_v2.json`.
- Published structural evidence:
  `research/results/alfworld/train_sanity_action_boundary_audit.json`,
  `research/results/alfworld/real_track_r_order_closure.json`, and
  `research/results/alfworld/order_correction_test_report_12d4b1a.json`.
- Corrected matched TRAIN sanity evidence:
  `research/results/alfworld/action_v2_matched_train_sanity.json`; bare/RCMF
  completed 6/6 tasks and 294 steps each with zero typed failures. Non-think
  substantive observations were 112/145 and 114/149, respectively. Both arms
  recorded 0/6 terminal successes; this is not a defect threshold and did not
  change any setting.
- Test-source distinction: old executable source `e9c2f3f` passed 1,111 tests
  with three skipped; order-repair source `12d4b1a` passed 1,113 with three
  skipped; action-boundary source `91598b6` passed 1,116 with three skipped.

---

# Historical ALFWorld State Before 2026-09-10 Scope Transition

# ALFWorld State

- Development base records SHA: `543de32a91e20796ca6441b65b3a9e41f271c412`
- Canonical executable ancestor SHA: `0ca0101c5ceacc7be3ae42b5d55bb98cd6bd9158`
- Canonical archive ref: `archive/rcmf-portable-canonical-v2_1-0ca0101`
- Bootstrap generated at SHA: `4e56702f467635bda120d118a5367c58e593ecef`
- Last verified UTC: `2026-09-09T09:58:34Z`
- RCMF Harness V1 integration source: `4e56702f467635bda120d118a5367c58e593ecef`
- Integration archive: `archive/rcmf-neutral-harness-v1-integration-4e56702`
- Final Harness source: `827ed6f394804834e93444c9bb02c435e9e238a3`
- Readiness branch/source/records: `dataset/alfworld-readiness-v1` /
  `87cf79d4ee47dfc0f74a799605630f9fbbae0f15` /
  `c7b3ddd2a063554b6c586f62b9f3db305897b63e`
- Readiness decision: `STOP_ALFWORLD_SPLIT_LEAKAGE`
- Future RCMF branch/worktree: `adapt/alfworld-v1`, based on the final RCMF
  Harness V1 integration records branch in a dedicated worktree
- Run namespace: `/lambda/nfs/rcmf-persist/project/runs/alfworld/<uuid>`
- Adapter protocol: `rcmf_reproducible_benchmark_adapter_v2`; no final ALFWorld
  adapter or scientific benchmark lock exists
- Prompt source/profile: ReAct commit
  `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`,
  `react_task_type_two_demo_v1`
- Trajectory source: official training-game `AlfredExpert` planner replay,
  `OFFICIAL_EXPERT`; six bounded training tasks replayed successfully
- Split/evaluation contract: train only for memory/training; `valid_seen` and
  `valid_unseen` evaluation-only; 4,027 unique task identities were inventoried
- Verified: ALFWorld source/data/environment identity, 3,553 train games,
  140 valid-seen and 134 valid-unseen games, six expert replays, two exact
  deterministic repeats, task/split lineage, prompt provenance
- Unverified: an eligible leakage-free base prompt or approved alternate split
  policy, final adapter, runtime token closure, benchmark lock, scientific run
- Blocker: demonstration `pick_two_obj:0` exactly matches evaluation task
  `alfworld:trial_T20190907_201917_045715`
- Next decision: separately approve a provenance-clean base prompt or revised
  evaluation-split policy, then re-audit demonstrations before adapter work
- Latest handoff: `research/handoffs/20260909T095834Z_rcmf_neutral_harness_v1_integration_freeze.md`
- Approval stop: do not alter prompt/split policy, freeze a benchmark lock, or
  run model generation/training without a separate reviewed task
- Scientific status: `NOT_EVALUATED`
