# RCMF：面向 Codex 的完整实现计划

## 0. 项目目标与不可偏离的边界

实现单 bank 版本的 **Reversible Compiled Memory Field（RCMF）**。系统把不断增长的 agent 经验集合编译成固定形状的可加、可删除记忆状态；推理时不把原始记忆插入 prompt，也不对记忆条目做 Top-k 检索，而是根据当前 agent 状态直接读取整个已编译记忆场，并用固定数量的 latent prefix tokens 或其他可切换注入器影响冻结的 Qwen3-8B。

核心数学定义：

\[
\Delta V_i = \rho_i\alpha_i p_i^\top,\qquad
\Delta c_i = \rho_i\alpha_i
\]

\[
V = \sum_{i\in\mathcal A}\Delta V_i,\qquad
c = \sum_{i\in\mathcal A}\Delta c_i
\]

\[
m(s)=b(s)^\top V,\qquad
d(s)=b(s)^\top c
\]

默认归一化读取：

\[
z(s)=\left(1-e^{-d(s)}\right)\frac{m(s)}{d(s)+\epsilon}
\]

原始线性版本作为消融：

\[
z_{\mathrm{raw}}(s)=b(s)^\top V
\]

其中：

- \(\alpha_i\in\mathbb R^R\)：经验的地址/适用区域；
- \(p_i\in\mathbb R^P\)：经验诱导的行为程序；
- \(\rho_i\in[0,1]\)：写入强度；
- \(b(s)\in\mathbb R^R\)：当前状态地址；
- \(V\in\mathbb R^{R\times P}\)：单一 memory bank；
- \(c\in\mathbb R^R\)：地址质量/写入质量累计量；
- \(z(s)\in\mathbb R^P\)：送入语言模型的 memory control vector。

必须明确：RCMF 编译的是 **experience-to-behavior memory**，不是无损档案问答系统。原始轨迹始终保留在 Ledger 中，但正常推理不读取 Ledger 文本。自然语言冲突自动发现、无限动态扩容和任意原文逐字恢复不属于 v1 的核心承诺。

---

## 1. 对现有文件的处理原则

不要在现有文件上堆叠全部逻辑。保留它们作为参考或 legacy adapter，新建模块化项目。

### `agent(2).py`

可复用：

- AppWorld 的 ReAct 循环；
- Python 代码块提取；
- `world.execute(code)`；
- AppWorld 官方 evaluator 调用；
- trace 和 usage 保存。

必须修改：

- 用 `time.perf_counter()` 替换 Windows 专用的 `ctypes.windll.kernel32`；
- 把模型调用从全局 `MODEL.forward` 改成注入的 `ModelBackend`；
- 把 prompt、状态渲染、memory read 和模型生成拆开；
- `subprocess.run` 不要在 list 参数下使用 `shell=True`；
- 每个 ReAct turn 读取一次 RCMF，并在这一 turn 的生成中复用；
- 日志不要打印凭据、完整隐私 prompt 或 API token。

### `main.py`

可复用：

- AppWorld split 迭代；
- resume/pass@k 思路；
- 单任务 JSON 与最终汇总。

必须修改：

- 所有硬编码开关迁移到 YAML；
- 支持 `train / compile / evaluate / baseline / ablation / scaling` 子命令；
- benchmark、模型、memory、loss、seed 和输出路径均由配置决定；
- checkpoint、memory snapshot 和实验配置一起保存。

### `model.py`

保留为可选远程 teacher backend；不要把它作为训练和正式评测的主后端。新建统一接口：

```python
class ModelBackend(Protocol):
    def tokenize_messages(...): ...
    def forward_train(...): ...
    def generate(...): ...
    def score_targets(...): ...
```

实现：

- `HFQwenBackend`：本地 Qwen3-8B，训练和正式评测；
- `APIBackend`：包装现有 DashScope/OpenRouter，只用于离线 teacher label 或数据生成；
- `MockBackend`：单元测试。

### `prompt(2).py`

当前包含长示例和 playbook。它可以作为 full-context demonstration baseline，但不能作为所有方法共同的系统 prompt，否则会掩盖或污染记忆系统效果。

建立三个 prompt profile：

