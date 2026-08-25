from __future__ import annotations

import _bootstrap  # noqa: F401

import rcmf.training.cross_attention_bounded_hooks_8b as bounded
import scripts.run_cross_attention_reader_8b_v7 as base


class RecomputeCompatibleMemoryBoundedHooks(base.CompatibleMemoryBoundedHooks):
    """Execute the scalar penalty branch in forward and recomputation alike."""

    def finish_forward(self) -> None:
        # Keeping scalar capture enabled preserves checkpoint operation identity.
        # The bounded hook retains no layer-sized delta or probability tensor.
        self.capture_audit = True


def main() -> None:
    base.CompatibleMemoryBoundedHooks = RecomputeCompatibleMemoryBoundedHooks
    bounded.MemoryBoundedCrossAttentionHooks = RecomputeCompatibleMemoryBoundedHooks
    base.main()


if __name__ == "__main__":
    main()
