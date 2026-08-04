# RCMF Next Iteration: Correctness, Teacher Supervision, Injection Position, and GitHub Handoff

> **Repository:** `BenjaminGump/rcmf-research`  
> **Active branch:** `workflow/research-loop`  
> **Lambda project:** `/lambda/nfs/rcmf-persist/project`  
> **Backbone:** frozen Qwen3-8B  
> **Benchmark:** AppWorld  
> **Date:** 2026-08-04

This is the implementation task for the next RCMF iteration. Preserve all existing code, checkpoints, logs, datasets, and Git history. Do not silently simplify the method, truncate data, replace the full memory bank with sampled memories, or start an expensive full run before the required correctness checks and smoke tests pass.

---

## 0. Main objective

The pipeline is operational, but current results do not prove useful state-conditioned memory:

- Bare Qwen3-8B: `53/168 = 31.55%`.
- Earlier low-disturbance checkpoints could match the first-10 baseline but not reliably improve it.
- The current semantic-retrieval version reached `4/10`, but only `7/37`.
- Stronger memory perturbations damage generation.
- Current `memory_z` statistics do not prove directional state dependence.
- Several intended objectives exist only as flags or unused helper functions.

The next version must establish:

1. correct train/test StateEncoder alignment;
2. correct record-level memory compilation;
3. non-collapsed address/program/read representations;
4. a non-circular primary teacher;
5. a train/test-aligned full memory bank;
6. semantically sensible additive-token injection positions;
7. real utility, ranking, distillation, interference, and anti-collapse objectives;
8. complete GitHub synchronization and handoff.

---

# 1. Formal definition of the full memory bank

This definition is non-negotiable and must be used in code, tests, logs, documentation, teacher-label generation, and training.

For a training decision example from task \(t\):

\[
M_t^{\mathrm{full}}
=
\sum_{i:\operatorname{task}(i)\ne t}\Delta M_i
\]

where:

\[
\Delta M_i=(\Delta V_i,\Delta c_i)
\]

`full` means:

> Every legally available training memory record except all records belonging to the current task, episode, replay, or derived lineage.

It does **not** mean:

- all records including the current task;
- a random four-memory support set;
- a text top-k retrieval result;
- a bank whose composition is arbitrarily different at train and test.

At test time, the test task has no ground-truth trajectory in the training ledger, so the test bank naturally contains all compiled training memories. This is the intended train/test correspondence.

For a legal candidate memory \(j\ne t\):

\[
M_{t,-j}
=
M_t^{\mathrm{full}}-\Delta M_j
\]

The current task's own memory must be absent from both \(M_t^{\mathrm{full}}\) and \(M_{t,-j}\).

## 1.1 Self-memory leakage rule

The current task's successful trajectory contains the target action and future information. It must not be used as a normal positive memory.

A self-memory condition may exist only as an explicitly named **oracle channel-capacity diagnostic**. It must never:

- enter normal action training;
- enter normal teacher candidates;
- be reported as RCMF benchmark performance;
- be used to claim cross-task memory transfer.

Add tests that fail if any current-task/current-lineage memory is included in the normal full bank.

---

# 2. Issue 1: Current `memory_z` statistics do not prove state dependence

The current diagnostic mainly reports statistics over row norms. A nonzero standard deviation of \(\|z(s)\|_2\) does not show that different states produce different vector directions.

Expand diagnostics for:

- state addresses \(b(s)\);
- memory addresses \(\alpha_i\);
- programs \(p_i\);
- write strengths \(\rho_i\);
- raw reads \(m(s)=b(s)^\top V\);
- read masses \(d(s)=b(s)^\top c\);
- normalized reads \(z(s)\);
- final additive embedding deltas.

For every vector matrix \(X\), report:

### Norm statistics

- mean;
- standard deviation;
- min/max;
- percentiles.

### Mean-centered relative variation

\[
R_{\mathrm{variation}}
=
\frac{
\mathbb E_i\|x_i-\bar x\|_2
}{
\mathbb E_i\|x_i\|_2+\epsilon
}
\]

### Pairwise cosine statistics

- mean;
- median;
- p05/p95;
- min/max.

Use deterministic sampling if all-pairs computation is too expensive. Record seed and sample count.

### Centered spectral statistics

For \(X_c=X-\bar X\), report:

- singular-value vector or compact summary;
- effective rank;
- first-PC explained variance;
- top-5 explained variance;
- numerical rank under a documented threshold.

### Address diagnostics

For \(b(s)\) and \(\alpha_i\):

- entropy;
- top-k basis indices;
- top-1 usage histogram;
- exact top-k set histogram;
- load balance;
- fraction of states sharing the same top-1 basis.

