from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


CODE_BLOCK_RE = re.compile(r"```(?:python)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _load_trace(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    trace = payload.get("extra_metrics", {}).get("trace")
    if trace is None:
        trace = payload.get("trace", [])
    if not isinstance(trace, list):
        raise ValueError(f"{path} does not contain a list trace")
    return payload, trace


def _compact(text: str, max_chars: int) -> str:
    text = text.replace("\r\n", "\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20].rstrip() + "\n... [truncated]"


def _extract_code(text: str) -> str | None:
    matches = CODE_BLOCK_RE.findall(text)
    if not matches:
        return None
    return matches[-1].strip()


def inspect_file(path: Path, max_chars: int, show_prompts: bool) -> None:
    payload, trace = _load_trace(path)
    print(f"=== {path} ===")
    for key in ("task_id", "success", "score", "steps", "wall_time_s"):
        if key in payload:
            print(f"{key}: {payload[key]}")
    if "metrics" in payload and isinstance(payload["metrics"], dict):
        metrics = payload["metrics"]
        print(
            "metrics:",
            {
                key: metrics.get(key)
                for key in ("success", "score", "steps", "wall_time_s")
                if key in metrics
            },
        )
    print(f"trace_messages: {len(trace)}")

    turn = 0
    for index, message in enumerate(trace):
        role = message.get("role")
        content = str(message.get("content", ""))
        if role == "assistant":
            turn += 1
            code = _extract_code(content)
            print(f"\n--- assistant turn {turn} message_index={index} ---")
            if code is not None:
                print(_compact(code, max_chars))
            else:
                print("[no code block]")
                print(_compact(content, max_chars))
        elif role == "user" and (show_prompts or content.lstrip().startswith("Output:")):
            label = "observation" if content.lstrip().startswith("Output:") else "user"
            print(f"\n--- {label} after turn {turn} message_index={index} ---")
            print(_compact(content, max_chars))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize saved AppWorld evaluation traces.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--show-prompts", action="store_true")
    args = parser.parse_args()

    for path in args.files:
        inspect_file(path, max_chars=args.max_chars, show_prompts=args.show_prompts)


if __name__ == "__main__":
    main()
