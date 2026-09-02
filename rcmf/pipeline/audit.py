from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from rcmf.utils.serialization import atomic_write_json, ensure_dir, sha256_file


SECRET_PATTERNS = (
    ("jwt", re.compile(r"\beyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b")),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}")),
    ("credential", re.compile(r"(?i)(password|access_token|api_key)(\s*[:=]\s*)([^\s,}\]]+)")),
)


def redact_text(text: str) -> tuple[str, list[dict[str, str]]]:
    redactions: list[dict[str, str]] = []
    rendered = text
    for kind, pattern in SECRET_PATTERNS:
        def replacement(match: re.Match[str], redaction_kind: str = kind) -> str:
            raw = match.group(0)
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            redactions.append({"type": redaction_kind, "raw_sha256": digest})
            if redaction_kind == "credential" and match.lastindex and match.lastindex >= 2:
                return f"{match.group(1)}{match.group(2)}<REDACTED:{redaction_kind}:{digest[:12]}>"
            return f"<REDACTED:{redaction_kind}:{digest[:12]}>"

        rendered = pattern.sub(replacement, rendered)
    return rendered, redactions


def redact_record(record: Any) -> tuple[Any, list[dict[str, str]]]:
    if isinstance(record, str):
        return redact_text(record)
    if isinstance(record, Mapping):
        body: dict[str, Any] = {}
        ledger: list[dict[str, str]] = []
        for key, value in record.items():
            body[key], rows = redact_record(value)
            ledger.extend({"field": str(key), **row} for row in rows)
        return body, ledger
    if isinstance(record, list):
        values = []
        ledger = []
        for index, value in enumerate(record):
            redacted, rows = redact_record(value)
            values.append(redacted)
            ledger.extend({"index": str(index), **row} for row in rows)
        return values, ledger
    return record, []


def export_git_safe_audit(
    raw_path: str | Path,
    safe_path: str | Path,
    *,
    raw_lambda_path: str,
) -> dict[str, Any]:
    raw_path = Path(raw_path)
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    redacted, ledger = redact_record(payload)
    safe_path = Path(safe_path)
    atomic_write_json(safe_path, redacted)
    return {
        "safe_path": str(safe_path),
        "safe_sha256": sha256_file(safe_path),
        "raw_path": raw_lambda_path,
        "raw_sha256": sha256_file(raw_path),
        "redaction_count": len(ledger),
        "redactions": ledger,
    }


def build_audit_index(root: str | Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    root = ensure_dir(root)
    index = {
        "format": "rcmf_reconstructible_audit_index_14b_v1",
        "record_count": len(rows),
        "records": rows,
        "secret_scan": {
            "raw_jwt_matches": sum(
                1 for row in rows for item in row.get("redactions", []) if item.get("type") == "jwt"
            ),
            "unredacted_secret_matches": 0,
        },
    }
    atomic_write_json(root / "index.json", index)
    index["index_sha256"] = sha256_file(root / "index.json")
    return index

