from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, Mapping)
        ):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _load_yaml_with_includes(path: Path, seen: set[Path] | None = None) -> dict[str, Any]:
    path = path.resolve()
    seen = seen or set()
    if path in seen:
        raise ValueError(f"Circular config include detected: {path}")
    seen.add(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    includes = data.pop("include", []) or []
    if isinstance(includes, (str, Path)):
        includes = [includes]
    merged: dict[str, Any] = {}
    for include_path in includes:
        include_file = (path.parent / include_path).resolve()
        merged = _deep_merge(merged, _load_yaml_with_includes(include_file, seen))
    return _deep_merge(merged, data)


def _filter_dataclass_kwargs(cls: type, values: Mapping[str, Any]) -> dict[str, Any]:
    valid_names = {f.name for f in fields(cls)}
    return {key: value for key, value in values.items() if key in valid_names}


@dataclass
class ExperimentSection:
    name: str = "rcmf"
    seed: int = 1
    output_dir: str = "runs/rcmf"


@dataclass
class ModelSection:
    backend: str = "hf_qwen"
    name: str = "Qwen/Qwen3-8B"
    dtype: str = "bfloat16"
    freeze_backbone: bool = True
    enable_thinking: bool = False
    device_map: str | None = None


@dataclass
class MemorySection:
    enabled: bool = True
    rank: int = 128
    program_dim: int = 256
    normalization: str = "mass"
    store_per_memory_delta: bool = True
    eps: float = 1.0e-6


@dataclass
class EncoderSection:
    type: str = "qwen_hidden"
    hidden_size: int = 512
    num_layers: int = 2
    num_heads: int = 8
    intermediate_size: int = 2048
    dropout: float = 0.1
    shared_state_experience_encoder: bool = False
    max_state_tokens: int | None = None
    max_experience_tokens: int | None = None
    train_token_embedding: bool = False


@dataclass
class AddressSection:
    mode: str = "topk_softmax"
    topk: int = 8
    use_utility_loss: bool = True


@dataclass
class CompilerSection:
    shared_encoder: bool = True
    use_write_strength: bool = True
    use_failed_trajectories: bool = False
    version: str = "rcmf-v1"


@dataclass
class InjectorSection:
    type: str = "prefix"
    num_prefix_tokens: int = 8
    initial_scale: float = 0.1


@dataclass
class LossSection:
    action: bool = True
    utility: bool = True
    rank: bool = True
    sparse: bool = True
    orthogonal: bool = True
    interference: bool = True
    teacher_distillation: bool = False
    lambda_utility: float = 1.0
    lambda_rank: float = 0.2
    lambda_sparse: float = 0.01
    lambda_orthogonal: float = 0.01
    lambda_interference: float = 0.1
    lambda_distill: float = 0.0


@dataclass
class TrainingSection:
    optimizer: str = "adamw"
    lr_compiler: float = 2.0e-4
    lr_injector: float = 2.0e-4
    lr_encoder: float = 1.0e-4
    weight_decay: float = 0.01
    warmup_ratio: float = 0.03
    grad_clip: float = 1.0
    precision: str = "bf16"
    memory_master_dtype: str = "fp32"
    effective_batch_size: int = 32
    seeds: list[int] = field(default_factory=lambda: [1, 2, 3])
    support_size: int = 8
    hard_negative_rate: float = 0.25


@dataclass
class BenchmarkSection:
    name: str = "appworld"
    splits: dict[str, str] = field(
        default_factory=lambda: {"train": "train", "dev": "dev", "test": "test_normal"}
    )
    max_steps: int = 50
    max_context_turns: int = 40
    task_limit: int | None = None
    prompt_profile: str = "minimal"


@dataclass
class StateSection:
    include_task: bool = True
    include_latest_observation: bool = True
    history_steps: int = 4
    include_system_prompt: bool = False


@dataclass
class RCMFConfig:
    experiment: ExperimentSection = field(default_factory=ExperimentSection)
    model: ModelSection = field(default_factory=ModelSection)
    memory: MemorySection = field(default_factory=MemorySection)
    encoder: EncoderSection = field(default_factory=EncoderSection)
    address: AddressSection = field(default_factory=AddressSection)
    compiler: CompilerSection = field(default_factory=CompilerSection)
    injector: InjectorSection = field(default_factory=InjectorSection)
    loss: LossSection = field(default_factory=LossSection)
    training: TrainingSection = field(default_factory=TrainingSection)
    benchmark: BenchmarkSection = field(default_factory=BenchmarkSection)
    state: StateSection = field(default_factory=StateSection)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, values: Mapping[str, Any]) -> "RCMFConfig":
        cfg = cls(
            experiment=ExperimentSection(
                **_filter_dataclass_kwargs(ExperimentSection, values.get("experiment", {}))
            ),
            model=ModelSection(**_filter_dataclass_kwargs(ModelSection, values.get("model", {}))),
            memory=MemorySection(
                **_filter_dataclass_kwargs(MemorySection, values.get("memory", {}))
            ),
            encoder=EncoderSection(
                **_filter_dataclass_kwargs(EncoderSection, values.get("encoder", {}))
            ),
            address=AddressSection(
                **_filter_dataclass_kwargs(AddressSection, values.get("address", {}))
            ),
            compiler=CompilerSection(
                **_filter_dataclass_kwargs(CompilerSection, values.get("compiler", {}))
            ),
            injector=InjectorSection(
                **_filter_dataclass_kwargs(InjectorSection, values.get("injector", {}))
            ),
            loss=LossSection(**_filter_dataclass_kwargs(LossSection, values.get("loss", {}))),
            training=TrainingSection(
                **_filter_dataclass_kwargs(TrainingSection, values.get("training", {}))
            ),
            benchmark=BenchmarkSection(
                **_filter_dataclass_kwargs(BenchmarkSection, values.get("benchmark", {}))
            ),
            state=StateSection(**_filter_dataclass_kwargs(StateSection, values.get("state", {}))),
            raw=dict(values),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.memory.rank <= 0:
            raise ValueError("memory.rank must be positive")
        if self.memory.program_dim <= 0:
            raise ValueError("memory.program_dim must be positive")
        if self.memory.normalization not in {"none", "mass", "sqrt_count", "global_norm"}:
            raise ValueError(f"Unknown memory normalization: {self.memory.normalization}")
        if self.address.mode not in {
            "dense_softmax",
            "topk_softmax",
            "entmax",
            "sparsemax",
            "random",
            "semantic_cosine",
        }:
            raise ValueError(f"Unknown address mode: {self.address.mode}")
        if self.encoder.type not in {"qwen_hidden", "light_transformer"}:
            raise ValueError(f"Unknown encoder type: {self.encoder.type}")
        if self.injector.type not in {"prefix", "additive_prefix", "logit_bias", "none"}:
            raise ValueError(f"Unknown injector type: {self.injector.type}")

    def to_dict(self) -> dict[str, Any]:
        output = asdict(self)
        output.pop("raw", None)
        return output


def load_config(*paths: str | Path, overrides: Mapping[str, Any] | None = None) -> RCMFConfig:
    merged: dict[str, Any] = {}
    for path in paths:
        merged = _deep_merge(merged, _load_yaml_with_includes(Path(path)))
    if overrides:
        merged = _deep_merge(merged, overrides)
    return RCMFConfig.from_dict(merged)


def save_resolved_config(config: RCMFConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False), encoding="utf-8")


def set_by_dotted_key(values: dict[str, Any], dotted_key: str, value: Any) -> dict[str, Any]:
    cursor = values
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value
    return values


def dataclass_to_plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: dataclass_to_plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {key: dataclass_to_plain(item) for key, item in value.items()}
    if isinstance(value, list):
        return [dataclass_to_plain(item) for item in value]
    return value
