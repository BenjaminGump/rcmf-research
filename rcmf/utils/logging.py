from __future__ import annotations

import logging
import re
from pathlib import Path


SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[^'\"\s]+"),
    re.compile(r"eyJ[A-Za-z0-9_\-.]+"),
]


def redact(text: str, replacement: str = "[REDACTED]") -> str:
    output = text
    for pattern in SECRET_PATTERNS:
        output = pattern.sub(replacement, output)
    return output


def configure_logging(log_file: str | Path | None = None, level: int = logging.INFO) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file is not None:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )

