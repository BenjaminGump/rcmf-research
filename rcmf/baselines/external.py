from __future__ import annotations


class MissingExternalBaseline(RuntimeError):
    pass


def require_official_baseline(name: str) -> None:
    raise MissingExternalBaseline(
        f"{name} requires its official implementation before reporting paper results"
    )

