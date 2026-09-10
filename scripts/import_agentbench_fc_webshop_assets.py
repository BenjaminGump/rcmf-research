from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any


AGENTBENCH_COMMIT = "d1e4a10db08c87075c78972e48ecc182be03e2d5"
AGENTBENCH_REPOSITORY = "https://github.com/THUDM/AgentBench"
TASK_PATH = Path("src/server/tasks/webshop/task.py")
CONFIG_PATH = Path("configs/tasks/webshop.yaml")
PROFILE = "agentbench_fc_webshop_v1"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_value(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ("git", "-C", str(root), *args), text=True, encoding="utf-8"
    ).strip()


def extract_assignment(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one {name} assignment, found {len(matches)}")
    value = ast.literal_eval(matches[0].value)
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agentbench-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.agentbench_root.resolve(strict=True)
    output = args.output_dir.resolve(strict=False)
    if git_value(root, "rev-parse", "HEAD") != AGENTBENCH_COMMIT:
        raise ValueError("AgentBench checkout differs from the frozen commit")

    import yaml

    task = root / TASK_PATH
    config = root / CONFIG_PATH
    system_prompt = extract_assignment(task, "prompt_with_max_turn")
    config_payload: dict[str, Any] = yaml.safe_load(config.read_text(encoding="utf-8"))
    tools = config_payload["default"]["parameters"]["tools"]

    output.mkdir(parents=True, exist_ok=True)
    prompt_path = output / "system_prompt.txt"
    tools_path = output / "tools.json"
    profile_path = output / "profile.json"
    manifest_path = output / "manifest.json"
    prompt_path.write_text(system_prompt, encoding="utf-8", newline="\n")
    tools_path.write_text(
        json.dumps(tools, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    profile_path.write_text(
        json.dumps(
            {
                "action_grammar": {
                    "click": "click_action(value)",
                    "search": "search_action(keywords)",
                },
                "content_changed": False,
                "demonstration_count": 0,
                "format": "rcmf_webshop_prompt_profile_v1",
                "profile": PROFILE,
                "system_asset": "system_prompt.txt",
                "tool_asset": "tools.json",
                "tool_choice": "required",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    manifest = {
        "profile": PROFILE,
        "upstream_repository": AGENTBENCH_REPOSITORY,
        "upstream_commit": AGENTBENCH_COMMIT,
        "upstream_path": TASK_PATH.as_posix(),
        "upstream_blob": git_value(root, "rev-parse", f"HEAD:{TASK_PATH.as_posix()}"),
        "upstream_file_sha256": sha256(task),
        "extracted_text_sha256": sha256(prompt_path),
        "local_path": "system_prompt.txt",
        "local_sha256": sha256(prompt_path),
        "license": "Apache-2.0",
        "extraction_method": (
            "Python AST literal extraction of the sole prompt_with_max_turn assignment; "
            "tools loaded from the pinned YAML with PyYAML"
        ),
        "content_changed": False,
        "status": "AGENTBENCH_FC_STANDARD_200_CANDIDATE",
        "config_path": CONFIG_PATH.as_posix(),
        "config_blob": git_value(root, "rev-parse", f"HEAD:{CONFIG_PATH.as_posix()}"),
        "config_sha256": sha256(config),
        "tools_path": "tools.json",
        "tools_sha256": sha256(tools_path),
        "profile_path": "profile.json",
        "profile_sha256": sha256(profile_path),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"manifest_sha256": sha256(manifest_path), **manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
