from __future__ import annotations


def evenly_spaced_indices(length: int, count: int) -> list[int]:
    if length <= 0 or count <= 0:
        raise ValueError("length and count must be positive")
    if count == 1:
        return [0]
    return [int(round(index * (length - 1) / (count - 1))) for index in range(count)]


def capped_memory_token_indices(token_count: int, token_cap: int = 256) -> list[int]:
    """Keep at most token_cap source tokens with deterministic full-span coverage."""
    if token_count <= token_cap:
        return list(range(token_count))
    return evenly_spaced_indices(token_count, token_cap)


def slot_indices(capped_token_count: int, slot_count: int = 16) -> list[int]:
    """Select a fixed slot count from the separately encoded capped sequence."""
    return evenly_spaced_indices(capped_token_count, slot_count)
