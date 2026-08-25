from __future__ import annotations

import _bootstrap  # noqa: F401

from rcmf.training.cross_attention_checkpoint_compat_8b import (
    checkpoint_safe_reader_forward,
)
import scripts.run_cross_attention_reader_8b as reader_base
import scripts.validate_cross_attention_reader_posttrain_8b as validation


def main() -> None:
    reader_base._forward = checkpoint_safe_reader_forward
    validation._forward = checkpoint_safe_reader_forward
    validation.main()


if __name__ == "__main__":
    main()

