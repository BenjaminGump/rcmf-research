from __future__ import annotations

from enum import Enum


HARNESS_PROTOCOL_VERSION = "agent_memory_harness_v1_rc1"


class MethodCapability(str, Enum):
    """Review-only copy of RC1 capability tokens, not a harness implementation."""

    PREPARE_TRAINING = "prepare_training"
    MESSAGE_AUGMENTATION = "message_augmentation"
    MODEL_FORWARD_HOOK = "model_forward_hook"
    ONLINE_STATE = "online_state"
    STATE_SNAPSHOT = "state_snapshot"
    EXTERNAL_SERVICES = "external_services"
    OFFLINE_MEMORY_COMPILE = "offline_memory_compile"
