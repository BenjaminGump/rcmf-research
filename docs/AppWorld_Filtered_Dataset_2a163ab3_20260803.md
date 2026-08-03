# AppWorld filtered prepared dataset: 2a163ab_3

Date: 2026-08-03

Status: approved by the user and implemented as a filtered prepared-dataset copy. The raw official AppWorld files are kept unchanged.

## What was found

The full-demo official successful-trajectory prepared dataset

```text
/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1
```

contains one pathological train episode:

```text
appworld:trace:2a163ab_3
```

The official raw file is:

```text
/lambda/nfs/rcmf-persist/project/experiments/outputs/legacy_react_code_agent/openai/gpt-4o-2024-05-13/train/tasks/2a163ab_3/logs/environment_io.md
```

I only inspected and copied this file; I did not modify the official raw output. It has 72 environment interactions and multiple repeated Venmo social-feed observations of about 600,851 characters each. The first giant output is produced by the code in interaction 6, which paginates the social feed until exhaustion and prints the whole accumulated list.

Important raw-file line ranges:

- Interaction 6 code: lines 377-387; output: lines 391-27158
- Interaction 12 output: lines 27551-54318
- Interaction 18 output: lines 54711-81478
- Interaction 24 output: lines 81870-108637
- Interaction 30 output: lines 109029-135796
- Interaction 36 output: lines 136188-162955
- Interaction 42 output: lines 163347-190114
- Interaction 48 output: lines 190506-217273
- Interaction 54 output: lines 217665-244432

## Prepared-data impact

In the prepared dataset `official_react_gpt4o_train_success_full_demo_a7be6f1`:

- `decision_examples.jsonl` lines 41-112 are from `appworld:trace:2a163ab_3` and contain 72 decision-step examples.
- Lines 47-112, corresponding to step 7 through step 72, exceed Qwen3-8B's effective context limit.
- `memory_records.jsonl` line 4 is the memory record for `appworld:trace:2a163ab_3`.

Length check before filtering:

- Examples: 710
- Effective context limit used for training preflight: 40,960 tokens
- Over-limit prompt+target examples: 66
- All 66 over-limit examples are from `appworld:trace:2a163ab_3`
- Worst sample: step 72, 2,318,676 prompt+target tokens

Length check after excluding this episode:

- Examples: 638
- Memory records: 46
- Over-limit prompt+target examples: 0
- Longest remaining sample: 35,615 prompt+target tokens, from `appworld:trace:afc0fce_1`, step 36

## Filtered dataset

The filtered prepared dataset should be generated at:

```text
/lambda/nfs/rcmf-persist/project/runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803
```

Generation command:

```bash
python scripts/filter_prepared_dataset.py \
  --source runs/appworld/official_react_gpt4o_train_success_full_demo_a7be6f1 \
  --output runs/appworld/official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803 \
  --exclude-episode-id appworld:trace:2a163ab_3 \
  --exclude-task-id 2a163ab_3 \
  --reason "2026-08-03 user-approved filter: official AppWorld train task 2a163ab_3 contains repeated 600,851-character Venmo social-feed observations, causing 66 prepared decision examples to exceed Qwen3-8B's 40,960-token effective context limit."
```

The script writes:

- `decision_examples.jsonl`
- `memory_records.jsonl`
- `summary.json`, with `source_filter`
- `filter_summary.json`, with source hashes, removed rows, line ranges, counts, and the human-readable reason
- `resolved_config.yaml`, copied from the source dataset when present

## Required protocol for future prepared datasets

Before training on any other prepared dataset, AppWorld split, or AppWorld subset, run:

```bash
python scripts/check_training_query_lengths.py \
  --config configs/benchmark/appworld_rcmf_full_prompt.yaml \
  --data <PREPARED_DATA_DIR> \
  --output <RUN_DIR>/query_token_lengths.json \
  --top-k 50
```

If `over_model_max.count` is nonzero:

1. Stop before training.
2. Report the over-limit task ids, episode ids, jsonl line numbers, counts, and worst samples to the user.
3. Ask explicitly whether to filter, inspect, or change the experimental setup.
4. Do not truncate prompt, state, support, or target tokens as a silent workaround.
5. Only after approval, generate a new filtered prepared-dataset copy with `scripts/filter_prepared_dataset.py`.

The training entrypoint also runs `query_length_preflight.json` before training and raises instead of truncating when a sample exceeds the effective context limit.

## Version notes

- Present in data version `official_react_gpt4o_train_success_full_demo_a7be6f1`.
- Eliminated in data version `official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`.
- The corrected Qwen3 baseline evaluation on `test_normal` is unaffected, because this filter changes only the training prepared dataset, not the test split or the baseline agent flow.
- Any RCMF training runs before this filtered dataset either failed preflight or used a data version that contained the pathological `2a163ab_3` episode. Future RCMF AppWorld full-demo training should use the filtered dataset unless the user explicitly requests a different data version.

