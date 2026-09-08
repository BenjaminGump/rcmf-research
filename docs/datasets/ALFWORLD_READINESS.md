# ALFWorld Readiness

Status: engineering plan only; no ALFWorld RCMF result exists.

Primary code source is `alfworld/alfworld` pinned at
`aaba6870f86c5be6a08a491f32a50b906227bc3e` (MIT). The deployed package and
downloaded data version are not yet independently sealed; a repository commit
must not be treated as a package/data identity.

Official data include train, validation-seen, validation-unseen games,
`traj_data.json`, human annotations, high-level expert PDDL plans, low-level
expert actions, TextWorld game files, and planner/hand-coded expert support.
The ALFRED/THOR low-level plan is source provenance, not automatically the
text-command sequence visible to RCMF.

## Planned Provider

`ALFWorldOfficialExpertTrajectoryProvider`, provenance `OFFICIAL_EXPERT`:

1. select official training games only;
2. load each exact TextWorld game;
3. invoke the official planner or a validated official expert;
4. execute actions in the same textual deployment interface;
5. record each command and exact returned observation;
6. verify official task success;
7. emit complete portable transitions;
8. bind each row back to `traj_data.json`, game file, plan, package, and data
   hashes;
9. reject nonreproducible rows with typed reasons.

Train trajectories alone may construct memory/training. Validation seen and
unseen are evaluation-only unless a later preregistered contract says
otherwise. Generated PDDL-expert additions, if needed, must use a separately
declared provenance class and cannot silently merge with official annotations.

The prompt candidate is exact ReAct
`react_task_type_two_demo_v1`; see `docs/PROMPT_SOURCES.md`. Task-family
selection is adapter-owned. The next bounded task is environment/data version
inspection and replay of at most a few training games across task families,
with no Qwen generation or training. If the environment is absent, install it
in the dataset worktree/environment only after reviewing data size/licensing.

Unresolved: deployed package/data hashes, exact game inventory, expert API,
TextWorld command/observation normalization, stable split/lineage manifest,
tokenizer counts, reset/replay determinism, and runtime estimate. A read-only
Lambda inspection at `2026-09-08T09:43:34Z` found no importable `alfworld`
package in `/home/ubuntu/venvs/rcmf-py311`; no installation was attempted.