- `minimal`：只含环境规则、输出格式和必要安全约束；RCMF、RAG、LoRA、no-memory 主实验共同使用；
- `full_demo`：当前长示例 prompt，仅用于 full-context/few-shot baseline；
- `teacher`：允许插入相关原始轨迹，用于离线蒸馏与 utility 标注。

### `test_qwen3_8b.py`

只把模型加载方式作为起点。不要沿用逐 token 重新前向整个序列的循环。正式实现必须：

- 使用 `tokenizer.apply_chat_template`；
- 使用 `model.generate(..., use_cache=True)` 或自定义 KV-cache loop；
- 支持 `inputs_embeds`，以便插入 latent prefix；
- 默认关闭 Qwen thinking，确保工具代码格式稳定；
- 训练时不要使用 `device_map="auto"`，改用 Accelerate/FSDP/DeepSpeed；
- 冻结 backbone 的 MVP 可用 BF16，memory 模块保持 FP32 master state。

---

## 2. 推荐项目目录

```text
rcmf_project/
├── pyproject.toml
├── README.md
├── configs/
│   ├── base.yaml
│   ├── model/qwen3_8b.yaml
│   ├── benchmark/appworld.yaml
│   ├── benchmark/evomembench.yaml
│   ├── benchmark/memoryagentbench.yaml
│   ├── ablation/*.yaml
│   └── baseline/*.yaml
├── rcmf/
│   ├── config.py
│   ├── schemas.py
│   ├── model/
│   │   ├── backends/base.py
│   │   ├── backends/hf_qwen.py
│   │   ├── backends/api.py
│   │   └── backends/mock.py
│   ├── memory/
│   │   ├── compiler.py
│   │   ├── state.py
│   │   ├── ledger.py
│   │   ├── normalization.py
│   │   └── write_controller.py
│   ├── injection/
│   │   ├── base.py
│   │   ├── prefix.py
│   │   └── logit_bias.py
│   ├── training/
│   │   ├── datasets.py
│   │   ├── episodic_sampler.py
│   │   ├── losses.py
│   │   ├── teacher_labels.py
│   │   └── trainer.py
│   ├── benchmarks/
│   │   ├── base.py
│   │   ├── appworld/
│   │   │   ├── adapter.py
│   │   │   ├── agent.py
│   │   │   ├── data.py
│   │   │   ├── prompt.py
│   │   │   └── evaluator.py
│   │   ├── evomembench/adapter.py
│   │   └── memoryagentbench/adapter.py
│   ├── baselines/
│   │   ├── no_memory.py
│   │   ├── full_context.py
│   │   ├── bm25.py
│   │   ├── dense_rag.py
│   │   ├── lora_memory.py
│   │   ├── fast_weight.py
│   │   ├── awm.py
│   │   ├── ace.py
│   │   ├── amem.py
│   │   └── delta_mem.py
│   ├── eval/
│   │   ├── runner.py
│   │   ├── metrics.py
│   │   ├── latency.py
│   │   └── scaling.py
│   └── utils/
│       ├── logging.py
│       ├── seed.py
│       └── serialization.py
├── scripts/
│   ├── prepare_appworld.py
│   ├── prepare_evomembench.py
│   ├── prepare_memoryagentbench.py
│   ├── generate_trajectories.py
│   ├── build_teacher_labels.py
│   ├── train.py
│   ├── compile_memory.py
│   ├── evaluate.py
│   ├── run_baselines.py
│   ├── run_ablations.py
│   └── run_scaling.py
└── tests/
    ├── test_memory_algebra.py
    ├── test_ledger.py
    ├── test_prefix_injection.py
    ├── test_no_memory_equivalence.py
    ├── test_no_data_leakage.py
    ├── test_appworld_smoke.py
    └── test_benchmark_adapters.py
```

---

## 3. 统一数据结构与 BenchmarkAdapter

### 核心 schema

```python
@dataclass
class MemoryRecord:
    memory_id: str
    benchmark: str
    episode_id: str
    task_id: str
    raw_trajectory: dict
    experience_text: str
    outcome: float
    success: bool
    metadata: dict

@dataclass
class DecisionExample:
    benchmark: str
    episode_id: str
    step_id: int
    state_text: str
    target_text: str
    target_type: str        # code | tool_call | answer | action
    candidate_memory_ids: list[str] | None
    metadata: dict

@dataclass
class AgentStep:
    state_text: str
    action_text: str
    observation_text: str
    done: bool
    reward: float | None

@dataclass
class BenchmarkResult:
    task_id: str
    success: bool
    score: float
    steps: int
    prompt_tokens: int
    generated_tokens: int
    ttft_ms: float
    wall_time_s: float
    extra_metrics: dict
```