### Program diagnostics

For \(p_i\):

- pairwise cosine;
- effective rank;
- norm distribution;
- ratio between final `z` norm and individual program norm.

RMS-normalized programs can all have nearly identical norms while collapsing to one direction. Norm-only statistics are insufficient.

### Behavioral controls

Support, for a fixed checkpoint and states:

```text
zero z
correct z(s)
mean z
shuffled z across states
random norm-matched z
```

Do not label state dependence as verified unless both directional metrics and behavioral controls support it.

Write machine-readable JSON plus a concise Markdown summary, including checkpoint, config, commit SHA, cache version, and number of states.

---

# 3. Issue 2: Train/test StateEncoder inputs are inconsistent

Keep the existing AppWorld full-demo prompt and the three long demonstrations. Do not remove them from bare Qwen or RCMF evaluation.

The required fix is:

> Train-time and test-time StateEncoder representations must use the same structured messages, the same full-demo prompt renderer, and the same Qwen chat template as action generation.

## 3.1 Shared message renderer

Create or consolidate one canonical function, for example:

```python
build_appworld_messages(
    task_message,
    trajectory_so_far,
    prompt_profile="full_demo",
    add_generation_prompt=True,
)
```

Use it for:

- training action prompts;
- training StateEncoder representation caches;
- test action prompts;
- test StateEncoder representations.

Do not keep one train-time format such as:

```text
[SYSTEM PROMPT]
[QUERY]
[TRACE SO FAR]
```

while test-time StateEncoder uses a manually serialized list of chat roles.

## 3.2 Shared message encoder

Implement a path such as:

```python
backend.encode_messages(messages, add_generation_prompt=True)
```

It must use the same:

- tokenizer;
- `apply_chat_template`;
- thinking setting;
- generation-marker behavior;
- model;
- pooling rule.

Do not approximate test messages as a different plain-text format.

After this fix, the only expected train/test difference is that training history uses a successful ground-truth trajectory prefix while test history uses the model's actual trajectory.

## 3.3 Representation-cache invalidation

Create a new cache format/version containing at least:

```text
format version
model identity/revision
tokenizer identity
prompt profile
renderer/chat-template version or hash
enable_thinking
add_generation_prompt
pooling method
decision dataset hash
memory dataset hash
```

Old state caches must not be silently reused.

Add tests proving that a synthetic state produces identical structured messages and token IDs through train and test rendering paths.

---

# 4. Issue 3: Memory records may be weighted by chunk count

The decision-example context audit does not prove that every complete `MemoryRecord.experience_text` fits into one representation chunk.

## 4.1 Required audit

For every memory record, log:

```text
memory_id
task_id
episode_id
raw token count
chunk count
effective tokens per chunk
aggregation mode
```

Summarize:

```text
total records
single-chunk count
multi-chunk count
maximum chunks
chunk-count histogram
all multi-chunk record IDs
```

This audit is required even if every current record has one chunk.

## 4.2 Record-level semantics

One ledger memory record must produce one compiled delta:

\[
\text{one MemoryRecord}
\rightarrow
(\alpha_i,p_i,\rho_i)
\rightarrow
(\Delta V_i,\Delta c_i)
\]

Chunking is an encoding detail. It must not silently multiply write strength.

Do not use unnormalized:

\[
\Delta V_i=\sum_c\Delta V_{ic}
\]

as the default.

## 4.3 Recommended first implementation

Aggregate chunk representations before compilation:

\[
h_i
=
\frac{
\sum_c \ell_{ic}h_{ic}
}{
\sum_c \ell_{ic}
}
\]

where \(\ell_{ic}\) is the effective token count.

Then compile once:

\[
(\alpha_i,p_i,\rho_i)=C_\theta(h_i)
\]

The semantic teacher and candidate bank must also treat one record as one candidate, not one candidate per chunk.

Log:

- aggregation mode;
- all aggregated records;
- chunk counts;
- pre/post aggregation norms;
- final \(\rho_i\).

Add a test showing that duplicating a chunk does not double the default record write mass.

---

# 5. Issue 4: Prevent the global-soft-prompt shortcut with a real primary teacher

Compiled-bank leave-one-out at random initialization is not meaningful supervision. Random compiler/address/program/injector parameters produce arbitrary loss changes.

Therefore:

> Compiled leave-one-out is a later refinement signal, not the initial source of semantic direction.

## 5.1 Roles

### Backbone

Frozen Qwen3-8B.

### Primary text-memory teacher

Frozen Qwen3-8B that can directly read one legal candidate's raw memory text in a teacher-only prompt. It does not use random RCMF modules.

