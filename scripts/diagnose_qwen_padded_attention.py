from __future__ import annotations

import argparse
import json
import time

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer


def main() -> int:
    parser = argparse.ArgumentParser(description="TRAIN-free Qwen padded-attention timing diagnostic")
    parser.add_argument("--model-snapshot", required=True)
    parser.add_argument("--attention-implementation", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=32)
    args = parser.parse_args()
    tokenizer = AutoTokenizer.from_pretrained(args.model_snapshot, local_files_only=True)
    tokenizer.padding_side = "left"
    texts = [
        tokenizer.apply_chat_template(
            [{"role": "user", "content": "Return one household action." + (" x" * count)}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        for count in (1, 8, 16, 24, 32, 40)
    ]
    batch = tokenizer(texts, padding=True, return_tensors="pt").to("cuda")
    model = AutoModelForCausalLM.from_pretrained(
        args.model_snapshot,
        local_files_only=True,
        torch_dtype=torch.bfloat16,
        attn_implementation=args.attention_implementation,
    ).to("cuda")
    model.eval()
    started = time.perf_counter()
    with torch.no_grad():
        output = model.generate(
            **batch,
            do_sample=False,
            max_new_tokens=args.max_new_tokens,
            use_cache=True,
            eos_token_id=tokenizer.eos_token_id,
            pad_token_id=tokenizer.eos_token_id,
        )
    elapsed = time.perf_counter() - started
    result = {
        "attention_implementation": args.attention_implementation,
        "batch_size": len(texts),
        "prompt_width": int(batch["input_ids"].shape[1]),
        "max_new_tokens": args.max_new_tokens,
        "generated_width": int(output.shape[1] - batch["input_ids"].shape[1]),
        "elapsed_seconds": elapsed,
        "peak_cuda_bytes": torch.cuda.max_memory_allocated(),
        "passed": True,
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