### 统一 adapter 接口

```python
class BenchmarkAdapter(ABC):
    def load_splits(self, config) -> dict[str, list[str]]: ...
    def build_memory_records(self, split: str) -> Iterable[MemoryRecord]: ...
    def build_decision_examples(self, split: str) -> Iterable[DecisionExample]: ...
    def render_state(self, env_state, history) -> str: ...
    def render_experience(self, trajectory) -> str: ...
    def run_episode(self, policy, task_id, config) -> BenchmarkResult: ...
    def evaluate_episode(self, task_id, trace) -> BenchmarkResult: ...
```

benchmark adapter 只能负责：

- 环境启动与动作执行；
- 轨迹转为统一 schema；
- 状态/经验序列化；
- 官方评测。

以下部分必须完全共享：

- ExperienceCompiler；
- StateEncoder；
- MemoryState/Ledger；
- PrefixInjector；
- utility/action/interference losses；
- episodic sampler；
- trainer；
- latency/scaling evaluator。

---

## 4. RCMF 模型实现

### 4.1 共享轻量编码器

不要为每次 memory read 再运行完整 Qwen3-8B。默认实现一个轻量共享 encoder：

- 输入 embedding：复用并冻结 Qwen token embedding；
- 2 层 `TransformerEncoder`；
- hidden size 512；
- 8 heads；
- FFN 2048；
- dropout 0.1；
- attention pooling 或 EOS pooling；
- experience 最大 1024 tokens；
- state 最大 512 tokens。

配置允许：

- `encoder.shared=true/false`；
- `encoder.type=light_transformer|qwen_hidden|mean_embedding`；
- `encoder.train_token_embedding=false`。

### 4.2 ExperienceCompiler

```python
h_e = experience_encoder(experience_text)
alpha_logits = W_alpha(h_e)             # [R]
alpha = sparse_address(alpha_logits)     # [R]
p = normalize_signed(W_p(h_e))           # [P]
rho = sigmoid(W_rho(h_e))                # scalar
```

默认：

- `R=128`；
- `P=256`；
- `address_topk=8`；
- Top-k 后 softmax；
- `p = tanh(...)` 后 RMS normalize；
- `rho` 初始偏置使初始写入较弱，例如 sigmoid 后约 0.1。

输出：

```python
delta_v = rho * outer(alpha, p)   # FP32
_delta_c = rho * alpha            # FP32
```

### 4.3 StateEncoder

```python
h_s = state_encoder(state_text)
b_logits = W_b(h_s)
b = sparse_or_dense_address(b_logits)
```

默认与 `alpha` 使用相同归一化形式，但参数不共享。支持消融：

- dense softmax；
- Top-k softmax；
- entmax/sparsemax；
- 随机固定地址；
- 语义 cosine 地址。

### 4.4 MemoryState

必须是独立、可测试的纯代数模块：

```python
class MemoryState:
    V: Tensor[R, P]   # FP32
    c: Tensor[R]      # FP32

    def add(delta): ...
    def remove(delta): ...
    def replace(old_delta, new_delta): ...
    def read(b, mode): ...
    def snapshot(path): ...
    def load(path): ...
```

读取模式：

- `none`: `z=b@V`；
- `mass`: 默认公式；
- `sqrt_count`: `z=(b@V)/sqrt(b@c+eps)`；
- `global_norm`: 只作消融，不建议默认。

要求：

- 加、删、替换均在 FP32 完成；
- 送入 Qwen 前才 cast 到 BF16；
- read 复杂度只依赖 `R*P`，不依赖 Ledger 条目数；
- 支持 batch read：`b @ V`。

### 4.5 Ledger

v1 采用 append-only ledger：

- metadata：SQLite 或 JSONL；
- 大 tensor：safetensors 分片；
- 每条记录存 `delta_v`、`delta_c`、compiler version、checksum；
- 状态：`active|superseded|deleted`；
- 保存 raw trajectory 和规范化 experience text；
- 删除时只改状态并从 MemoryState 减去该 delta，不物理删除原始记录；
- 支持从 active ledger 全量重建 `V,c`；
- snapshot 必须原子写入。