### RCMF student

Frozen Qwen3-8B controlled only by the compiled RCMF bank and additive-token injection.

### Snapshot teacher

A trained RCMF checkpoint used only later for stabilization, cached compiled-utility refresh, or self-distillation. It is not the initial source of semantic truth.

## 5.2 Primary teacher utility

For state \(s_t\), target action \(y_t^\star\), and unchanged full-demo prompt:

\[
L_0
=
-\log Q\left(y_t^\star\mid P_{\mathrm{full-demo}}(s_t)\right)
\]

For a legal cross-task candidate \(e_j\), append its raw memory text in a clearly delimited teacher-only section:

\[
L_j^{\mathrm{text}}
=
-\log Q\left(
y_t^\star
\mid
P_{\mathrm{full-demo}}(s_t),e_j^{\mathrm{text}}
\right)
\]

Define:

\[
u_j^{\mathrm{text}}(s_t)
=
L_0-L_j^{\mathrm{text}}
\]

Interpretation:

- positive: raw text memory improves target-action probability;
- near zero: neutral;
- negative: distracting.

This direction comes from the ground-truth successful next action and frozen Qwen's interpretation of raw memory. It is independent of random RCMF parameters.

Exclude all current-task/current-lineage memories from teacher candidates.

## 5.3 Candidate selection

Candidate selection only reduces computation. It does not assign labels.

Use a deterministic union of:

- top frozen-Qwen hidden-cosine candidates;
- same-App candidates found by programmatic parsing of task text, prior code, API modules, and memory trajectories;
- random low-similarity candidates;
- current high-weight RCMF candidates only after an RCMF checkpoint exists.

The sign and magnitude always come from \(u_j^{\mathrm{text}}\).

No external LLM API is required in this iteration.

On a small audited subset, scan all legal memories to estimate candidate-recall quality.

## 5.4 Teacher cache

Create a versioned cache containing:

```text
state/example ID
task ID
candidate memory ID
candidate source
L0
Lj_text
text utility
teacher model/checkpoint
renderer version
memory text hash
target text hash
token counts
```

Optionally cache target-position teacher logits for distillation.

## 5.5 Training stages

### Stage A: Generate primary teacher labels

RCMF may still be untrained.

### Stage B: Pretrain addressing

Train the StateEncoder and memory applicability path so:

\[
q_j(s)=\rho_j\,b(s)^\top\alpha_j
\]

matches or ranks text-teacher utility.

Because \(q_j\ge0\), negative-utility memories should be pushed toward zero applicability, not assigned negative addresses.

### Stage C: Train program and injector by teacher distillation

Teacher: raw text memory.  
Student: compiled memory.

Use:

- target action CE;
- target-position teacher/student KL or another documented distillation objective;
- utility/ranking supervision.

### Stage D: Full-bank action training

Use exactly:

\[
M_t^{\mathrm{full}}
=
\sum_{i:\operatorname{task}(i)\ne t}\Delta M_i
\]

for the primary action loss.

Do not replace it with a random four-memory bank.

### Stage E: Compiled counterfactual refinement

Only after the RCMF channel is non-random, compute:

\[
u_j^{\mathrm{compiled}}
=
L(s_t,M_{t,-j})
-
L(s_t,M_t^{\mathrm{full}})
\]

Compare text and compiled utility:

```text
text helpful + compiled helpful: expected
text helpful + compiled harmful: compiler/program/injector failure
text neutral + compiled large effect: spurious global perturbation
text harmful + compiled high weight: addressing/interference failure
```

Do not use compiled leave-one-out alone as the initial teacher.

## 5.6 External API policy

Do not use a strong external API model as the default utility teacher.

It may later be added as an optional ablation if local Qwen3-8B cannot produce non-degenerate text-memory teacher signals.

---

# 6. Issue 5: Injection location does not match the intended semantics

The historical virtual-token prefix changed sequence length/positions and failed even with zero memory. Git history preserves that experiment.

Do not keep the old virtual-token prefix as an active research option.

## 6.1 Rename the active mechanism

Rename the active concept from:

```text
AdditivePrefixMemoryInjector
```

to:

```text
AdditiveTokenMemoryInjector
```

A deprecated `additive_prefix` alias may exist only if necessary for loading old checkpoints. The old virtual-token `prefix` type should be removed from active configuration/factory options once no required current code depends on it.

## 6.2 Supported positions

```yaml
injector:
  type: additive_token
  position: first_k | last_prompt_k | last_user_k
  num_tokens: 4
```

### `first_k`

Current behavior: modify the first \(K\) real prompt-token embeddings.

