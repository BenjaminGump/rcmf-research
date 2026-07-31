from __future__ import annotations

import argparse
import json

import torch


def main() -> None:
    parser = argparse.ArgumentParser(description="Print RCMF checkpoint module shapes.")
    parser.add_argument("--checkpoint", required=True)
    args = parser.parse_args()

    checkpoint = torch.load(args.checkpoint, map_location="cpu")
    config = checkpoint.get("config", {})
    core_keys = ["model", "memory", "encoder", "address", "compiler", "injector", "loss", "training"]
    print("step:", checkpoint.get("step"))
    print("config_core:")
    print(json.dumps({key: config.get(key) for key in core_keys}, ensure_ascii=False, indent=2, sort_keys=True))
    print("omitted_state_keys:")
    print(json.dumps(checkpoint.get("omitted_state_keys", []), ensure_ascii=False, indent=2))
    print("module_shapes:")
    for name, value in checkpoint.get("modules", {}).items():
        print(f"{name}\t{tuple(value.shape)}\t{value.dtype}")


if __name__ == "__main__":
    main()