### 4.6 WriteController

核心 v1 只保证在已知 `memory_id` 时精确删除或替换。另实现一个默认关闭的写入路径扩展：

```text
new memory
 -> write-time BM25/dense candidate search
 -> relation classifier/LLM
 -> ADD | REPLACE(target_id) | DELETE(target_id) | UNCERTAIN
```

该搜索只发生在写入阶段，不参与正常任务推理。所有涉及自然语言冲突解析的实验必须明确标记为 `RCMF+WriteController`，不能把它混入核心 RCMF 的能力声明。

---

## 5. Memory 注入 Qwen3-8B

### 5.1 默认：动态 latent prefix

```python
prefix = prefix_mlp(z).view(K, d_model)
inputs_embeds = concat(prefix, token_embeddings, dim=sequence)
attention_mask = concat(prefix_mask, token_mask)
labels = concat(ignore_labels_for_prefix, target_labels)
```

默认：

- `K=8`；
- 两层 MLP：`P -> 4P -> K*d_model`；
- final layer 零初始化或很小初始化；
- 可学习 scalar `prefix_scale`，初始 0.1；
- prefix token 数与记忆条目数无关。

需要正确处理：

- position ids；
- attention mask；
- chat template；
- generation 时第一次 prefill 使用 prefix，后续 token 只使用 KV cache；
- 每个 ReAct turn 根据新 observation 重算一次 `z` 和 prefix；
- 同一 turn 内所有生成 token 不重复重算 memory field。

### 5.2 备用注入器

实现统一接口：

```python
class MemoryInjector(ABC):
    def prepare_train_inputs(...): ...
    def prepare_generate_inputs(...): ...
```

至少支持：

- `prefix`：主方法；
- `logit_bias`：`z -> vocab bias`，主要作消融；
- `none`：no-memory equivalence test。

动态 LoRA/hidden adapter 可以留到 phase 2，不要阻塞首个可运行版本。

---

## 6. 数据生成与无泄漏训练

### 6.1 AppWorld

首轮继续使用用户现有的 90/57/168 划分做 smoke test，但正式论文应遵循官方 split，并明确哪些 split 含完整 ground truth。

训练数据来源优先级：

1. 官方 train/dev ground-truth API calls 与 compiled solution；
2. 重放 ground truth，记录每一步 observation，生成 state-action pairs；
3. 当前 ReAct/ACE 产生的成功轨迹；
4. 失败轨迹及自动 reflection，仅作为可开关增强。

实现 `prepare_appworld.py`：

- 读取 task instruction；
- 读取/重放官方 ground truth；
- 生成完整 raw trajectory；
- 每一步生成 `DecisionExample(state_text, target_code)`；
- 一条 episode 生成一条 `MemoryRecord`；
- 保存 train/dev/test task IDs 和来源标记；
- 严禁把 query episode 自己的 trajectory 放入 support memory；
- test 环境绝不调用 ground truth。

### 6.2 统一 experience 序列化

不同 benchmark 最终都输出相同骨架：

```text
[BENCHMARK]
...
[TASK]
...
[INITIAL STATE]
...
[STEPS]
Step 1
Observation: ...
Action: ...
Tool/API: ...
Result: ...
...
[OUTCOME]
Success/Failure, reward
[LESSON]
可选 reflection 或自动抽取的程序性规则
```

raw trajectory 永久保存；compiler 输入可以截断、压缩或只选字段，但必须记录具体策略，保证复现。

### 6.3 Episodic meta-training sampler

每个训练 batch 由两部分组成：

- support episodes：编译为临时 `V,c`；
- query episodes：生成训练状态和目标动作。

硬约束：

```python
support_episode_ids.isdisjoint(query_episode_ids)
```

默认 curriculum：

- 前期 support size：4–8；
- 中期：16–32；
- 后期：64 或更大；
- 每个 batch 随机打乱 support 写入顺序；
- 部分 batch 注入无关、语义相似但错误的 hard negatives。

最终评测时，使用全部允许的训练记忆编译一次全局 snapshot，再在 dev/test 上运行 agent。

---

## 7. 训练目标与阶段

### Stage 0：接口和数据验证

- no-memory local Qwen agent 能运行；
- 数据能生成；
- 单 batch 能通过；
- 所有泄漏检查通过。

