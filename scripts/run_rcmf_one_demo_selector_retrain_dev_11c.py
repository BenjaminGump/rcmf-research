"""Run EXP-034B dev evaluation through the unchanged EXP-034A harness."""

from __future__ import annotations

import run_rcmf_one_demo_retrain_dev_11b as runner


runner.RUN_UUID = "rcmf_one_demo_selector_retrain_11c_20260830_001"
runner.EXPERIMENT_PREFIX = "exp034b"
runner.CONDITION_NAMES = {
    "N1": "one_demo_fresh_selector_correct_499_memory_field",
    "N2": "one_demo_fresh_selector_key_payload_shuffle_499_memory_field",
}
runner.TASK_RESULT_FORMAT = "rcmf_one_demo_selector_retrain_dev_task_11c_v1"


if __name__ == "__main__":
    runner.main()