## Current Lambda runner

For the current filtered-data experiment, use:

```bash
bash scripts/lambda_train_eval_filtered_rcmf.sh <STAMP>
```

The runner performs the approved filter if the filtered dataset does not exist, runs the context-length check, refuses to train if any sample still exceeds the model context limit, trains RCMF for one full epoch by default, compiles the memory snapshot, then evaluates RCMF on `test_normal` first with `--limit 10` and then on the full split. It does not rerun the full Qwen baseline; the summary records the corrected baseline reference `53/168 = 31.5476%`.

## 2026-08-03 training OOM and fix

Run `rcmf_filtered_20260803_101000` generated the filtered dataset and completed both Qwen hidden representation caches:

- `memory_record_representations.pt`
- `decision_state_representations.pt`

It then failed on the first actual training step with CUDA OOM. The cause was not an over-context sample and not generation length. The filtered dataset's longest prompt+target sample was 35,615 tokens, below the 40,960-token effective context limit. The OOM came from the training backend passing `labels` directly to `AutoModelForCausalLM`, which made transformers compute full vocabulary logits and default causal-LM cross entropy for every prompt position, even though prompt labels are `-100`. For a 35k-token sequence and Qwen's vocabulary, that full `[sequence, vocab]` logits tensor can exceed 80GB.

This behavior is present through commit `e179956`. It is fixed after that version by changing `HFQwenBackend.forward_train()` to compute Qwen hidden states for the full untruncated sequence, then apply `lm_head` and cross entropy only at shifted target-token positions where `labels[..., 1:] != -100`. This preserves the full context and the target-only training objective, but avoids allocating prompt-position vocabulary logits.

A retry on the target-only-loss version reached `step=1/638` and then OOMed on a longer sample during Qwen base forward. That exposed a second memory issue: even with the vocabulary-logit allocation removed, frozen-backbone training still needs gradients through the full Qwen computation back to the prefix/memory vector, so long-context activations can exceed 80GB. The next fix enables gradient checkpointing for the frozen Qwen backbone and temporarily sets the model to training mode only for target-only training forwards so transformers actually uses checkpointing. This uses recomputation rather than token truncation.

After checkpointed training completed, RCMF `test10` was `0/10`; traces showed three consecutive 512-token repetitive natural-language outputs with no executable code, so AppWorld returned `No code available to execute.` and the invalid-action guard stopped at 3 steps. `checkpoint_step100` had the same failure pattern, and a zero-memory-bank control still failed in the same 3-step way. That isolated a generation-side prefix issue: `PrefixMemoryInjector.prepare_generate_inputs()` produced `inputs_embeds`, `attention_mask`, and `position_ids` for `prefix+prompt` length, but passed `input_ids` with prompt-only length. This mismatch is present through commit `d1d3d63`; the following fix pads `input_ids` with dummy prefix ids so generation inputs have consistent length and slices generated tokens after the full prefix+prompt length.

The full-length dummy `input_ids` fix did not recover the zero-memory-bank control: prepending even zero-valued prefix embeddings still caused the 3-step no-code failure. This indicates that changing Qwen's effective prompt length/positions is itself too disruptive for AppWorld action generation. The next version keeps the old `prefix` injector for reproducibility but changes the AppWorld full-prompt config to `injector.type=additive_prefix`, which adds the memory-derived vector to the first real prompt token embeddings instead of inserting new virtual tokens. With `memory_z=0`, additive prefix exactly preserves the original prompt embeddings and sequence length.

The first additive-prefix implementation still used `inputs_embeds` during generation, and a zero-memory-bank control continued to fail. That suggests Qwen3 generation is also sensitive to the `inputs_embeds` generation path itself. The follow-up implementation keeps training on additive `inputs_embeds`, but generation now stays on the normal `input_ids` path and applies the additive memory delta through a one-shot embedding-layer forward hook. With a zero memory delta, generation should now use the exact same inputs as the no-memory baseline.

## 2026-08-03 filtered-data training and evaluation results

The first complete additive-prefix filtered-data run was:

```text
/lambda/nfs/rcmf-persist/project/runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_additive_20260803_121500
```

Training details:

- Code version recorded by the run: `5cdc383a404...`
- Data: `official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`
- Decision examples: 638
- Memory records compiled: 46
- Support mode during training: `all_except_current_task`, so each training example can attend to all memory records except the current task's record.
- Qwen backbone: frozen; hidden representations cached offline for memory records and decision states.
- AppWorld prompt/evaluation flow: same full original prompt and `max_steps=50` policy as the corrected Qwen3 baseline.
- Full-run training completed in about 31.5 minutes on the Lambda H100 instance.

