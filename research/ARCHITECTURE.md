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
full-size AppWorld path now treats one `MemoryRecord` as one compiled write.
If a record is too long for one Qwen forward, chunk representations are combined
with a token-weighted mean before the compiler is called. Chunking therefore
does not multiply write strength.

The training sampler uses the full legal train memory bank for each decision
example. The CLI-compatible mode name `all_except_current_task` now excludes
records sharing the current task, episode, replay, or lineage keys. It is not a
random support sample in the full AppWorld path.

## Injection

The active AppWorld configs use `additive_token`, which adds a learned
memory-derived delta to selected existing prompt-token embeddings without
inserting new virtual tokens. The default position is `first_k` with
`num_tokens=4`; additional configs test `last_prompt_k` and `last_user_k`.

The deprecated `additive_prefix` config alias is retained only for old
checkpoints/config compatibility and maps to `additive_token` with
`position=first_k`.

## Losses

The supervised loss is target-only: labels are `-100` for prompt tokens and
trainable for target tokens. EOS is appended to targets.

The current best first-10 config also enables an optional semantic-retrieval
auxiliary loss. It trains the learned state address and compiled memory address
space to better match a Qwen-hidden-representation cosine-similarity teacher.

`loss.utility`, `loss.interference`, and teacher-distillation style objectives
are not active AppWorld objectives unless a future implementation adds real loss
terms for them.

## Evaluation

The corrected fair comparison uses the original full AppWorld prompt,
`max_steps=50`, and `max_new_tokens=512`. The bare Qwen full baseline is
`53/168 = 31.55%`; the fixed first-10 slice is `3/10 = 30%`.
