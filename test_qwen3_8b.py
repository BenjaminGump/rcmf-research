from transformers import AutoTokenizer, AutoModelForCausalLM
import torch.nn.functional as F
import torch

# 模型名称
model_name = "Qwen/Qwen3-8B"

# 加载 tokenizer
tokenizer = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)

# 加载模型，自动使用 GPU 并根据显存调整数据精度
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    device_map="auto",                 # 自动将模型加载到可用 GPU
    dtype=torch.bfloat16,        # 如果显卡支持 bfloat16（比如 A100），否则改为 torch.float16
    trust_remote_code=True
)
model.eval()
# 模型推理示例
prompt = "请简要介绍一下人工智能的发展历程。"
inputs = tokenizer(prompt, return_tensors="pt").input_ids.to(model.device)

# Manual generation settings
max_new_tokens = 300
temperature = 0.7
top_p = 0.9

outputs = None
for _ in range(max_new_tokens):
    with torch.no_grad():
        logits = model(input_ids=inputs).logits
        logit = logits[:, -1, :]  # Get logits of the last token

    # Optional: top-p (nucleus) sampling
    logit = logit / temperature
    probs = F.softmax(logit, dim=-1)

    # Apply top-p filtering
    sorted_probs, sorted_indices = torch.sort(probs, descending=True)
    cumulative_probs = torch.cumsum(sorted_probs, dim=-1)
    cutoff = cumulative_probs > top_p
    if cutoff.any():
        cutoff_index = cutoff[0].nonzero(as_tuple=False)[0, 0]
        sorted_probs[:, cutoff_index + 1:] = 0
        sorted_probs = sorted_probs / sorted_probs.sum(dim=-1, keepdim=True)
        next_token = torch.multinomial(sorted_probs, num_samples=1)
        next_token = sorted_indices.gather(1, next_token)
    else:
        next_token = torch.multinomial(probs, num_samples=1)

    inputs = torch.cat((inputs, next_token), dim=1)
    
    if outputs is None:
        outputs = next_token
    else:
        outputs = torch.cat((outputs, next_token), dim=1)

    # Early stop if </s> or other special stop token is generated
    if next_token.item() in tokenizer.all_special_ids:
        break

# Decode
result = tokenizer.decode(outputs[0], skip_special_tokens=True)
print(result)

# # 直接推理
# with torch.no_grad():
#     outputs = model.generate(
#         **inputs,
#         max_new_tokens=300,
#         do_sample=True,
#         temperature=0.7,
#         top_p=0.9
#     )

# # 输出结果
# result = tokenizer.decode(outputs[0], skip_special_tokens=True)
# print(result)