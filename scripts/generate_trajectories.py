from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ReAct/ACE trajectories for RCMF.")
    parser.add_argument("--config", default="configs/base.yaml")
    parser.add_argument("--method", default="react")
    args = parser.parse_args()
    raise NotImplementedError(
        "Trajectory generation should call the AppWorld agent on a bounded split and save "
        "raw trajectories. Wire this after smoke evaluation is stable."
    )


if __name__ == "__main__":
    main()
