from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path

from common import env_snapshot, git_info, repo_root, run_cmd, sha256_file, utc_now, write_json, write_text


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a GitHub-safe experiment start manifest.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--method", required=True)
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--seed", default="0")
    parser.add_argument("--config")
    parser.add_argument("--baseline-run")
    parser.add_argument("--command-file")
    parser.add_argument("--output-dir", default="research/manifests")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    info = git_info(root)
    if info["dirty"] and not args.allow_dirty:
        print("Refusing to start run from a dirty working tree. Use --allow-dirty to snapshot it.")
        print(info["status_short_branch"])
        return 2

    run_dir = root / args.output_dir / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    config_path = (root / args.config).resolve() if args.config else None
    command_path = (root / args.command_file).resolve() if args.command_file else None

    if config_path and config_path.exists():
        shutil.copy2(config_path, run_dir / config_path.name)
    if command_path and command_path.exists():
        shutil.copy2(command_path, run_dir / command_path.name)

    if info["dirty"]:
        write_text(run_dir / "git.diff.patch", run_cmd(["git", "diff"], cwd=root))
        write_text(run_dir / "git.status.txt", info["status_short_branch"] + "\n")

    manifest = {
        "run_id": args.run_id,
        "created_at_utc": utc_now(),
        "hypothesis": args.hypothesis,
        "method": args.method,
        "benchmark": args.benchmark,
        "split": args.split,
        "seed": args.seed,
        "baseline_run": args.baseline_run,
        "git": info,
        "config": str(config_path) if config_path else None,
        "config_sha256": sha256_file(config_path) if config_path else None,
        "command_file": str(command_path) if command_path else None,
        "command_file_sha256": sha256_file(command_path) if command_path else None,
        "python": sys.version,
        "platform": platform.platform(),
        "environment": env_snapshot(),
    }
    write_json(run_dir / "start_manifest.json", manifest)
    print(run_dir / "start_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
