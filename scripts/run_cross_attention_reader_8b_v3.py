from __future__ import annotations

import _bootstrap  # noqa: F401

from rcmf.training.cross_attention_persistent_hooks_8b import (
    persistent_checkpoint_reader_forward,
)
import scripts.run_cross_attention_reader_8b as base


def main() -> None:
    base._forward = persistent_checkpoint_reader_forward
    base.main()


if __name__ == "__main__":
    main()

