# EXP-037A-R5 Final Launch Preflight Handoff

Date: 2026-09-03T17:45:38Z

## Decision And Git

- Decision: `READY_FOR_FINAL_RUN_APPROVAL`
- Starting SHA: `c898d86e77eec353ec8f2103ae285dbdbc5bb9a0`
- `LAUNCH_SOURCE_SHA`:
  `31c9e98ab408d4768c83d2a45bb1b21ae565b4be`
- Frozen source branch:
  `archive/exp037a-r5-launch-source-31c9e98`
- Records branch: `research/v6-rcmf-exp037a-final-launch-freeze`
- `RECORDS_SHA`: resolve as the commit containing this handoff. Formal
  execution must check out `LAUNCH_SOURCE_SHA`, not that records commit.

## Frozen Run

- UUID:
  `rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`
- Root:
  `/lambda/nfs/rcmf-persist/project/runs/reproducible_pipeline/rcmf_reproducible_3d_gate_1d_pipeline_14g_20260904_001`
- Config: `configs/pipeline/rcmf_appworld_repro_14g.yaml`
- Config SHA256:
  `1678368f05b717c56f970f3aab9a7da13adbb9c42a45e2076bc7fc1493564b1a`
- Contract SHA256:
  `5c254fb2ccb4cf76c7275451c9bdfc3332962e6d196ed8691cbd3206d6e2b47f`
- Artifact-index SHA256:
  `d8de848d336aa4172d908af278c629512f39da2ddf5aed87f38cce53a5907f46`
- The root was absent before final preflight. It contains preflight only,
  with no stages and no runtime authorization.

## Authorization State

`NOT_AUTHORIZED`. The old 200-hour grant is not inherited. The request cannot
launch and was rejected by the real launcher with exit 1 before any stage.
A later exact grant must bind UUID, root, launch source, config hash, contract
hash, 120-hour cap, authorization version, and full 3D-then-conditional-1D
scope. The proposed 120-hour cap remains unapproved.

## Scientific Contract

- Selector split: SHA256-order, seed 18018, 29 train / 8 heldout parents.
- Legal cells: 310,433.
- Selector seeds: CV 25071; final 25071/25072/25073.
- Panel: 256/499/40.
- Post-D06 gate: 366 train / 98 heldout, outcome-only.
- Prompts: 3D `full_demo`; 1D `full_demo_first_only`.
- Scientific changes from R3: zero.
- Historical artifacts: comparison-only after fresh seal.

## Enforced Gates

- D06B performs exact post-seal 366/98, state-set, status, label,
  over-context, and replay-semantic reproduction checks before D07.
- D07 and D08 accept only fresh upstream artifacts; D08 also enforces S05B,
  499 valid rows, no truncation/subsampling/imputation, and 366/98.
- D08B performs an isolated one-unit writer/reader forward/backward/step;
  discards its parameters; D09 starts from untouched initialization.
- D22 is the only gateway to 1D and requires exact
  `THREE_DEMO_REPRODUCTION_PASS`.
- Resume and child deadline identity are strict and content-addressed.

## Runtime

- 3D-fail branch: 26.75 h expected, 56.5 h conservative, 21.85 H100 h.
- 3D-pass then 1D branch: 47.75 h expected, 92.5 h conservative,
  39.05 H100 h.
- Storage: 46 GiB expected, 90 GiB conservative.
- Cap formula: max(95.5, 115.625) = 115.625 h; rounded proposal 120 h.
- Restart: atomic stage outputs, append-only attempts, exact identity/hash
  validation, resume at first incomplete stage.

## Tests And Smoke

- Local focused: 96 passed.
- Local full: 885 passed, 2 skipped.
- Lambda focused: 96 passed.
- Lambda full: 887 passed.
- Strict Git-safe scan: 0 JWT, 0 bearer-token, and 0 credential matches; 28
  broad-regex matches were verified `stage_c_*` config-path false positives.
- Technical smoke: passed, 46.629611 seconds, no infrastructure exception,
  peak GPU memory 17,903,331,840 bytes.
- Real unapproved-request launch check: rejected with exit 1; no runtime auth
  or stages created.

## Runtime State

At 2026-09-03T17:54:40Z, NFS was mounted, the H100 reported 0% and 0 MiB,
no EXP-037A process or tmux session existed, and only unrelated historical
tmux sessions remained. Lambda was clean on the frozen launch-source archive;
the canonical root had no runtime authorization and no stages.

`SAFE_TO_TERMINATE = YES`: no active work depends on the instance, and all
preflight/diagnostic artifacts are persisted on NFS. The instance was not
terminated.

## Next Exact Action

Do not launch from this handoff. After explicit user approval for this exact
packet, create a distinct authorized grant at
`$ROOT/preflight/explicit_user_authorization.json`, check out
`31c9e98ab408d4768c83d2a45bb1b21ae565b4be`, and invoke:

```bash
PYTHONHASHSEED=25101 /home/ubuntu/venvs/rcmf-py311/bin/python \
  scripts/run_rcmf_reproducible_pipeline_14b.py \
  --contract "$ROOT/preflight/stage_dag.json" \
  --run-root "$ROOT" \
  --authorize-and-run \
  --authorization-file "$ROOT/preflight/explicit_user_authorization.json"
```

No authorization grant exists now.
