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

## Paper-disclosure note

For paper or appendix reporting: one official successful AppWorld train trajectory, `2a163ab_3`, was excluded from the RCMF training prepared dataset because its raw official trace contains repeated full social-feed dumps that expand a single episode to multi-million-token training contexts, far beyond Qwen3-8B's 40,960-token effective context window. The raw official trace was not altered. The exclusion removes 1 train memory record and 72 per-step train decision examples, including 66 over-context examples; 638 decision examples and 46 memory records remain.
