from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from rcmf.benchmarks.alfworld.runtime_agent import (
    FROZEN_FLASH_ATTN_VERSION,
    flash_attention_runtime_entries,
)
from rcmf.benchmarks.alfworld.task_manifest import canonical_sha256


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory the task-owned ALFWorld FlashAttention runtime"
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve(strict=True)
    entries = flash_attention_runtime_entries(root)
    metadata_files = [
        root / row["path"]
        for row in entries
        if row["path"].endswith(".dist-info/METADATA")
    ]
    if len(metadata_files) != 1:
        raise ValueError("FlashAttention distribution metadata is not unique")
    metadata = metadata_files[0].read_text(encoding="utf-8")
    if f"Version: {FROZEN_FLASH_ATTN_VERSION}\n" not in metadata:
        raise ValueError("FlashAttention distribution version differs")
    manifest = {
        "schema_version": "alfworld_flash_attention_runtime_manifest_v1",
        "source_commit": args.source_commit,
        "package": "flash-attn",
        "version": FROZEN_FLASH_ATTN_VERSION,
        "local_root_evidence": str(root),
        "file_count": len(entries),
        "total_bytes": sum(int(row["bytes"]) for row in entries),
        "files": entries,
        "installation_manifest_sha256": canonical_sha256(entries),
    }
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, output)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