Evaluation against the corrected first-10 baseline task set:

- Corrected bare-Qwen baseline: `3/10 = 30%`, from `qwen_appworld_full_prompt_context40_newline_full_20260731_235900`.
- Final additive-prefix checkpoint at scale 1.0: `1/10`; only `325d6ec_1` succeeded.
- Step-100 additive-prefix checkpoint at scale 1.0: `2/10`; `325d6ec_2` and `325d6ec_3` succeeded, but baseline successes `325d6ec_1` and `29a7b7e_1` were lost.
- Step-200 additive-prefix checkpoint at scale 1.0: `1/10`; only `325d6ec_3` succeeded.
- Step-100 additive-prefix checkpoint at scale 0.25: `1/10`; only `325d6ec_3` succeeded.
- Step-100 additive-prefix checkpoint at scale 0.05: `2/10`; `325d6ec_1` and `325d6ec_3` succeeded.
- Step-100 additive-prefix checkpoint at scale 0.0: `3/10`, exactly matching the corrected bare-Qwen first-10 baseline success set (`325d6ec_1`, `325d6ec_3`, `29a7b7e_1`).

The scale-0.0 control is important: it shows the current `agent.py`, full AppWorld prompt, max-step policy, generation hook, and baseline flow are aligned with the bare-Qwen baseline when the learned memory perturbation is disabled. The remaining degradation is therefore caused by the nonzero learned memory path, not by a missing prompt or a broken AppWorld execution loop.

Trace inspection found two representative failure modes:

- `325d6ec_3`: the bare baseline repeatedly used `spotify.next_song()` until finding a liked song; the RCMF checkpoint drifted into solving a different playlist-like task.
- `29a7b7e_1`: the bare baseline moved files one by one and completed; the RCMF checkpoint repeatedly called `file_system.directory_exists("slides.ppt")`, received AppWorld 422 errors, and looped until `max_steps=50`.

Memory-injection statistics for the step-100 checkpoint showed a likely structural problem:

- Qwen input embedding row norm mean: about `1.3758`.
- Additive prefix token norm at memory scale 1.0: mean about `0.4227`, about 31% of the average Qwen embedding row norm.
- Even at memory scale 0.05, the perturbation changed trajectories and reduced test10 accuracy from 3/10 to 2/10.
- The read memory vector `memory_z` was nearly constant across the 638 training states (`std` about `0.0078` at scale 1.0), suggesting the memory read is acting like a mostly global prompt perturbation rather than a strongly state-conditioned retrieval result.

Version status:

- The prepend-prefix generation failure is present through commit `d1d3d63` and eliminated by the additive-prefix/hook path.
- The `inputs_embeds` generation sensitivity is present through the first additive-prefix implementation and eliminated by commit `7f50422`.
- The target-only additive training input mismatch is fixed by commit `5cdc383`.
- Optimizer parameter groups used only `training.lr_compiler` for all trainable modules through commit `7764cd4`. Commit `5a9a141` fixes this so compiler, state encoder, and injector use `lr_compiler`, `lr_encoder`, and `lr_injector` respectively. This does not change the completed `20260803_121500` run's effective learning rates because the active config set all three values to `1e-5`, but it matters for future low-injector or module-specific LR runs.
- As of commit `5a9a141`, the best nonzero-memory test10 result is still `2/10`, below the corrected baseline `3/10`. Further training versions should focus on preventing state-independent/global memory perturbations before attempting a full 168-task evaluation.

## 2026-08-03 low-injector follow-up

Commit `de21bea` adds `configs/benchmark/appworld_rcmf_full_prompt_low_injector.yaml` and updates the Lambda runner so a full-size training run can reuse an existing Qwen hidden representation cache. This version keeps the RCMF structure intact:

- Qwen hidden representations for memory records and decision states.
- Additive-prefix injection.
- Frozen Qwen backbone.
- Target-only supervised loss with EOS in the target.
- Full filtered prepared dataset, 638 decision examples and 46 memory records.
- `support_mode=all_except_current_task`.
- Same AppWorld full prompt, `max_steps=50`, and `max_new_tokens=512` as the corrected baseline flow.

The only intended behavioral change is lower injector disturbance:

- `injector.initial_scale: 0.02`
- `training.lr_compiler: 1e-5`
- `training.lr_encoder: 1e-5`
- `training.lr_injector: 1e-6`

Run:

```text
/lambda/nfs/rcmf-persist/project/runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_lowinj_20260803_152500
```

Final checkpoint result on the same first-10 test set:

- `2/10 = 20%`
- Successes: `fd1f8fa_2`, `325d6ec_1`
- This is below the corrected first-10 baseline `3/10`.

Checkpoint sweep:

