from __future__ import annotations

import argparse
from pathlib import Path

from common import git_info, read_text, repo_root, write_text


def _latest_file(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    files = [path for path in directory.glob("*.md") if path.is_file()]
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a concise ChatGPT-readable research snapshot.")
    parser.add_argument("--output")
    parser.add_argument("--include-results", action="store_true")
    args = parser.parse_args()

    root = repo_root()
    info = git_info(root)
    latest_handoff = _latest_file(root / "research" / "handoffs")

    sections = [
        "# RCMF Research Snapshot",
        "",
        "## Git",
        "",
        f"- Branch: `{info['branch']}`",
        f"- Commit: `{info['commit']}`",
        f"- Dirty: `{info['dirty']}`",
        "",
        "## Current State",
        "",
        read_text(root / "research" / "CURRENT_STATE.md"),
        "",
        "## Next Experiments",
        "",
        read_text(root / "research" / "NEXT_EXPERIMENTS.md"),
        "",
    ]
    if latest_handoff:
        sections.extend(["## Latest Handoff", "", read_text(latest_handoff), ""])
    if args.include_results:
        for result in sorted((root / "research" / "results").glob("*.md")):
            sections.extend([f"## Result: {result.name}", "", read_text(result), ""])

    snapshot = "\n".join(sections)
    if args.output:
        write_text(root / args.output, snapshot)
        print(root / args.output)
    else:
        print(snapshot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