### Stage 1：Utility/address 预训练

Utility 不是普通 LM loss，而是记忆对当前状态行动质量的增益。

高质量标签：

\[
u(e,s)=\mathrm{CE}_{\mathrm{without}\ e}(a^*)-
       \mathrm{CE}_{\mathrm{with}\ e}(a^*)
\]

更昂贵的小规模标签：

\[
u_{\mathrm{rollout}}(e,s)=R_{\mathrm{with}\ e}-R_{\mathrm{without}\ e}
\]

实现三层标注：

1. 便宜规则标签：相同 API 前置条件、相同工具错误、相同 workflow；
2. teacher CE 差值：teacher 看到单条原始记忆文本；
3. 少量 paired rollout：校准前两类标签。

损失：

```text
L_utility_mse
L_pairwise_rank
L_hard_negative
```

预测量可用：

\[
\hat u(e_i,s)=b(s)^\top\alpha_i
\]

### Stage 2：端到端行为训练

support memories 编译成 `V,c`；query state 读取 `z`；prefix 注入冻结 Qwen；只对目标动作/代码 token 计算 CE。

总损失：

\[
\mathcal L=
\mathcal L_{action}
+\lambda_u\mathcal L_{utility}
+\lambda_r\mathcal L_{rank}
+\lambda_o\mathcal L_{orth}
+\lambda_s\mathcal L_{sparse}
+\lambda_i\mathcal L_{interference}
+\lambda_d\mathcal L_{distill}
\]

建议：

- `L_action`：目标代码/工具调用/答案 CE；
- `L_distill`：与可见原始相关记忆的 teacher policy KL；
- `L_sparse`：地址稀疏度或 entropy regularization；
- `L_orth`：对一个 batch 的地址协方差或地址投影权重做去相关，不是多 bank 正交；
- `L_interference`：加入一条被标为无关的记忆后，旧 query 输出不应显著变化。

`L_reversibility` 主要做数值单元测试，不必作为主要训练 loss；如果加入，比较 `+delta-delta` 前后的 logits/hidden。

### Stage 3：可选环境级优化

只有 Stage 2 已经稳定后，再尝试 DPO/GRPO/环境回报优化。首版论文不应依赖这一阶段才能工作。

### 默认优化参数

作为起点，而不是写死：

```yaml
optimizer: adamw
lr_compiler: 2.0e-4
lr_injector: 2.0e-4
lr_encoder: 1.0e-4
weight_decay: 0.01
warmup_ratio: 0.03
grad_clip: 1.0
precision: bf16
memory_master_dtype: fp32
effective_batch_size: 32
seeds: [1, 2, 3]
```

backbone 默认冻结。LoRA 只作为 baseline 或单独消融，不能默认启用，否则无法区分记忆存于 RCMF 还是模型权重。

---

## 8. 三个核心 benchmark

### 8.1 AppWorld

用途：多步、真实状态变化、API 调用与程序性经验迁移。主指标为官方 task success/task goal completion，同时测 ReAct 步数、工具错误、TTFT、总耗时和输入 token。

关键实验：

- train memories -> dev/test；
- useful-but-dissimilar；
- similar-but-harmful；
- 多条经验组合；
- 删除错误经验后的恢复；
- memory size 从 0 到全量的扩展曲线。

### 8.2 EvoMemBench

作为主记忆 benchmark，覆盖：

- episode 内知识更新；
- episode 内执行；
- episode 间知识更新；
- episode 间工具、网页、具身执行经验演化。

直接复用其官方数据和 evaluator，不修改任务内容。adapter 把 knowledge answer、tool call、web action 和 embodied action 统一映射成 `DecisionExample.target_type`。

### 8.3 MemoryAgentBench

作为诊断 benchmark，覆盖：

- accurate retrieval；
- test-time learning；
- long-range understanding；
- selective forgetting/conflict。

注意：它和 EvoMemBench 部分子任务有来源重合。论文中应把 MemoryAgentBench 作为能力诊断表，不把两个总分当作完全独立的证据重复计算。

对于 selective forgetting：

- 核心 RCMF 报告显式 memory ID 删除能力；
- 自然语言冲突定位使用独立的 `RCMF+WriteController`；
- 不得把 oracle memory ID 的结果包装成“自动冲突解决”。

### 可选压力测试：LongMemEval-V2