### `last_prompt_k`

Modify the last \(K\) valid prompt positions before target generation.

During training, use labels/target mask to identify the prompt boundary. Never inject into ground-truth target tokens.

During test, use the final valid prompt positions.

### `last_user_k`

Modify the final \(K\) content tokens of the last user message. Use the shared structured-message renderer to locate the span. Do not add visible marker tokens merely for span detection.

## 6.3 Meaning of `K=4`

There is no theoretical or experimental proof that four is optimal. It is an inherited MVP hyperparameter.

First compare positions with:

```text
K = 4
```

held fixed:

```text
first_k
last_prompt_k
last_user_k
```

After choosing a position, compare:

```text
K = 1, 4, 8
```

Do not sweep position and K simultaneously in the first experiment.

## 6.4 Perturbation control

Report:

\[
\|\Delta E\|_F
\]

and:

\[
\frac{\|\Delta E\|_F}
{\|E_{\mathrm{selected}}\|_F+\epsilon}
\]

When comparing K, consider \(1/\sqrt K\) scaling so larger K does not simply inject more total energy.

## 6.5 Required token audit

For representative train and test examples, log:

```text
position mode
K
selected indices
token IDs
token strings
decoded text
message role
demo/current-task/latest-observation classification
original embedding norm
delta norm
delta/original ratio
```

Add tests for:

- zero-delta equivalence to bare Qwen;
- no target-token injection;
- correct prompt boundary;
- correct last-user span;
- one-shot generation hook behavior.

---

# 7. Issue 6: Intended objectives are not implemented by setting flags to `true`

Every claimed objective must have:

- actual input fields;
- trainer computation;
- nonzero execution path;
- gradients;
- logs;
- tests;
- a documented formula.

## 7.1 Utility and ranking

Populate candidate IDs, legal masks, teacher utilities, and labels in the batch.

Implement utility regression/distribution matching and pairwise ranking.

Log:

```text
candidate count
positive/neutral/negative utility counts
utility distribution
predicted applicability distribution
ranking accuracy
teacher/predicted correlation
```

## 7.2 Teacher distillation

Implement a real raw-text-teacher versus compiled-student branch.

Document whether distillation uses:

- target-position logits;
- probability distributions;
- hidden states;
- logit differences from no-memory behavior.

Add a test proving nonzero loss and gradients in intended RCMF modules.

## 7.3 Interference/preservation

Implement explicit preservation for neutral/irrelevant memories.

The reference behavior must be documented, for example no-extra-memory or a legal full-bank reference.

Unrelated memories should not significantly change Qwen behavior.

Do not leave an unused helper function and call the feature implemented.

## 7.4 Sparse loss caution

Top-k normalization is already sparse. Entropy minimization can make every state select the same basis more confidently.

Do not enable sparse loss by default without basis-usage and load-balance diagnostics.

## 7.5 Orthogonality/diversity caution

Batch size one makes within-batch covariance losses ineffective.

Use an actual multi-state batch, queue, or deterministic cross-step buffer.

Measure:

- address variance;
- basis load balance;
- effective rank;
- alpha diversity;
- program diversity.

Do not equate diversity with forcing all vectors to be orthogonal.

## 7.6 Optimizer and gradients

Use explicit parameter groups for:

- StateEncoder;
- compiler/address/program/write-strength heads;
- injector.

Log module learning rates and gradient norms.

At startup save a table:

```text
loss name
enabled by config
required fields present
executed this step
raw value
weighted value
affected modules
```

This prevents configuration-only features from being mistaken for active training objectives.

---

# 8. Issue 7: Complete the existing `7/37` record without new GPU work

Do not extend the current flawed run.

From existing artifacts only, extract:

```text
bare-Qwen first-37 successes
RCMF first-37 successes
retained
gained
lost
both failed
```

Record it as a historical superseded experiment with explicit caveats.

Do not spend time on deep trace analysis unless it reveals a direct implementation bug relevant to this iteration.

---

# 9. Required implementation order

## Milestone 1: Correctness and observability

1. Shared full-demo message renderer.
2. Identical train/test StateEncoder token path.
3. New cache version and invalidation.
4. Expanded collapse diagnostics.
5. Chunk audit and record-level aggregation.
6. Full-bank leakage tests.
7. Historical first-37 paired summary.

## Milestone 2: Additive-token injection

1. Rename/refactor injector.
2. Implement three positions.
3. Add token-position audit.
4. Verify zero-delta equivalence.
5. Run only minimal Qwen smoke tests.

## Milestone 3: Primary text-memory teacher

