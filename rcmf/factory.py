from __future__ import annotations

from typing import Any

from rcmf.config import RCMFConfig
from rcmf.injection.none import build_injector
from rcmf.memory.compiler import (
    ExperienceCompiler,
    StateEncoder,
    build_lightweight_encoder,
)
from rcmf.model.backends.api import APIBackend
from rcmf.model.backends.hf_qwen import HFQwenBackend
from rcmf.model.backends.mock import MockBackend
from rcmf.training.trainer import RCMFTrainer


def build_backend(config: RCMFConfig, load_model: bool = True):
    if config.model.backend == "hf_qwen":
        return HFQwenBackend(
            model_name=config.model.name,
            dtype=config.model.dtype,
            device_map=config.model.device_map,
            freeze_backbone=config.model.freeze_backbone,
            enable_thinking=config.model.enable_thinking,
            load_model=load_model,
        )
    if config.model.backend == "api":
        return APIBackend(model_name=config.model.name)
    if config.model.backend == "mock":
        return MockBackend()
    raise ValueError(f"Unknown model backend: {config.model.backend}")


def _backend_vocab_and_dims(backend: Any, config: RCMFConfig) -> tuple[int, int, Any | None]:
    model = getattr(backend, "model", None)
    tokenizer = getattr(backend, "tokenizer", None)
    vocab_size = getattr(tokenizer, "vocab_size", None)
    model_dim = None
    token_embedding = None
    if model is not None:
        cfg = getattr(model, "config", None)
        model_dim = getattr(cfg, "hidden_size", None)
        vocab_size = vocab_size or getattr(cfg, "vocab_size", None)
        if hasattr(model, "get_input_embeddings"):
            token_embedding = model.get_input_embeddings()
            vocab_size = vocab_size or token_embedding.num_embeddings
            model_dim = model_dim or token_embedding.embedding_dim
    if vocab_size is None:
        vocab_size = 32000
    if model_dim is None:
        model_dim = config.encoder.hidden_size
    return int(vocab_size), int(model_dim), token_embedding


def build_memory_modules(config: RCMFConfig, backend: Any):
    vocab_size, model_dim, token_embedding = _backend_vocab_and_dims(backend, config)
    shared_encoder = build_lightweight_encoder(
        vocab_size=vocab_size,
        encoder_cfg=config.encoder,
        token_embedding=token_embedding if config.encoder.type == "light_transformer" else None,
    )
    if config.encoder.shared_state_experience_encoder:
        experience_encoder = shared_encoder
        state_text_encoder = shared_encoder
    else:
        experience_encoder = shared_encoder
        state_text_encoder = build_lightweight_encoder(
            vocab_size=vocab_size,
            encoder_cfg=config.encoder,
            token_embedding=token_embedding if config.encoder.type == "light_transformer" else None,
        )
    compiler = ExperienceCompiler(
        encoder=experience_encoder,
        hidden_size=config.encoder.hidden_size,
        memory=config.memory,
        address=config.address,
        use_write_strength=config.compiler.use_write_strength,
    )
    state_encoder = StateEncoder(
        encoder=state_text_encoder,
        hidden_size=config.encoder.hidden_size,
        memory=config.memory,
        address=config.address,
    )
    injector = build_injector(
        injector_type=config.injector.type,
        program_dim=config.memory.program_dim,
        model_dim=model_dim,
        vocab_size=vocab_size,
        num_prefix_tokens=config.injector.num_prefix_tokens,
        initial_scale=config.injector.initial_scale,
    )
    return compiler, state_encoder, injector


def build_trainer(config: RCMFConfig, backend: Any) -> RCMFTrainer:
    compiler, state_encoder, injector = build_memory_modules(config, backend)
    return RCMFTrainer(config, backend, compiler, state_encoder, injector)

