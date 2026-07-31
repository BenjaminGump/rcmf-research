# agent.py Qwen vs RCMF 对比实验：325d6ec_2

本实验按同一份 `agent.py::AppWorldAgent` 跑同一道 AppWorld 题：

```text
task_id: 325d6ec_2
agent: agent.py::AppWorldAgent
max_context: 40
max_steps: 50
temperature: 0.3
max_new_tokens: 1024
seed: 1
```

区别只在运行时 monkeypatch `model.MODEL.forward`：

```text
base_qwen: Qwen/Qwen3-8B
trained_qwen_rcmf: Qwen/Qwen3-8B + RCMF checkpoint/injector/memory
```

使用的训练模块：

```text
config: configs/benchmark/appworld_mvp_experiment.yaml
checkpoint: runs/experiments/appworld_official_react_gpt4o_train_20260730_170000/train/checkpoint.pt
memory: runs/experiments/appworld_official_react_gpt4o_train_20260730_170000/memory.safetensors
```

脚本：

```text
scripts/run_agent_py_qwen_pair_debug.py
```

该脚本会记录每一步：

```text
llm_input_messages
llm_input_rendered_prompt
raw_model_output
fixed_model_output_from_raw
extracted_code_from_raw
appworld_observation
prompt_tokens
generation_time_s
memory_z statistics, for trained run
```

完整结果文件：

```text
local:
runs/debug/agent_py_pair_325d6ec_2_20260731_014600/base_qwen.json
runs/debug/agent_py_pair_325d6ec_2_20260731_014600/trained_qwen_rcmf.json
runs/debug/agent_py_pair_325d6ec_2_20260731_014600/summary.json

lambda:
/lambda/nfs/rcmf-persist/project/runs/debug/agent_py_pair_325d6ec_2_20260731_014600/base_qwen.json
/lambda/nfs/rcmf-persist/project/runs/debug/agent_py_pair_325d6ec_2_20260731_014600/trained_qwen_rcmf.json
/lambda/nfs/rcmf-persist/project/runs/debug/agent_py_pair_325d6ec_2_20260731_014600/summary.json
```

## 结果

```text
base_qwen:
  is_correct: false
  num_steps: 8
  error: null

trained_qwen_rcmf:
  is_correct: false
  num_steps_logged: 18
  error: CUDA OutOfMemoryError at step 19 before generation
```

## base_qwen 失败机制

裸 Qwen 能正常调用 API，但做错了任务语义。

关键路径：

```text
step 5: show_downloaded_songs -> Found 5 downloaded songs
step 6: show_song_queue -> Found 10 songs in the queue
step 7: 遍历 queue，发现 position 0 的 Tangled Lies 已下载
step 8: complete_task()
```

它没有执行任务要求的“Keep going to the previous song”，而是静态检查 queue 并发现当前
queue position 0 已下载，于是过早完成。官方评测失败。

## trained_qwen_rcmf 失败机制

训练模块版从第 1 步就明显退化。

关键现象：

```text
step 1:
  memory_z_norm: 0
  generated_tokens: 1024
  output: 重复 “Let me ... Spotify ... downloaded ...”
  extracted_code: "Code:"
  observation: Syntax error

step 2:
  memory_z_norm: 0
  output: 仍然重复，并生成 "Code:" 这种无效代码
  observation: Syntax error

step 4-18:
  memory_z_norm: 0
  output: 继续长篇重复
  extracted_code: empty
  observation: No code available to execute.
```

第 19 步在进入 LLM 生成前 OOM：

```text
torch.OutOfMemoryError:
CUDA out of memory. Tried to allocate 16.46 GiB.
process had ~65.67 GiB in use.
```

OOM 发生在：

```text
scripts/run_agent_py_qwen_pair_debug.py::_memory_z_for_messages
  -> trainer.state_encoder(input_ids, attention_mask)
  -> rcmf/memory/compiler.py::LightweightTextEncoder.forward
  -> torch.nn.TransformerEncoder
```

原因是 `state_encoder` 对完整 `agent.py` history 做 Transformer self-attention。随着
AppWorld observation 和无效模型输出累积，state_text 到第 18 步已经超过 22k tokens；
第 19 步继续前向时 H100 80GB 也 OOM。

## 重要结论

当前训练模块不是“帮助 Qwen 读到有用记忆”，而是：

```text
state_encoder address 与 memory bank 几乎不重合
=> memory_z_norm = 0
=> 读出的记忆为零
=> 但 PrefixMemoryInjector 仍会注入训练后的 prefix
=> Qwen 生成从第 1 步开始退化为重复文本/无效代码
```

也就是说，当前 RCMF 路径的主要问题至少有两个：

```text
1. memory read 没读到有效内容：memory_z_norm 持续为 0。
2. 即使 memory_z 为 0，训练后的 prefix injector 仍会扰动 Qwen，破坏原本可用的指令跟随能力。
```

此外，完整 history 不截断地送入当前 `state_encoder` 在长程 AppWorld 中不可行：

```text
full history -> 22k+ state tokens -> Transformer state_encoder OOM
```

后续修复应先做最小诊断，而不是继续正式评测：

```text
1. 检查 injector(memory_z=0) 是否应该严格等价于 no-injector。
2. 检查 checkpoint 后 prefix_scale、MLP bias、zero-memory prefix norm。
3. 检查 state_encoder address 与 memory_state.c 非零 rank 的重合率。
4. 重新设计 AppWorld state encoder 的长上下文处理；这一步不能静默截断，需要单独决定策略。
```
