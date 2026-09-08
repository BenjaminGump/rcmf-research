from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from rcmf.benchmarks.alfworld.prompt_profile import (
    load_profile as load_alfworld_profile,
)
from rcmf.benchmarks.alfworld.prompt_profile import (
    render_react_messages as render_alfworld_messages,
)
from rcmf.benchmarks.webshop.prompt_profile import (
    render_react_messages as render_webshop_messages,
)
from rcmf.model.backends.hf_qwen import HFQwenBackend


def _count(
    backend: HFQwenBackend,
    messages: Sequence[dict[str, str]],
) -> dict[str, Any]:
    rows = list(messages)
    tokenized = backend.tokenize_messages(rows, add_generation_prompt=True)
    direct = len(
        backend.tokenizer.apply_chat_template(
            rows,
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    )
    runtime_count = int(tokenized.metadata["input_tokens"])
    if runtime_count != direct:
        raise RuntimeError(
            f"portable prompt count differs: backend={runtime_count}, direct={direct}"
        )
    rendered = str(tokenized.metadata["text"])
    return {
        "tokens": runtime_count,
        "direct_tokens": direct,
        "render_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        "count_contract_equal": True,
    }


def validate(repo_root: Path, model_name: str, local_files_only: bool) -> dict[str, Any]:
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        model_name,
        trust_remote_code=True,
        local_files_only=local_files_only,
    )
    backend = HFQwenBackend(enable_thinking=False, load_model=False)
    backend.tokenizer = tokenizer
    prompt_root = repo_root / "assets/prompts"
    alfworld_root = prompt_root / "alfworld/react_task_type_two_demo_v1"
    alfworld_profile, _ = load_alfworld_profile(alfworld_root)
    rows = []
    for family in sorted(alfworld_profile["task_family_prefixes"]):
        messages = render_alfworld_messages(
            profile_root=alfworld_root,
            gamefile=f"{family}/portable-tokenizer-fixture.z8",
            current_trajectory="Your task is to complete the fixture.\n> ",
        )
        rows.append({"dataset": "alfworld", "task_family": family, **_count(backend, messages)})
    webshop_root = prompt_root / "webshop/react_official_one_demo_v1"
    rows.append(
        {
            "dataset": "webshop",
            "profile": "react_official_one_demo_v1",
            **_count(
                backend,
                render_webshop_messages(
                    profile_root=webshop_root,
                    instruction_and_observation=(
                        "Instruction: buy the fixture\n[Search]\nAction:"
                    ),
                ),
            ),
        }
    )
    return {
        "format": "rcmf_portable_prompt_tokenizer_validation_v1",
        "model": model_name,
        "enable_thinking": False,
        "add_generation_prompt": True,
        "model_loaded": False,
        "generation_executed": False,
        "rows": rows,
        "passed": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate portable prompt counts without loading a language model."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--model", default="Qwen/Qwen3-8B")
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            validate(args.repo_root.resolve(), args.model, args.local_files_only),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