1. Teacher prompt path.
2. Candidate selection.
3. Ground-truth target-loss scoring.
4. Versioned teacher cache.
5. Small pilot with manual inspection of positive/neutral/negative examples.

Do not begin large student training until teacher labels are shown to be sensible and non-degenerate.

## Milestone 4: Real objectives

1. Addressing utility/ranking.
2. Teacher distillation.
3. Interference/preservation.
4. Anti-collapse diagnostics/regularization.
5. Full-bank action training.

## Milestone 5: Controlled short experiment

Verify:

- each loss executes;
- gradients reach intended modules;
- state dependence increases;
- correct `z` differs from mean/shuffled `z`;
- zero memory preserves baseline;
- no data leakage exists.

Do not launch a full 638-step/full-168 run solely because training code completes.

---

# 10. Minimum experiment matrix

## Injection-position comparison

Hold fixed:

- K = 4;
- dataset;
- seed;
- learning rates;
- initial scale;
- teacher cache;
- full-bank definition;
- training steps.

Compare:

```text
first_k
last_prompt_k
last_user_k
```

## Behavioral controls

For selected checkpoints:

```text
zero z
correct z(s)
mean z
shuffled z
random norm-matched z
```

## Teacher quality

On a deterministic audited subset:

- no-memory target loss;
- raw-text teacher loss;
- utility distribution;
- candidate recall against all-memory scans.

## Student fidelity

Report:

- text utility versus predicted applicability;
- teacher loss versus student loss;
- text-helpful but compiled-harmful cases;
- text-neutral but compiled-large-effect cases.

---

# 11. Cost and safety

Before any expensive run:

1. estimate GPU time;
2. show exact command;
3. show dataset/config/seed/output paths;
4. confirm all preflights;
5. obtain user approval for a substantial full run.

Codex may run unit tests, mock tests, audits, small teacher pilots, short smoke training, and small first-N diagnostics.

Do not silently:

- truncate or filter data;
- drop records;
- alter the three full-demo examples;
- change the test split;
- change generation temperature;
- change max steps;
- replace full bank with sampled supports;
- replace Qwen3-8B;
- delete checkpoints/logs/data.

---

# 12. GitHub synchronization and handoff

All completed source/config/test/documentation changes must be committed and pushed to:

```text
BenjaminGump/rcmf-research
branch: workflow/research-loop
```

Do not leave the true project state only in Codex chat or Lambda.

Update as applicable:

```text
research/CURRENT_STATE.md
research/DECISIONS.md
research/NEXT_EXPERIMENTS.md
research/FAILURE_ANALYSIS.md
research/EVALUATION_CONTRACT.md
research/experiments.jsonl
```

Create a structured handoff in:

```text
research/handoffs/
```

Record explicitly:

- the definition of \(M_t^{\mathrm{full}}\);
- current-task/current-lineage exclusion;
- primary text-memory teacher;
- snapshot-teacher limitation;
- record-level chunk aggregation;
- additive-token positions;
- removal/deprecation of virtual prefix;
- why `K=4` is not theoretically fixed;
- which losses are truly implemented;
- which claims remain unverified.

For every run, record:

```text
commit SHA
config
seed
exact command
dataset/hash
teacher-cache version
checkpoint
Lambda artifact paths
metrics
status/failure reason
per-task changes when available
```

Before reporting completion:

```bash
git status
git log --oneline -5
git rev-parse HEAD
git push
```

Confirm the pushed commit is visible on GitHub.

Do not commit secrets, model caches, checkpoints, private datasets, or large logs.

The final Codex response must include:

```text
branch
commit SHA
push status
modified files
new tests and results
new configs
teacher pilot results
diagnostic outputs
Lambda artifact paths
known limitations
full-run recommendation
whether a full GPU run was started
active processes/tmux sessions
safe-to-terminate status
```

---

# 13. Final success criterion

The next version must not appear safe merely because the memory signal is shrunk until behavior resembles bare Qwen.

It must provide evidence for:

\[
\text{raw experience}
\rightarrow
\text{teacher-verified behavioral utility}
\rightarrow
(\alpha_i,p_i,\rho_i)
\rightarrow
M_t^{\mathrm{full}}
\rightarrow
z(s)
\rightarrow
\text{state-dependent additive-token control}
\rightarrow
\text{improved or preserved behavior}
\]

Required evidence includes:

- no current-task leakage;
- aligned train/test StateEncoder inputs;
- one-record/one-delta semantics;
- non-collapsed addresses and programs;
- correct `z` outperforming mean/shuffled controls;
- helpful memories receiving higher applicability;
- neutral memories preserving baseline behavior;
- harmful compiled effects being detectable and suppressible;
- every claimed loss actually executing.
