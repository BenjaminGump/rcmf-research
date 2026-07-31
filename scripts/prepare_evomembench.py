from __future__ import annotations

import argparse

import _bootstrap  # noqa: F401


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare EvoMemBench via its official adapter.")
    parser.add_argument("--config", default="configs/benchmark/evomembench.yaml")
    parser.parse_args()
    raise NotImplementedError("EvoMemBench official package/evaluator is not installed in this repo yet")


if __name__ == "__main__":
    main()
