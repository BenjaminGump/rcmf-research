from __future__ import annotations

import argparse
import json
from pathlib import Path

from rcmf.pipeline.portable_v2.release_validation import validate_portable_v2_release


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the portable-v2 release surface.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(validate_portable_v2_release(args.repo_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

