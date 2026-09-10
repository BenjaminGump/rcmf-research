from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rcmf.benchmarks.webshop.generation import merge_construction_blocks


def _object(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.resolve(strict=True).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"expected a JSON object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--block-root", type=Path, action="append", required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--generation-config", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    source_manifest = _object(args.source_manifest)
    source_identity = source_manifest.get("source_identity")
    if not isinstance(source_identity, Mapping) or not source_identity:
        raise ValueError("source manifest must contain a non-empty source_identity")
    result = merge_construction_blocks(
        block_roots=args.block_root,
        source_identity=source_identity,
        construction_config=_object(args.generation_config),
        output_root=args.output_root,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