只在核心三项稳定后加入，用于上百轨迹、超长历史和规模延迟。由于其证据收集/上下文生成接口与“直接行为编译”不完全一致，单独报告，不阻塞主线。

---

## 9. Baseline 设计

所有可控 baseline 必须使用同一个 Qwen3-8B、同一个 minimal system prompt、同一批训练轨迹、相同生成预算和同一硬件。

### 必做 baseline

1. **No-memory ReAct**
   - 冻结 Qwen3-8B；
   - 无历史记忆；
   - 只保留当前任务轨迹。

2. **Full-context raw episodic memory**
   - 原始轨迹直接插入 prompt；
   - 在上下文不足时采用固定、公开的截断策略；
   - 作为高成本上界和“原始经验不压缩”对照。

3. **BM25 RAG**
   - trajectory/chunk 文本检索；
   - Top-k 插入 prompt；
   - k 在 dev 上选择。

4. **Dense RAG**
   - Qwen3-Embedding-4B 或同等级公开 embedding；
   - 和 BM25 使用相同 chunk 与 k 搜索空间。

5. **LoRA memory**
   - 在同一批训练 trajectory 上 SFT；
   - base frozen，只训练 LoRA；
   - 报告写入新记忆所需训练时间和更新成本。

6. **Simple fast-weight/associative memory**
   - 与 RCMF 使用相同 `R,P` 和 injector；
   - 但只用普通语义 key/value 或 delta-rule；
   - 不使用 utility supervision、质量归一化、干扰损失；
   - 用来证明收益不是“加一个矩阵”本身。

7. **AWM**
   - workflow memory baseline；
   - 优先调用官方实现；
   - 适用于 procedural agent tasks。

8. **ACE**
   - evolving playbook/context baseline；
   - AppWorld 上必须纳入，因为与现有项目及评审预期高度相关。

### 资源允许时加入

9. **A-MEM 或 Mem0**
   - structured/general textual memory；
   - 至少选一个，优先官方可复现版本。

10. **δ-mem**
    - 最接近 latent associative memory 的 baseline；
    - 若官方代码和模型接入可行，优先直接使用；
    - 若无法忠实复现，只能把自实现版本命名为 `delta-rule fast-weight baseline`，不能声称是官方 δ-mem。

11. **Reflexion/ReasoningBank**
    - 在支持反思式 episodic memory 的任务上作为补充。

### 公平性要求

- 官方方法若只支持其他 backbone，报告两组：`official setting` 和 `Qwen3-controlled setting`；
- 不允许只给 RCMF 使用 ground-truth trajectory，而 baseline 使用模型生成的低质量记忆；
- 所有方法共享相同 memory corpus；
- 所有 latency 在同一卡、同 batch、预热后测量；
- 检索索引构建时间和 memory 写入时间都要报告，不能只报在线推理。

---

## 10. Ablation 开关

所有主要模块必须可通过 YAML 开关，不要复制多份代码。

```yaml
memory:
  enabled: true
  rank: 128
  program_dim: 256
  normalization: mass      # none | mass | sqrt_count
  store_per_memory_delta: true

address:
  mode: topk_softmax       # dense_softmax | topk_softmax | entmax | random
  topk: 8
  use_utility_loss: true

compiler:
  shared_encoder: true
  use_write_strength: true
  use_failed_trajectories: false

injector:
  type: prefix             # prefix | logit_bias | none
  num_prefix_tokens: 8

loss:
  utility: true
  rank: true
  sparse: true
  orthogonal: true
  interference: true
  teacher_distillation: true

state:
  include_task: true
  include_latest_observation: true
  history_steps: 4
  include_system_prompt: false
```

### 优先级最高的消融表

- A0：no memory；
- A1：原始 `b@V`，无质量归一化；
- A2：无 utility/ranking，只训练 action CE；
- A3：dense address vs Top-k sparse address；
- A4：无 interference loss；
- A5：无 write strength `rho`；
- A6：prefix vs logit bias；
- A7：`R={32,64,128,256}`；
- A8：`P={64,128,256,512}`；
- A9：memory set size 扩展；
- A10：只成功轨迹 vs 成功+失败轨迹；
- A11：shared vs separate state/experience encoder；
- A12：teacher distillation on/off。

