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


def inspect_file(
    path: Path,
    max_chars: int,
    show_prompts: bool,
    first_turns: int | None,
    last_turns: int | None,
) -> None:
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

    total_turns = sum(1 for message in trace if message.get("role") == "assistant")
    selected_turns: set[int] | None = None
    if first_turns is not None or last_turns is not None:
        selected_turns = set()
        if first_turns is not None:
            selected_turns.update(range(1, min(first_turns, total_turns) + 1))
        if last_turns is not None:
            start = max(1, total_turns - last_turns + 1)
            selected_turns.update(range(start, total_turns + 1))
        omitted = total_turns - len(selected_turns)
        if omitted > 0:
            print(f"selected_turns: {len(selected_turns)}/{total_turns} (omitted {omitted})")

    turn = 0
    active_turn_selected = show_prompts
    for index, message in enumerate(trace):
        role = message.get("role")
        content = str(message.get("content", ""))
        if role == "assistant":
            turn += 1
            active_turn_selected = selected_turns is None or turn in selected_turns
            if not active_turn_selected:
                continue
            code = _extract_code(content)
            print(f"\n--- assistant turn {turn} message_index={index} ---")
            if code is not None:
                print(_compact(code, max_chars))
            else:
                print("[no code block]")
                print(_compact(content, max_chars))
        elif (
            role == "user"
            and active_turn_selected
            and (show_prompts or content.lstrip().startswith("Output:"))
        ):
            label = "observation" if content.lstrip().startswith("Output:") else "user"
            print(f"\n--- {label} after turn {turn} message_index={index} ---")
            print(_compact(content, max_chars))
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize saved AppWorld evaluation traces.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--show-prompts", action="store_true")
    parser.add_argument("--first-turns", type=int)
    parser.add_argument("--last-turns", type=int)
    args = parser.parse_args()

    for path in args.files:
        inspect_file(
            path,
            max_chars=args.max_chars,
            show_prompts=args.show_prompts,
            first_turns=args.first_turns,
            last_turns=args.last_turns,
        )


if __name__ == "__main__":
    main()
