# ALFWorld Track R Corrective Execution State

- Status: `ACTIVE_TRAIN_CONFIRMED_ACTION_BOUNDARY_CORRECTION_PREREGISTERED`.
- A pre-rerun TRAIN-only semantic audit found a second structural defect:
  all 588 existing sanity/smoke first-line actions retained a leading ReAct
  transcript marker `>`, all 588 environment responses were `Nothing
  happens.`, and no state/reward/done/won progression was observed. A same-reset
  TRAIN replay proved that removing exactly one marker converts a rejected
  command into the corresponding admissible TextWorld command. The correction
  rule and its required versioned Harness lock are preregistered in
  `research/plans/ALFWORLD_TRACK_R_ACTION_BOUNDARY_CORRECTION_PREREGISTRATION.md`.
- No corrective complete evaluation has started. The order-only corrective
  UUIDs will not be reused after the action-extraction identity changes.
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
- Final executable/scientific source SHA:
  `e9c2f3faf59fec243589eb5e9ba8424067fa677e`.
- Source archive ref:
  `archive/rcmf-alfworld-track-r-full-134-e9c2f3f`.
- Harness Track R authority: branch
  `dataset/alfworld-track-r-execution-lock-v1`, source
  `4fa9274eaf55d066eb85bf828f76cea4f40c3dbb`, archive
  `archive/alfworld-track-r-execution-lock-v1-4fa9274`, lock identity
  `055e5364fefd088f0ad74106acca53231fce4d0dbd5cbfbb854a8822efd344de`.
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
- Frozen prompt/chat/generation identities: prompt SHA-256
  `a10976b4ae99f4802aa9e621933bb71065ae103f2bd273a24466ab1005fbc45a`,
  chat-template SHA-256
  `a55ee1b1660128b7098723e0abcd92caa0788061051c62d51cbe87d9cf1974d8`,
  generation identity
  `6f5df9b7560265a34a90985c7d15c633fb5f3085cc421bd7bc27cb74cc7fd8d9`.
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
  `research/plans/ALFWORLD_TRACK_R_ORDER_CORRECTION_PREREGISTRATION.md`.

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