Ledger rollback 是系统性质，删除其存储会让删除功能不可用但未必改变静态准确率，因此应单独做系统实验，不要假装它是普通性能消融。

---

## 11. 评测指标

### 任务性能

- AppWorld 官方 success/task goal completion；
- EvoMemBench 官方 accuracy/success；
- MemoryAgentBench 各能力子分；
- pass@1 为主，pass@3 作为补充；
- 平均工具调用数、无效调用数、不可逆错误数。

### 效率

- 每条 memory 编译/写入延迟；
- 全库首次编译时间；
- active matrix 大小与 Ledger 大小；
- TTFT；
- prefill latency；
- decode tokens/s；
- end-to-end wall time；
- prompt token 数；
- GPU peak memory；
- 检索 baseline 的索引时间和在线检索时间。

### 记忆性质

- `add -> remove` 后 `V,c` 最大绝对误差；
- replace 正确性；
- 写入无关记忆后的旧任务性能漂移；
- 删除错误记忆后的恢复；
- useful-but-dissimilar / similar-but-harmful；
- 多记忆组合；
- memory set size `N={0,10,100,1k,10k}` 时读取延迟与准确率。

真实数据不足以到 10k 时，可复制/扰动训练轨迹做纯系统 scaling，但准确率结论只能基于真实唯一记忆，二者要分开报告。

### 统计

- 训练至少 3 seeds；
- success rate 使用 bootstrap 95% CI；
- 方法比较使用 paired bootstrap 或配对检验；
- 固定任务顺序、采样 seed 和 generation 参数；
- 保存每题 trace，允许逐题配对分析。

---

## 12. 实现里程碑与验收标准

### M0：项目骨架与本地模型后端

完成：

- 配置系统；
- `HFQwenBackend`；
- minimal AppWorld agent；
- 使用 KV cache；
- 现有 API backend 作为 teacher 可选项。

验收：

- Qwen3-8B 本地完成一条 AppWorld smoke task；
- `memory.enabled=false` 时行为与纯本地 Qwen 后端一致；
- Linux 下无 Windows API 依赖。

### M1：Memory algebra 与 Ledger

完成：

- compiler 输出 delta；
- `MemoryState` add/remove/replace/read；
- Ledger、snapshot、rebuild。

验收：

- FP32 下 `add(delta); remove(delta)` 最大误差 `<1e-6`；
- 随机 1000 次写入、删除、替换后，从 Ledger rebuild 与在线状态一致；
- memory read latency 不随 Ledger 条目数变化。

### M2：Prefix injection

完成：

- train/generate 的 latent prefix；
- position/mask/cache 正确；
- injector 开关。

验收：

- prefix 关闭时 logits 与基础模型在容差内一致；
- 单个小 batch 可过拟合；
- 生成时每 turn 只做一次 memory read 和一次 prefix prefill。

### M3：AppWorld 数据管线

完成：

- ground truth replay；
- raw trajectory、MemoryRecord、DecisionExample；
- split 与泄漏检查。

验收：

- 随机抽查轨迹动作可在环境中重放；
- query episode 永不出现在 support；
- test 不读取 ground truth；
- 3–5 个任务完整端到端运行。

### M4：Stage 1/2 训练

完成：

- episodic sampler；
- utility labels；
- action、ranking、sparsity、interference losses；
- checkpoint/resume。

验收：

- utility ranking 在 held-out pairs 上高于随机；
- action loss 稳定下降；
- 同一 checkpoint 重载结果一致；
- frozen Qwen 参数无梯度变化。

### M5：AppWorld 正式实验

完成：

- full memory compilation；
- ReAct RCMF agent；
- no-memory、full-context、BM25、dense RAG、LoRA、fast-weight、ACE/AWM。

验收：

- 每个方法产出同格式 JSON；
- 自动生成性能和效率表；
- failed/resume/pass@k 正确；
- 结果可按 task ID 配对。

### M6：EvoMemBench 与 MemoryAgentBench

完成：

- 两个 adapter；
- 复用同一模型、trainer 和 memory snapshot 格式；
- selective forgetting 的 core 与 `+WriteController` 分开。

验收：

- 官方 sample 与 evaluator 通过；
- training loop 不包含 benchmark-specific 分支，差异只在 adapter；
- benchmark 混合训练可运行。

### M7：Ablation 与 scaling

完成：

