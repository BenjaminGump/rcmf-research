from __future__ import annotations

import argparse
import json
from pathlib import Path

from rcmf.pipeline.portable_v2.config import PortablePipelineConfig
from rcmf.pipeline.portable_v2.dag import portable_stage_graph_manifest
from rcmf.pipeline.manifests import content_sha256


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate a portable-v2 config and print its generic stage contract."
    )
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = PortablePipelineConfig.load(args.config)
    graph = portable_stage_graph_manifest(config.policy)
    report = {
        "format": "rcmf_portable_v2_config_preflight",
        "schema_version": config.schema_version,
        "adapter_factory": config.adapter_factory,
        "dataset_profile": config.dataset_profile,
        "run_root_template": config.run_root_template,
        "stage_graph": graph,
        "stage_graph_sha256": content_sha256(graph),
        "scientific_execution": False,
        "authorized_to_launch": False,
        "passed": True,
    }
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
