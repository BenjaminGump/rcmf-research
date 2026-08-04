# Current Architecture

## Data

Official successful AppWorld trajectories are converted into two prepared files:

- `decision_examples.jsonl`: one supervised example per agent step.
- `memory_records.jsonl`: one memory record per successful episode.

Each decision example contains the prompt prefix up to the current step and the
current model response as target text. This is not a final-answer-only dataset.

## Representations

The current full-size AppWorld path uses frozen Qwen hidden representations for
both memory records and decision states. These representations are cached
offline where possible, because the base model is frozen.

The earlier lightweight text encoder path is no longer the intended full-size
training path for AppWorld.

## Memory

The compiler maps support memory representations into memory bank tensors. The
training sampler currently uses `support_mode=all_except_current_task` for the
full AppWorld runs, so the current task's own train episode is excluded from its
support memory.

## Injection

The active AppWorld configs use `additive_prefix`, which adds a learned
memory-derived delta to existing prompt embeddings without inserting new virtual
tokens. This was chosen because prepending even zero-valued virtual tokens
changed Qwen's effective sequence and broke AppWorld action generation.

## Losses

The supervised loss is target-only: labels are `-100` for prompt tokens and
trainable for target tokens. EOS is appended to targets.

The current best first-10 config also enables an optional semantic-retrieval
auxiliary loss. It trains the learned state address and compiled memory address
space to better match a Qwen-hidden-representation cosine-similarity teacher.

## Evaluation

The corrected fair comparison uses the original full AppWorld prompt,
`max_steps=50`, and `max_new_tokens=512`. The bare Qwen full baseline is
`53/168 = 31.55%`; the fixed first-10 slice is `3/10 = 30%`.