- `checkpoint_step100.pt` with memory scale 1.0 reached `3/10 = 30%`.
- Step-100 successes: `fd1f8fa_2`, `325d6ec_1`, `325d6ec_3`.
- Compared with the corrected bare-Qwen first-10 baseline (`325d6ec_1`, `325d6ec_3`, `29a7b7e_1`), this checkpoint preserved two baseline successes, lost `29a7b7e_1`, and gained `fd1f8fa_2`.
- Step-100 average score: `30.0`; average steps: `27.1`; average prompt tokens: `352,115.8`; average generated tokens: `3,487.9`; average wall time: `117.1s`.

Step-100 memory-injection stats:

- `injector_prefix_scale`: about `0.02012`.
- Additive prefix token norm at memory scale 1.0: mean about `0.01418`, approximately 1% of Qwen's average input embedding row norm (`1.3758`).
- `memory_z` is still nearly constant across the 638 training states (`std` about `0.000106` at scale 1.0), so the state-conditioned retrieval/address issue is not solved yet.

Because `checkpoint_step100.pt` reached the first-10 baseline threshold, a remaining-split evaluation was started from `test_normal` `start_index=10`:

```text
/lambda/nfs/rcmf-persist/project/runs/experiments/rcmf_appworld_filtered_lowinj_step100_full_from10_20260803_164500
```

This run should be combined with the first-10 result above if a full 168-task estimate is needed. It intentionally starts after the first 10 tasks to avoid repeating completed AppWorld work.

The remaining-split run was stopped early after 27 completed tasks because the interim result was low enough that continuing the same checkpoint was not a good use of GPU time:

- Remaining-split partial: `3/27 = 11.1%`
- Combined with the completed first 10 tasks: `6/37 = 16.2%`
- To match the corrected full baseline `53/168 = 31.5%`, the remaining 131 tasks would have needed about 36% success after this point.
- The partial run's successes were `0d01c76_1`, `0d01c76_3`, and `ff58e36_3`.

The next local version adds an optional semantic-retrieval auxiliary loss. It is motivated by the repeated observation that action-only training can reach first-10 baseline when the additive perturbation is very small, but the learned memory read remains almost state-independent.

## 2026-08-03 semantic-retrieval follow-up

Commit `8a6ba96` adds an optional semantic-retrieval auxiliary loss. The loss is off by default and is enabled by:

```text
configs/benchmark/appworld_rcmf_full_prompt_semantic_retrieval.yaml
```

The new loss uses frozen Qwen hidden representations as a teacher. For each training example, it compares the current state representation against all support memory representations with cosine similarity, then trains the RCMF state address and compiled memory addresses to produce a similar distribution. This is meant to reduce the state-independent memory-read collapse seen in the action-only low-injector runs.

Run:

```text
/lambda/nfs/rcmf-persist/project/runs/experiments/appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_semretr_20260803_172000
```

Training details:

- Code version: `75eb6c0`
- Data: `official_react_gpt4o_train_success_full_demo_filtered_no_2a163ab3_20260803`
- Decision examples: 638
- Memory records: 46
- Support mode: `all_except_current_task`
- Reused representation cache: `appworld_qwen_repr_full_prompt_filtered_no_2a163ab3_additive_20260803_121500/train/representation_cache`
- `loss_semantic_retrieval` was present in `metrics.jsonl` throughout training, typically around `0.01-0.13` before applying `lambda_semantic_retrieval=0.05`.

Final checkpoint first-10 result:

- `4/10 = 40%`
- Successes: `325d6ec_1`, `325d6ec_2`, `325d6ec_3`, `29a7b7e_1`
- This exceeds the corrected bare-Qwen first-10 baseline `3/10`.
- It keeps all three baseline first-10 successes except that it also adds `325d6ec_2`, which the corrected bare-Qwen first-10 baseline failed.
- Average score: `40.0`; average steps: `18.3`; average prompt tokens: `201,445.7`; average generated tokens: `1,853`; average wall time: `68.6s`.

Because this version exceeded the first-10 baseline, the runner continued into a full 168-task evaluation:

```text
/lambda/nfs/rcmf-persist/project/runs/experiments/rcmf_appworld_full_prompt_filtered_no_2a163ab3_semretr_full_20260803_172000
```

This full run was still in progress when this note was written.

## Paper-disclosure note

For paper or appendix reporting: one official successful AppWorld train trajectory, `2a163ab_3`, was excluded from the RCMF training prepared dataset because its raw official trace contains repeated full social-feed dumps that expand a single episode to multi-million-token training contexts, far beyond Qwen3-8B's 40,960-token effective context window. The raw official trace was not altered. The exclusion removes 1 train memory record and 72 per-step train decision examples, including 66 over-context examples; 638 decision examples and 46 memory records remain.
