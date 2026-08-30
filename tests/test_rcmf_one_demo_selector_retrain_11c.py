from __future__ import annotations

import inspect
from pathlib import Path

import torch

from rcmf.benchmarks.appworld.prompt import (
    FULL_DEMO_FIRST_ONLY_PROFILE,
    appworld_renderer_metadata,
    full_demo_sections,
    get_system_prompt,
)
from rcmf.config import load_config
from rcmf.training.rcmf_joint_full_bank_9a import FrozenSelectorDecomposition
from rcmf.training.signature_balanced_field_7c import (
    state_class_balanced_weights,
    validate_class_balance,
)
from rcmf.training.oracle_convergence_5fb import tensor_state_sha256
from scripts import prepare_rcmf_one_demo_selector_retrain_11c as prepare
from scripts import run_rcmf_one_demo_retrain_dev_11b as dev_11b


CONFIG = Path(
    "configs/benchmark/stage_c_rcmf_one_demo_selector_retrain_11c.yaml"
)


def test_exp034b_locked_recipe_and_single_global_seed() -> None:
    cfg = load_config(CONFIG)
    settings = cfg.raw["stage_c_11c"]
    assert prepare.GLOBAL_SEED == 25101
    assert settings["global_seed"] == 25101
    assert prepare._candidate(settings) == {
        "name": "hard_lr3e4_e120_t075",
        "learning_rate": 3.0e-4,
        "epochs": 120,
        "temperature": 0.75,
        "listwise_weight": 1.0,
        "pairwise_weight": 0.75,
        "hard_negative_weight": 0.75,
        "exact_api_weight": 0.1,
        "stage_weight": 0.1,
    }
    assert settings["selector"]["member_count"] == 3
    seeds = prepare._member_seeds(3)
    assert len(seeds) == len(set(seeds)) == 3
    assert seeds == prepare._member_seeds(3)


def test_exp034b_selector_architecture_is_historical_structure() -> None:
    cfg = load_config(CONFIG)
    selector = cfg.raw["stage_c_11c"]["selector"]
    assert {
        key: selector[key]
        for key in (
            "state_views",
            "transition_views",
            "input_dim",
            "projection_dim",
            "interaction_rank",
        )
    } == {
        "state_views": 10,
        "transition_views": 10,
        "input_dim": 4096,
        "projection_dim": 64,
        "interaction_rank": 32,
    }


def test_exp034b_fresh_member_initialization_is_deterministic_and_distinct() -> None:
    cfg = load_config(CONFIG)
    settings = cfg.raw["stage_c_11c"]["selector"]
    seeds = prepare._member_seeds(3)
    first = prepare._selector(settings, seeds[0])
    repeated = prepare._selector(settings, seeds[0])
    second = prepare._selector(settings, seeds[1])
    first_sha = tensor_state_sha256(first.state_dict())
    assert first_sha == tensor_state_sha256(repeated.state_dict())
    assert first_sha != tensor_state_sha256(second.state_dict())


def test_exp034b_training_function_cannot_load_historical_selector() -> None:
    source = inspect.getsource(prepare._selector_train)
    assert "historical_ensemble" not in source
    assert "historical_selector_root" not in source
    assert '"historical_weights_loaded": False' in source
    assert "dev_used" in source


def test_exp034b_prompt_identity_is_exact_one_demo_asset() -> None:
    cfg = load_config(CONFIG)
    settings = cfg.raw["stage_c_11c"]
    metadata = appworld_renderer_metadata(FULL_DEMO_FIRST_ONLY_PROFILE)
    retained = prepare.sha256_text(
        full_demo_sections(get_system_prompt("full_demo"))[
            "demo_1_with_instruction_prefix"
        ]
    )
    assert retained == settings["expected"]["retained_demo_sha256"]
    assert metadata["initial_messages_sha256"] == settings["expected"][
        "initial_prompt_asset_sha256"
    ]
    assert prepare._prompt_identity(settings)["passed"]


def test_exp034b_full_state_and_downstream_counts_are_locked() -> None:
    cfg = load_config(CONFIG)
    expected = cfg.raw["stage_c_11c"]["expected"]
    assert expected["selector_state_count"] == 638
    assert expected["selector_train_state_count"] == 499
    assert expected["selector_validation_state_count"] == 139
    assert expected["downstream_train_state_count"] == 366
    assert expected["downstream_heldout_state_count"] == 98
    assert (
        expected["selector_state_count"]
        - expected["downstream_train_state_count"]
        - expected["downstream_heldout_state_count"]
        == 174
    )


def test_exp034b_class_balance_keeps_equal_state_and_signature_mass() -> None:
    rows = [
        {"state_example_id": "s0", "signature_class_id": "a"},
        {"state_example_id": "s0", "signature_class_id": "a"},
        {"state_example_id": "s0", "signature_class_id": "b"},
        {"state_example_id": "s1", "signature_class_id": "c"},
    ]
    weights = state_class_balanced_weights(rows)
    validation = validate_class_balance(rows, weights)
    assert validation["passed"]
    assert weights[0] == weights[1]
    assert weights[0] + weights[1] == weights[2]


def test_exp034b_calibrated_factorization_is_exact_without_key_expansion() -> None:
    cfg = load_config(CONFIG)
    selector_settings = cfg.raw["stage_c_11c"]["selector"]
    checkpoints = []
    calibration = []
    for member_index, seed in enumerate(prepare._member_seeds(3)):
        model = prepare._selector(selector_settings, seed)
        checkpoints.append({"model_state_dict": model.state_dict()})
        calibration.append(
            {"train_mean": 0.1 * (member_index + 1), "train_std": 1.0 + member_index}
        )
    decomposition = FrozenSelectorDecomposition.from_checkpoints(
        checkpoints, calibration
    )
    states = torch.randn(2, 10, 4096)
    transitions = torch.randn(3, 10, 4096)
    direct = decomposition.direct_scores(states, transitions)
    decomposed = (
        decomposition.query(states) @ decomposition.key(transitions).T
        + decomposition.intercept
    )
    assert decomposition.key_dim == 960
    torch.testing.assert_close(direct, decomposed, atol=1.0e-5, rtol=1.0e-5)


def test_exp034b_configs_prohibit_scientific_split_substitution() -> None:
    text = CONFIG.read_text(encoding="utf-8")
    assert "full_demo_first_only" in text
    assert "test_normal" not in text
    assert "first37" not in text
    assert "test_challenge" not in text


def test_exp034b_dev_wrapper_changes_identity_not_scientific_conditions() -> None:
    source = Path(
        "scripts/run_rcmf_one_demo_selector_retrain_dev_11c.py"
    ).read_text(encoding="utf-8")
    assert dev_11b.EXPERIMENT_PREFIX == "exp034a"
    assert dev_11b.RUN_UUID == "rcmf_exp031a_one_demo_retrain_11b_20260829_001"
    assert 'runner.EXPERIMENT_PREFIX = "exp034b"' in source
    assert (
        'runner.RUN_UUID = "rcmf_one_demo_selector_retrain_11c_20260830_001"'
        in source
    )
    assert "runner.main()" in source
    assert dev_11b.CONDITIONS == ("N1", "N2")
    assert dev_11b.FIELD_CONTROLS == {"N1": "D1", "N2": "D2"}
