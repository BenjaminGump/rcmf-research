# Filtered AppWorld Training Dataset

Dataset:

`/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`

Source:

`/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1`

Removed after user approval:

- task: `2a163ab_3`;
- episode: `appworld:trace:2a163ab_3`;
- decision examples: 72;
- memory records: 1;
- over-context examples removed: 66.

Remaining:

- decision examples: 638;
- memory records: 46;
- max total prompt+target tokens after filtering: 35,615;
- over-context examples after filtering: 0.

Important caveat:

The raw official trace was not edited. This filtering applies only to the
prepared RCMF training dataset.