- YAML sweep；
- 自动收集 R/P、memory size、normalization、utility、injector 结果；
- latency profiler。

验收：

- 一条命令生成完整 ablation job list；
- 每个 run 保存 resolved config、git commit、seed、环境信息；
- scaling 图所需 CSV 自动生成。

### M8：复现与清理

完成：

- README；
- 环境 lock；
- 数据准备文档；
- 单元测试；
- 失败恢复；
- release scripts。

验收：

```bash
pytest -q
python scripts/prepare_appworld.py --config ...
python scripts/train.py --config ...
python scripts/compile_memory.py --checkpoint ...
python scripts/evaluate.py --benchmark appworld --memory-snapshot ...
```

在干净环境中均可执行。

---

## 13. Codex 实现时必须遵守的工程规则

1. 先完成 M0–M2 的最小闭环，不要一开始实现所有 benchmark 和 baseline。
2. 不修改第三方 benchmark 核心代码，只写 adapter。
3. 所有 benchmark-specific 数据不得进入 `rcmf/memory` 和 `rcmf/training`。
4. 所有开关通过 dataclass/YAML 配置，禁止在训练脚本中散落布尔常量。
5. 所有 tensor shape 都写类型注释和 assert。
6. MemoryState 使用 FP32；模型前向按需转换 dtype/device。
7. 每条 memory delta 必须可追踪到 ledger ID。
8. 删除和替换必须先做单元测试，再接入 agent。
9. 不把 teacher API 结果在线用于测试；teacher 只能提前生成固定数据。
10. 不把 AppWorld ground truth 暴露给评测 agent。
11. 不把当前长 demo prompt 用于 RCMF 主实验。
12. 不使用逐 token 全序列重新计算；必须使用 KV cache。
13. 任何无法忠实复现的论文 baseline 都要改用准确描述的自实现名称。
14. 先保证单 benchmark 正确，再做 mixed-benchmark training。
15. 遇到不确定的 AppWorld ground-truth 字段时，检查官方对象/文档并写兼容探测，不要硬猜字段名。

---

## 14. 推荐的首个最小实验

为了尽快判断构想是否成立，首个实验只做：

- benchmark：AppWorld；
- base：Qwen3-8B frozen；
- memories：train 中成功 ground-truth trajectories；
- query：dev；
- `R=64, P=128, K=4, topk=4`；
- 只训练轻量 encoder、compiler、state encoder、prefix MLP；
- losses：`action + utility ranking`；
- baselines：no-memory、BM25 RAG、full-context、simple fast-weight；
- 先在 10–20 个任务上验证，再扩到 90/57。

首个 go/no-go 标准：

1. RCMF 相比 no-memory 在 dev step-level action accuracy 或完整 task success 上有稳定提升；
2. 相比 simple fast-weight，utility-aware addressing 有增益；
3. memory 条目数增加时，RCMF 输入 token 和读取延迟基本不增长；
4. 删除一条 memory 后可精确恢复 `V,c`；
5. useful-but-dissimilar 测试中优于语义 RAG。

若 1–2 不成立，不要立即扩展 benchmark；优先诊断：

- teacher utility 标签是否可靠；
- state serialization 是否缺少决定性信息；
- prefix 容量是否不足；
- memory normalization 是否抹平有效信号；
- experience compiler 是否只学到了语义相似度；
- support/query 构造是否存在任务分布错位。

---

## 15. 参考工作与代码检索关键词

Codex 在实现 adapter/baseline 前应查阅官方论文和仓库，优先使用官方代码：

- AppWorld（ACL 2024；官方 appworld repository）；
- EvoMemBench（2026；官方 repository）；
- MemoryAgentBench（ICLR 2026；官方 repository）；
- LongMemEval-V2（可选压力测试）；
- Agent Workflow Memory / AWM；
- ACE: Agentic Context Engineering；
- A-MEM；
- Mem0；
- δ-mem；
- Larimar；
- Reflexion；
- ReasoningBank。

论文中应把贡献集中在：

> 将 agent experience set 编译成一个 utility-conditioned、可代数编辑、固定读取成本的 policy field，并通过可逆 Ledger 保留原始经验和逐条修改能力。

不要把单 bank、外积矩阵、latent prefix 或 Ledger 单独声称为创新；创新必须来自完整的训练目标、读写语义、系统性质和实验证据组合。
