from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from rcmf.config import load_config
from rcmf.training.appworld_provenance_replay_6h3 import (
    QUARANTINED_TASK_ID,
    SNAPSHOT_SEARCH_VERSION,
    build_quarantine_manifest,
    build_quarantine_sentinel,
    classify_provenance_failure,
    redacted_path,
    select_preflight_branch,
    summarize_corpus_identity,
    text_sha256,
    training_contamination_report,
)
from rcmf.training.appworld_semantic_replay_6h2 import (
    canonical_hash,
    identity_hashes,
    parse_full_demo_query,
)
from rcmf.training.datasets import (
    _parse_appworld_state_text,
    load_decision_examples,
    load_memory_records,
)
from rcmf.training.state_conditioned_transition_6b import (
    AttemptLedger,
    initialize_or_validate_run_manifest,
)
from rcmf.training.transition_memory_6a import state_example_id
from rcmf.utils.serialization import atomic_write_json, atomic_write_text, read_jsonl, sha256_file


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [dict(row) for row in read_jsonl(path)]


def _manifest_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("rows", "query_rows"):
        if isinstance(payload.get(key), list):
            return [dict(row) for row in payload[key]]
    raise ValueError("Manifest has no rows/query_rows")


def _attempt_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {str(row["attempt_id"]) for row in read_jsonl(path)}


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "_.-" else "_" for character in value)


def _atomic_write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _spec_fields(path: Path) -> dict[str, str]:
    payload = _load_json(path)
    supervisor = dict(payload["supervisor"])
    return {
        "instruction": str(payload["instruction"]),
        "first_name": str(supervisor["first_name"]),
        "last_name": str(supervisor["last_name"]),
        "email": str(supervisor["email"]),
        "phone_number": str(supervisor["phone_number"]),
    }


def _field_hashes(fields: Mapping[str, str]) -> dict[str, str]:
    return {key: text_sha256(str(value)) for key, value in sorted(fields.items())}


def _full_query(fields: Mapping[str, str]) -> str:
    return (
        "Now here is another task in a different environment. The task is the following:\n"
        f"My name is: {fields['first_name']} {fields['last_name']}. "
        f"My personal email is {fields['email']} and phone number is "
        f"{fields['phone_number']}.\nTask: {fields['instruction']}"
    )


def _line_span(text: str, needle: str) -> dict[str, Any]:
    start = text.find(needle)
    if start < 0:
        return {"present": False, "start_line": None, "end_line": None}
    end = start + len(needle)
    return {
        "present": True,
        "start_line": text.count("\n", 0, start) + 1,
        "end_line": text.count("\n", 0, end) + 1,
    }


def _redacted_line_context(
    text: str, *, start_line: int | None, end_line: int | None, radius: int = 3
) -> list[dict[str, Any]]:
    if start_line is None or end_line is None:
        return []
    lines = text.splitlines()
    first = max(1, int(start_line) - int(radius))
    last = min(len(lines), int(end_line) + int(radius))
    return [
        {
            "line": line_number,
            "line_sha256": text_sha256(lines[line_number - 1]),
            "nonempty": bool(lines[line_number - 1].strip()),
            "inside_query_span": int(start_line) <= line_number <= int(end_line),
        }
        for line_number in range(first, last + 1)
    ]


def _task_snapshot_index(roots: Sequence[str]) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows: list[dict[str, Any]] = []
    by_identity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for root_text in roots:
        root = Path(str(root_text))
        task_root = root / "tasks"
        if not task_root.is_dir():
            rows.append({"root": str(root), "exists": False, "task_count": 0})
            continue
        count = 0
        for path in sorted(task_root.glob("*/specs.json")):
            count += 1
            fields = _spec_fields(path)
            hashes = _field_hashes(fields)
            identity = canonical_hash(hashes)
            entry = {
                "task_id": path.parent.name,
                "root": str(root),
                "spec_path": str(path),
                "spec_sha256": sha256_file(path),
                "field_sha256": hashes,
                "identity_sha256": identity,
                "full_query_sha256": text_sha256(_full_query(fields)),
            }
            by_identity[identity].append(entry)
        rows.append(
            {
                "root": str(root),
                "exists": True,
                "task_count": count,
                "root_path_sha256": text_sha256(str(root)),
            }
        )
    return rows, by_identity


def _candidate_text_files(
    root: Path,
    *,
    suffixes: set[str],
    name_fragments: Sequence[str],
    maximum_bytes: int,
    task_id: str,
) -> list[Path]:
    if not root.exists():
        return []
    output: list[Path] = []
    fragments = tuple(str(value).lower() for value in name_fragments)
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        name = path.name.lower()
        if task_id.lower() not in path.as_posix().lower() and not any(value in name for value in fragments):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size <= int(maximum_bytes):
            output.append(path)
    return output


def _scan_text_files(
    files: Sequence[Path],
    *,
    query: str,
    source_fields: Mapping[str, str],
    category: str,
) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    total_bytes = 0
    for path in files:
        size = path.stat().st_size
        total_bytes += size
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        full_match = query in text
        component_matches = sorted(
            key
            for key in ("first_name", "last_name", "email", "phone_number")
            if source_fields[key] and source_fields[key] in text
        )
        if not full_match and not component_matches:
            continue
        hits.append(
            {
                "category": category,
                **redacted_path(str(path)),
                "file_sha256": sha256_file(path),
                "file_bytes": size,
                "full_query_match": full_match,
                "matching_component_names": component_matches,
                "is_task_snapshot": path.name == "specs.json",
            }
        )
    return {
        "category": category,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "hit_count": len(hits),
        "hits": hits,
    }


def _git_history_search(repo: Path, values: Mapping[str, str]) -> dict[str, Any]:
    hits: dict[str, list[str]] = {}
    for key, value in values.items():
        completed = subprocess.run(
            ["git", "log", "--all", f"-S{value}", "--format=%H", "--"],
            cwd=repo,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        commits = sorted(set(completed.stdout.splitlines())) if completed.returncode == 0 else []
        hits[key] = commits
    return {
        "repository_head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True
        ).strip(),
        "search_terms": sorted(values),
        "commit_hits": hits,
        "error_free": True,
    }


def _git_lfs_search(
    repo: Path,
    *,
    query: str,
    source_fields: Mapping[str, str],
    maximum_bytes: int,
) -> dict[str, Any]:
    completed = subprocess.run(
        ["git", "lfs", "ls-files", "--all", "--long"],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    common_dir = subprocess.check_output(
        ["git", "rev-parse", "--git-common-dir"], cwd=repo, text=True
    ).strip()
    common_path = Path(common_dir)
    if not common_path.is_absolute():
        common_path = (repo / common_path).resolve()
    object_root = common_path / "lfs" / "objects"
    files = sorted(path for path in object_root.rglob("*") if path.is_file()) if object_root.exists() else []
    needles = {
        "full_query": query.encode("utf-8"),
        **{
            key: str(source_fields[key]).encode("utf-8")
            for key in ("first_name", "last_name", "email", "phone_number")
        },
    }
    hits = []
    searched = 0
    searched_bytes = 0
    skipped_large = 0
    for path in files:
        size = path.stat().st_size
        if size > int(maximum_bytes):
            skipped_large += 1
            continue
        searched += 1
        searched_bytes += size
        content = path.read_bytes()
        matching = sorted(key for key, value in needles.items() if value and value in content)
        if matching:
            hits.append(
                {
                    "object_sha256": sha256_file(path),
                    "object_bytes": size,
                    "matching_component_names": matching,
                }
            )
    return {
        "git_lfs_available": completed.returncode == 0,
        "git_lfs_indexed_line_count": len(completed.stdout.splitlines()) if completed.returncode == 0 else 0,
        "git_lfs_index_sha256": text_sha256(completed.stdout) if completed.returncode == 0 else None,
        "git_lfs_error_sha256": text_sha256(completed.stderr) if completed.returncode != 0 else None,
        "object_root_exists": object_root.exists(),
        "object_count": len(files),
        "searched_object_count": searched,
        "searched_bytes": searched_bytes,
        "skipped_large_object_count": skipped_large,
        "hit_count": len(hits),
        "hits": hits,
    }


def _archive_member_hits(
    path: Path,
    *,
    query: str,
    source_fields: Mapping[str, str],
    suffixes: set[str],
    maximum_bytes: int,
) -> tuple[list[dict[str, Any]], int, int]:
    needles = {
        "full_query": query.encode("utf-8"),
        **{
            key: str(source_fields[key]).encode("utf-8")
            for key in ("first_name", "last_name", "email", "phone_number")
        },
    }
    members: list[tuple[str, int, bytes]] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                if info.is_dir() or info.file_size > int(maximum_bytes):
                    continue
                if Path(info.filename).suffix.lower() not in suffixes:
                    continue
                members.append((info.filename, info.file_size, archive.read(info)))
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for info in archive.getmembers():
                if not info.isfile() or info.size > int(maximum_bytes):
                    continue
                if Path(info.name).suffix.lower() not in suffixes:
                    continue
                extracted = archive.extractfile(info)
                if extracted is not None:
                    members.append((info.name, info.size, extracted.read()))
    hits = []
    for name, size, content in members:
        matching = sorted(key for key, value in needles.items() if value and value in content)
        if matching:
            hits.append(
                {
                    "member_basename": Path(name).name,
                    "member_path_sha256": text_sha256(name),
                    "member_bytes": size,
                    "member_sha256": hashlib.sha256(content).hexdigest(),
                    "matching_component_names": matching,
                }
            )
    return hits, len(members), sum(size for _, size, _ in members)


def _bundle_inventory(
    roots: Sequence[str],
    *,
    repo: Path,
    query: str,
    source_fields: Mapping[str, str],
    suffixes: set[str],
    maximum_bytes: int,
) -> dict[str, Any]:
    seen: set[Path] = set()
    rows: list[dict[str, Any]] = []
    for root_text in roots:
        root = Path(str(root_text))
        if not root.exists():
            continue
        for pattern in ("*.bundle", "*.tar", "*.tar.gz", "*.tgz", "*.zip"):
            for path in sorted(root.glob(pattern)):
                resolved = path.resolve()
                if resolved in seen or not path.is_file():
                    continue
                seen.add(resolved)
                row: dict[str, Any] = {
                    **redacted_path(str(path)),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                    "kind": "git_bundle" if path.suffix == ".bundle" else "archive",
                }
                if path.suffix == ".bundle":
                    listed = subprocess.run(
                        ["git", "bundle", "list-heads", str(path)],
                        cwd=repo,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=False,
                    )
                    heads = [line.split(maxsplit=1)[0] for line in listed.stdout.splitlines() if line.strip()]
                    present = []
                    for commit in heads:
                        checked = subprocess.run(
                            ["git", "cat-file", "-e", f"{commit}^{{commit}}"],
                            cwd=repo,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                        present.append(checked.returncode == 0)
                    row.update(
                        {
                            "head_count": len(heads),
                 ïo;¶‰žËkºwµçq•Ì‰t¤è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰•¥Í¥½¸¥‘•¹Ñ¥Ñä…Õ‘¥Ð‘¥¹½Ð…½Õ¹Ð™½È•Ù•Éä•á…µÁ±”ˆ¤(€€€€€€€¥˜½ÉÁÕÍl‰¥‘•¹Ñ¥Ñå}µ¥Íµ…Ñ¡}½Õ¹Ð‰t€ø€Äè(€€€€€€€€€€€‘•¥Í¥½¸€ôì(€€€€€€€€€€€€€€€€‰™½Éµ…Ðˆè€‰…ÁÁÝ½É±‘}ÁÉ½Ù•¹…¹•}ÁÉ•™±¥¡Ñ}‘•¥Í¥½¹|Ù Í}ØÄˆ°(€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}‰É…¹ ˆè€‰Í½ÕÉ•}‘…Ñ…Í•Ñ}¥‘•¹Ñ¥Ñå}½¹Í¥ÍÑ•¹å}™…¥±ÕÉ”ˆ°(€€€€€€€€€€€€€€€€‰Í¹…ÁÍ¡½Ñ}™½Õ¹ˆè…±Í”°(€€€€€€€€€€€€€€€€‰ÅÕ…É…¹Ñ¥¹•}…±±½Ý•ˆè…±Í”°(€€€€€€€€€€€€€€€€‰É•Á±…å}…±±½Ý•ˆè…±Í”°(€€€€€€€€€€€€€€€€‰ÅÝ•¹}¥µÁ½ÉÑ}½É}™½ÉÝ…É‘}½Õ¹Ðˆè€À°(€€€€€€€€€€€ô(€€€€€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ•™±¥¡Ñ}‘•¥Í¥½¸¹©Í½¸ˆ°‘•¥Í¥½¸¤(€€€€€€€€€€€…ÑÑ•µÁÐ¹ÁÉ½É•ÍÌ¡±…Ñ•ÍÑ}Ù…±¥‘…Ñ•‘}¡•­Á½¥¹ÐõÍÑÈ¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ•™±¥¡Ñ}‘•¥Í¥½¸¹©Í½¸ˆ¤¤(€€€€€€€€€€€ÁÉ¥¹Ð¡©Í½¸¹‘ÕµÁÌ¡‘•¥Í¥½¸°¥¹‘•¹ÐôÈ°Í½ÉÑ}­•åÌõQÉÕ”¤¤(€€€€€€€€€€€É•ÑÕÉ¸(€€€€€€€¥˜½ÉÁÕÍl‰¥‘•¹Ñ¥Ñå}µ¥Íµ…Ñ¡}Ñ…Í­}¥‘Ì‰t€„ômÍÑÈ¡•áÁ•Ñ•‘l‰ÅÕ…É…¹Ñ¥¹•‘}Ñ…Í­}¥‰t¥tè(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰½ÉÁÕÌµ¥Íµ…Ñ ¥Ì¹½Ð¥Í½±…Ñ•Ñ¼Ñ¡”ÁÉ•É•¥ÍÑ•É•Ñ…Í¬ˆ¤((€€€€€€€Ñ…Í­}¥€ôÍÑÈ¡•áÁ•Ñ•‘l‰ÅÕ…É…¹Ñ¥¹•‘}Ñ…Í­}¥‰t¤(€€€€€€€Í½ÕÉ•}™¥•±‘Ì€ôÁÉ¥Ù…Ñ•}Í½ÕÉ•}™¥•±‘ÍmÑ…Í­}¥‘t(€€€€€€€½™™¥¥…±}™¥•±‘Ì€ôÁÉ¥Ù…Ñ•}½™™¥¥…±}™¥•±‘ÍmÑ…Í­}¥‘t(€€€€€€€Í½ÕÉ•}ÅÕ•Éä€ôÍÑÈ¡É•½É‘Í}‰å}Ñ…Í­mÑ…Í­}¥‘t¹É…Ý}ÑÉ…©•Ñ½Éål‰ÅÕ•Éä‰t¤(€€€€€€€Í¹…ÁÍ¡½Ñ}É½½ÑÌ°¥‘•¹Ñ¥Ñå}¥¹‘•à€ô}Ñ…Í­}Í¹…ÁÍ¡½Ñ}¥¹‘•à¡Í•ÑÑ¥¹Íl‰Í¹…ÁÍ¡½Ñ}Í•…É ‰ul‰Ñ…Í­}Í¹…ÁÍ¡½Ñ}É½½ÑÌ‰t¤(€€€€€€€Í½ÕÉ•}¥‘•¹Ñ¥Ñä€ô…¹½¹¥…±}¡…Í ¡}™¥•±‘}¡…Í¡•Ì¡Í½ÕÉ•}™¥•±‘Ì¤¤(€€€€€€€Í½ÕÉ•}¥‘•¹Ñ¥Ñå}‰å}Ñ…Í¬€ôì(€€€€€€€€€€€…¹‘¥‘…Ñ•}Ñ…Í¬è…¹½¹¥…±}¡…Í ¡}™¥•±‘}¡…Í¡•Ì¡™¥•±‘Ì¤¤(€€€€€€€€€€€™½È…¹‘¥‘…Ñ•}Ñ…Í¬°™¥•±‘Ì¥¸ÁÉ¥Ù…Ñ•}Í½ÕÉ•}™¥•±‘Ì¹¥Ñ•µÌ ¤(€€€€€€€ô(€€€€€€€Í½ÕÉ•}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í­Ì€ôÍ½ÉÑ• (€€€€€€€€€€€…¹‘¥‘…Ñ•}Ñ…Í¬(€€€€€€€€€€€™½È…¹‘¥‘…Ñ•}Ñ…Í¬°¥‘•¹Ñ¥Ñä¥¸Í½ÕÉ•}¥‘•¹Ñ¥Ñå}‰å}Ñ…Í¬¹¥Ñ•µÌ ¤(€€€€€€€€€€€¥˜…¹‘¥‘…Ñ•}Ñ…Í¬€„ôÑ…Í­}¥…¹¥‘•¹Ñ¥Ñä€ôôÍ½ÕÉ•}¥‘•¹Ñ¥Ñä(€€€€€€€€¤(€€€€€€€¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•Ì€ô¥‘•¹Ñ¥Ñå}¥¹‘•à¹•Ð¡Í½ÕÉ•}¥‘•¹Ñ¥Ñä°mt¤(€€€€€€€•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ€ômÉ½Ü™½ÈÉ½Ü¥¸¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•Ì¥˜É½Ýl‰Ñ…Í­}¥‰t€ôôÑ…Í­}¥‘t(€€€€€€€½Ñ¡•É}Ñ…Í­}µ…Ñ¡•Ì€ômÉ½Ü™½ÈÉ½Ü¥¸¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•Ì¥˜É½Ýl‰Ñ…Í­}¥‰t€„ôÑ…Í­}¥‘t((€€€€€€€Í•…É¡}Í•ÑÑ¥¹Ì€ôÍ•ÑÑ¥¹Íl‰Í¹…ÁÍ¡½Ñ}Í•…É ‰t(€€€€€€€ÍÕ™™¥á•Ì€ôÍ•Ð¡µ…À¡ÍÑÈ°Í•…É¡}Í•ÑÑ¥¹Íl‰Í•…É¡…‰±•}ÍÕ™™¥á•Ì‰t¤¤(€€€€€€€µ…á¥µÕµ}‰åÑ•Ì€ô¥¹Ð¡Í•…É¡}Í•ÑÑ¥¹Íl‰µ…á¥µÕµ}Ñ•áÑ}™¥±•}‰åÑ•Ì‰t¤(€€€€€€€Í•…É¡}É½ÕÁÌè±¥ÍÑm‘¥ÑmÍÑÈ°¹åut€ômt(€€€€€€€Í½ÕÉ•}É½½Ð€ôA…Ñ ¡ÍÑÈ¡Í•…É¡}Í•ÑÑ¥¹Íl‰Í½ÕÉ•}•áÁ•É¥µ•¹Ñ}É½½Ð‰t¤¤(€€€€€€€Í½ÕÉ•}™¥±•Ì€ô}…¹‘¥‘…Ñ•}Ñ•áÑ}™¥±•Ì (€€€€€€€€€€€Í½ÕÉ•}É½½Ð°(€€€€€€€€€€€ÍÕ™™¥á•ÌõÍÕ™™¥á•Ì°(€€€€€€€€€€€¹…µ•}™É…µ•¹ÑÌõÍ•…É¡}Í•ÑÑ¥¹Íl‰Í•…É¡…‰±•}¹…µ•}™É…µ•¹ÑÌ‰t°(€€€€€€€€€€€µ…á¥µÕµ}‰åÑ•Ìõµ…á¥µÕµ}‰åÑ•Ì°(€€€€€€€€€€€Ñ…Í­}¥õÑ…Í­}¥°(€€€€€€€€¤(€€€€€€€Í•…É¡}É½ÕÁÌ¹…ÁÁ•¹¡}Í…¹}Ñ•áÑ}™¥±•Ì¡Í½ÕÉ•}™¥±•Ì°ÅÕ•ÉäõÍ½ÕÉ•}ÅÕ•Éä°Í½ÕÉ•}™¥•±‘ÌõÍ½ÕÉ•}™¥•±‘Ì°…Ñ•½Éäô‰Í½ÕÉ•}•áÁ•É¥µ•¹Ñ}½ÕÑÁÕÑÌˆ¤¤(€€€€€€€ÉÕ¹}É½½Ð€ôA…Ñ ¡ÍÑÈ¡Í•…É¡}Í•ÑÑ¥¹Íl‰ÁÉ½©•Ñ}ÉÕ¹Í}É½½Ð‰t¤¤(€€€€€€€ÉÕ¹}™¥±•Ì€ô}…¹‘¥‘…Ñ•}Ñ•áÑ}™¥±•Ì (€€€€€€€€€€€ÉÕ¹}É½½Ð°(€€€€€€€€€€€ÍÕ™™¥á•ÌõÍÕ™™¥á•Ì°(€€€€€€€€€€€¹…µ•}™É…µ•¹ÑÌõÍ•…É¡}Í•ÑÑ¥¹Íl‰Í•…É¡…‰±•}¹…µ•}™É…µ•¹ÑÌ‰t°(€€€€€€€€€€€µ…á¥µÕµ}‰åÑ•Ìõµ…á¥µÕµ}‰åÑ•Ì°(€€€€€€€€€€€Ñ…Í­}¥õÑ…Í­}¥°(€€€€€€€€¤(€€€€€€€ÉÕ¹}™¥±•Ì€ômÁ…Ñ ™½ÈÁ…Ñ ¥¸ÉÕ¹}™¥±•Ì¥˜…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È¹É•Í½±Ù” ¤¹½Ð¥¸Á…Ñ ¹É•Í½±Ù” ¤¹Á…É•¹ÑÍt(€€€€€€€Í•…É¡}É½ÕÁÌ¹…ÁÁ•¹¡}Í…¹}Ñ•áÑ}™¥±•Ì¡ÉÕ¹}™¥±•Ì°ÅÕ•ÉäõÍ½ÕÉ•}ÅÕ•Éä°Í½ÕÉ•}™¥•±‘ÌõÍ½ÕÉ•}™¥•±‘Ì°…Ñ•½Éäô‰Á•ÉÍ¥ÍÑ•¹Ñ}ÁÉ½©•Ñ}ÉÕ¹Ìˆ¤¤(€€€€€€€Í¹…ÁÍ¡½Ñ}™¥±•Ì€ômA…Ñ ¡É½Ýl‰ÍÁ•}Á…Ñ ‰t¤™½ÈÙ…±Õ•Ì¥¸¥‘•¹Ñ¥Ñå}¥¹‘•à¹Ù…±Õ•Ì ¤™½ÈÉ½Ü¥¸Ù…±Õ•Ít(€€€€€€€Í•…É¡}É½ÕÁÌ¹…ÁÁ•¹¡}Í…¹}Ñ•áÑ}™¥±•Ì¡Í¹…ÁÍ¡½Ñ}™¥±•Ì°ÅÕ•ÉäõÍ½ÕÉ•}ÅÕ•Éä°Í½ÕÉ•}™¥•±‘ÌõÍ½ÕÉ•}™¥•±‘Ì°…Ñ•½Éäô‰Ñ…Í­}Í¹…ÁÍ¡½Ñ}ÍÁ•Ìˆ¤¤(€€€€€€€¥Ñ}Í•…É €ô}¥Ñ}¡¥ÍÑ½Éå}Í•…É  (€€€€€€€€€€€A…Ñ ¡ÍÑÈ¡Í•…É¡}Í•ÑÑ¥¹Íl‰¥Ñ}É½½Ð‰t¤¤°(€€€€€€€€€€€ì‰™Õ±±}ÅÕ•ÉäˆèÍ½ÕÉ•}ÅÕ•Éä°€¨©í­•äèÍ½ÕÉ•}™¥•±‘Ím­•åt™½È­•ä¥¸€ ‰•µ…¥°ˆ°€‰Á¡½¹•}¹Õµ‰•Èˆ¥õô°(€€€€€€€€¤(€€€€€€€±™Í}Í•…É €ô}¥Ñ}±™Í}Í•…É  (€€€€€€€€€€€A…Ñ ¡ÍÑÈ¡Í•…É¡}Í•ÑÑ¥¹Íl‰¥Ñ}É½½Ð‰t¤¤°(€€€€€€€€€€€ÅÕ•ÉäõÍ½ÕÉ•}ÅÕ•Éä°(€€€€€€€€€€€Í½ÕÉ•}™¥•±‘ÌõÍ½ÕÉ•}™¥•±‘Ì°(€€€€€€€€€€€µ…á¥µÕµ}‰åÑ•Ìõµ…á¥µÕµ}‰åÑ•Ì°(€€€€€€€€¤(€€€€€€€ÑÉ…¹Í™•É}Í•…É €ô}‰Õ¹‘±•}¥¹Ù•¹Ñ½Éä (€€€€€€€€€€€Í•…É¡}Í•ÑÑ¥¹Íl‰ÑÉ…¹Í™•É}É½½ÑÌ‰t°(€€€€€€€€€€€É•Á¼õA…Ñ ¡ÍÑÈ¡Í•…É¡}Í•ÑÑ¥¹Íl‰¥Ñ}É½½Ð‰t¤¤°(€€€€€€€€€€€ÅÕ•ÉäõÍ½ÕÉ•}ÅÕ•Éä°(€€€€€€€€€€€Í½ÕÉ•}™¥•±‘ÌõÍ½ÕÉ•}™¥•±‘Ì°(€€€€€€€€€€€ÍÕ™™¥á•ÌõÍÕ™™¥á•Ì°(€€€€€€€€€€€µ…á¥µÕµ}‰åÑ•Ìõµ…á¥µÕµ}‰åÑ•Ì°(€€€€€€€€¤(€€€€€€€¥˜¹½Ð‰½½°¡ÑÉ…¹Í™•É}Í•…É¡l‰Í•…É¡}½µÁ±•Ñ”‰t¤è(€€€€€€€€€€€É…¥Í”IÕ¹Ñ¥µ•ÉÉ½È ‰ÑÉ…¹Í™•È‰Õ¹‘±”½¹Ñ…¥¹ÌÕ¹É•…¡…‰±”¡•…‘Ì…¹Ý…Ì¹½Ð™Õ±±äÍ•…É¡•ˆ¤(€€€€€€€Í¹…ÁÍ¡½Ñ}Í•…É €ôì(€€€€€€€€€€€€‰™½Éµ…ÐˆèM9AM!=Q}MI!}YIM%=8°(€€€€€€€€€€€€‰Ñ…Í­}¥ˆèÑ…Í­}¥°(€€€€€€€€€€€€‰Í½ÕÉ•}ÅÕ•Éå}Í¡„ÈÔØˆèÑ•áÑ}Í¡„ÈÔØ¡Í½ÕÉ•}ÅÕ•Éä¤°(€€€€€€€€€€€€‰Í½ÕÉ•}¥‘•¹Ñ¥Ñå}Í¡„ÈÔØˆèÍ½ÕÉ•}¥‘•¹Ñ¥Ñä°(€€€€€€€€€€€€‰•¹Õµ•É…Ñ•‘}Í½ÕÉ•Ìˆèl(€€€€€€€€€€€€€€€€‰Á•ÉÍ¥ÍÑ•¹Ñ}ÁÉ½©•Ñ}ÉÕ¹Ìˆ°(€€€€€€€€€€€€€€€€‰…ÁÁÝ½É±‘}±•…å}É½½ÑÍ}…¹‘}‰…­ÕÁÌˆ°(€€€€€€€€€€€€€€€€‰¡¥ÍÑ½É¥…±}Í½ÕÉ•}•áÁ•É¥µ•¹Ñ}½ÕÑÁÕÑÌˆ°(€€€€€€€€€€€€€€€€‰Ñ…Í­}ÍÁ•Í}…¹‘}‘…Ñ…‰…Í•Í}Ù¥…}Ñ…Í­}É½½Ñ}µ…¹¥™•ÍÑÌˆ°(€€€€€€€€€€€€€€€€‰¥Ñ}¡¥ÍÑ½Éäˆ°(€€€€€€€€€€€€€€€€‰¥Ñ}±™Í}½‰©•ÑÌˆ°(€€€€€€€€€€€€€€€€‰É•½É‘•‘}ÑÉ…¹Í™•É}‰Õ¹‘±•}¥¹Ù•¹Ñ½Éäˆ°(€€€€€€€€€€€t°(€€€€€€€€€€€€‰Ñ…Í­}Í¹…ÁÍ¡½Ñ}É½½ÑÌˆèÍ¹…ÁÍ¡½Ñ}É½½ÑÌ°(€€€€€€€€€€€€‰Ñ•áÑ}Í•…É¡}É½ÕÁÌˆèÍ•…É¡}É½ÕÁÌ°(€€€€€€€€€€€€‰¥Ñ}¡¥ÍÑ½Éäˆè¥Ñ}Í•…É °(€€€€€€€€€€€€‰¥Ñ}±™Ìˆè±™Í}Í•…É °(€€€€€€€€€€€€‰ÑÉ…¹Í™•É}‰Õ¹‘±•}¥¹Ù•¹Ñ½ÉäˆèÑÉ…¹Í™•É}Í•…É °(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•}½Õ¹Ðˆè±•¸¡¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•Ì¤°(€€€€€€€€€€€€‰•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½Ñ}½Õ¹Ðˆè±•¸¡•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ¤°(€€€€€€€€€€€€‰½Ñ¡•É}Ñ…Í­}¥‘•¹Ñ¥Ñå}µ…Ñ¡}½Õ¹Ðˆè±•¸¡½Ñ¡•É}Ñ…Í­}µ…Ñ¡•Ì¤°(€€€€€€€€€€€€‰Í½ÕÉ•}½ÉÁÕÍ}½Ñ¡•É}Ñ…Í­}¥‘•¹Ñ¥Ñå}µ…Ñ¡}½Õ¹Ðˆè±•¸¡Í½ÕÉ•}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í­Ì¤°(€€€€€€€€€€€€‰Í½ÕÉ•}½ÉÁÕÍ}½Ñ¡•É}Ñ…Í­}¥‘•¹Ñ¥Ñå}µ…Ñ¡•ÌˆèÍ½ÕÉ•}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í­Ì°(€€€€€€€€€€€€‰¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•Ìˆèl(€€€€€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€€€€€‰Ñ…Í­}¥ˆèÉ½Ýl‰Ñ…Í­}¥‰t°(€€€€€€€€€€€€€€€€€€€€‰É½½Ñ}Á…Ñ¡}Í¡„ÈÔØˆèÑ•áÑ}Í¡„ÈÔØ¡É½Ýl‰É½½Ð‰t¤°(€€€€€€€€€€€€€€€€€€€€‰ÍÁ•}Í¡„ÈÔØˆèÉ½Ýl‰ÍÁ•}Í¡„ÈÔØ‰t°(€€€€€€€€€€€€€€€€€€€€‰Í…µ•}Ñ…Í­}¥ˆèÉ½Ýl‰Ñ…Í­}¥‰t€ôôÑ…Í­}¥°(€€€€€€€€€€€€€€€ô(€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸¥‘•¹Ñ¥Ñå}…¹‘¥‘…Ñ•Ì(€€€€€€€€€€€t°(€€€€€€€€€€€€‰•á…Ñ}¡¥ÍÑ½É¥…±}Í¹…ÁÍ¡½Ñ}™½Õ¹ˆè‰½½°¡•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ¤°(€€€€€€€€€€€€‰Í•…É¡}½µÁ±•Ñ”ˆè‰½½°¡ÑÉ…¹Í™•É}Í•…É¡l‰Í•…É¡}½µÁ±•Ñ”‰t¤°(€€€€€€€€€€€€‰Í•…É¡}É•ÍÕ±Ðˆè€‰•á…Ñ}¡¥ÍÑ½É¥…±}Í¹…ÁÍ¡½Ñ}™½Õ¹ˆ¥˜•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ•±Í”€‰•á…Ñ}¡¥ÍÑ½É¥…±}Í¹…ÁÍ¡½Ñ}¹½Ñ}™½Õ¹ˆ°(€€€€€€€ô(€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰‰½Õ¹‘•‘}Í¹…ÁÍ¡½Ñ}Í•…É ¹©Í½¸ˆ°Í¹…ÁÍ¡½Ñ}Í•…É ¤((€€€€€€€É•½É€ôÉ•½É‘Í}‰å}Ñ…Í­mÑ…Í­}¥‘t(€€€€€€€•Ù¥‘•¹”€ô}ÑÉ…©•Ñ½Éå}¥‘•¹Ñ¥Ñå}•Ù¥‘•¹” (€€€€€€€€€€€É•½É°(€€€€€€€€€€€Í½ÕÉ•}™¥•±‘ÌõÍ½ÕÉ•}™¥•±‘Ì°(€€€€€€€€€€€½™™¥¥…±}™¥•±‘Ìõ½™™¥¥…±}™¥•±‘Ì°(€€€€€€€€¤(€€€€€€€ˆÁ}É½Ü€ô¹•áÐ¡É½Ü™½ÈÉ½Ü¥¸½ÉÁÕÍ}É½ÝÌ¥˜É½Ýl‰Ñ…Í­}¥‰t€ôôÑ…Í­}¥¤(€€€€€€€ÍÕÁ•ÉÙ¥Í½É}½¹±ä€ôÍ•Ð¡ˆÁ}É½Ýl‰µ¥Íµ…Ñ¡•‘}™¥•±‘Ì‰t¤€ôôì‰™¥ÉÍÑ}¹…µ”ˆ°€‰±…ÍÑ}¹…µ”ˆ°€‰•µ…¥°ˆ°€‰Á¡½¹•}¹Õµ‰•È‰ô(€€€€€€€±…ÍÍ¥™¥…Ñ¥½¸€ô±…ÍÍ¥™å}ÁÉ½Ù•¹…¹•}™…¥±ÕÉ” (€€€€€€€€€€€Í½ÕÉ•}±…å•ÉÍ}…É•”õ‰½½°¡ˆÁ}É½Ýl‰Í½ÕÉ•}±…å•ÉÍ}…É•”‰t¤°(€€€€€€€€€€€ÍÕÁ•ÉÙ¥Í½É}½¹±å}µ¥Íµ…Ñ õÍÕÁ•ÉÙ¥Í½É}½¹±ä°(€€€€€€€€€€€•á…Ñ}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í¬õ‰½½°¡½Ñ¡•É}Ñ…Í­}µ…Ñ¡•Ì½ÈÍ½ÕÉ•}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í­Ì¤°(€€€€€€€€€€€Í½ÕÉ•}¥‘•¹Ñ¥Ñå}•Ù¥‘•¹•}½Õ¹Ðõ¥¹Ð¡•Ù¥‘•¹•l‰Í½ÕÉ•}¥‘•¹Ñ¥Ñå}•Ù¥‘•¹•}½Õ¹Ð‰t¤°(€€€€€€€€€€€½™™¥¥…±}¥‘•¹Ñ¥Ñå}•Ù¥‘•¹•}½Õ¹Ðõ¥¹Ð¡•Ù¥‘•¹•l‰½™™¥¥…±}¥‘•¹Ñ¥Ñå}•Ù¥‘•¹•}½Õ¹Ð‰t¤°(€€€€€€€€€€€µ¥á•‘}¥‘•¹Ñ¥Ñå}ÍÑ•Á}½Õ¹Ðõ¥¹Ð¡•Ù¥‘•¹•l‰µ¥á•‘}¥‘•¹Ñ¥Ñå}ÍÑ•Á}½Õ¹Ð‰t¤°(€€€€€€€€€€€•á…Ñ}Í¹…ÁÍ¡½Ñ}™½Õ¹õ‰½½°¡•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ¤°(€€€€€€€€¤(€€€€€€€ˆÁ}•á…µÁ±•Ì€ô•á…µÁ±•Í}‰å}Ñ…Í­mÑ…Í­}¥‘t(€€€€€€€¥˜±•¸¡ˆÁ}•á…µÁ±•Ì¤€„ô¥¹Ð¡•áÁ•Ñ•‘l‰ÅÕ…É…¹Ñ¥¹•‘}‘•¥Í¥½¹}½Õ¹Ð‰t¤è(€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰EÕ…É…¹Ñ¥¹•Ñ…Í¬‘•¥Í¥½¸µ•á…µÁ±”½Õ¹Ð¡…¹•ˆ¤(€€€€€€€…‘©…•¹Ñ}‰å}±¥¹”è‘¥Ñm¥¹Ð°‘¥ÑmÍÑÈ°¹åut€ôíô(€€€€€€€™½È¥¹‘•à°|¥¸ˆÁ}•á…µÁ±•Ìè(€€€€€€€€€€€™½È…¹‘¥‘…Ñ”¥¸É…¹”¡µ…à À°¥¹‘•à€´€Ä¤°µ¥¸¡±•¸¡•á…µÁ±•Ì¤°¥¹‘•à€¬€È¤¤è(€€€€€€€€€€€€€€€•á…µÁ±”€ô•á…µÁ±•Ím…¹‘¥‘…Ñ•t(€€€€€€€€€€€€€€€…‘©…•¹Ñ}‰å}±¥¹•m…¹‘¥‘…Ñ”€¬€Åt€ôì(€€€€€€€€€€€€€€€€€€€€‰±¥¹”ˆè…¹‘¥‘…Ñ”€¬€Ä°(€€€€€€€€€€€€€€€€€€€€‰Ñ…Í­}¥ˆèÍÑÈ¡•á…µÁ±”¹µ•Ñ…‘…Ñ…l‰Ñ…Í­}¥‰t¤°(€€€€€€€€€€€€€€€€€€€€‰ÍÑ…Ñ•}¥‘}Í¡„ÈÔØˆèÑ•áÑ}Í¡„ÈÔØ¡ÍÑ…Ñ•}•á…µÁ±•}¥¡…¹‘¥‘…Ñ”°•á…µÁ±”¤¤°(€€€€€€€€€€€€€€€€€€€€‰¥Í}ˆÁ„á•…•|ÈˆèÍÑÈ¡•á…µÁ±”¹µ•Ñ…‘…Ñ…l‰Ñ…Í­}¥‰t¤€ôôÑ…Í­}¥°(€€€€€€€€€€€€€€€ô(€€€€€€€™½É•¹Í¥Œ€ôì(€€€€€€€€€€€€‰™½Éµ…Ðˆè€‰ˆÁ„á•…•|É}™½É•¹Í¥}ÁÉ½Ù•¹…¹•|Ù Í}ØÄˆ°(€€€€€€€€€€€€‰Ñ…Í­}¥ˆèÑ…Í­}¥°(€€€€€€€€€€€€‰™…¥±ÕÉ•}±…ÍÍ¥™¥…Ñ¥½¸ˆè±…ÍÍ¥™¥…Ñ¥½¸°(€€€€€€€€€€€€‰Í½ÕÉ•}±…å•ÉÍ}…É•”ˆèˆÁ}É½Ýl‰Í½ÕÉ•}±…å•ÉÍ}…É•”‰t°(€€€€€€€€€€€€‰µ¥Íµ…Ñ¡•‘}™¥•±‘ÌˆèˆÁ}É½Ýl‰µ¥Íµ…Ñ¡•‘}™¥•±‘Ì‰t°(€€€€€€€€€€€€‰½™™¥¥…±}…¹‘}‰…­ÕÁ}…É•”ˆèˆÁ}É½Ýl‰½™™¥¥…±}‰…­ÕÁ}…É•”‰t°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±”ˆèˆÁ}É½Ýl‰Í½ÕÉ•}Á…Ñ ‰t°(€€€€€€€€€€€€‰Í½ÕÉ•}™¥±•}Í¡„ÈÔØˆèˆÁ}É½Ýl‰Í½ÕÉ•}™¥±•}Í¡„ÈÔØ‰t°(€€€€€€€€€€€€‰Í½ÕÉ•}ÅÕ•Éå}±¥¹•}ÍÁ…¸ˆèˆÁ}É½Ýl‰Í½ÕÉ•}ÅÕ•Éå}±¥¹•}ÍÁ…¸‰t°(€€€€€€€€€€€€‰‘•¥Í¥½¹}•á…µÁ±•}±¥¹•Ìˆèm¥¹‘•à€¬€Ä™½È¥¹‘•à°|¥¸ˆÁ}•á…µÁ±•Ít°(€€€€€€€€€€€€‰…‘©…•¹Ñ}‘•¥Í¥½¹}É•½É‘Ìˆèm…‘©…•¹Ñ}‰å}±¥¹•m±¥¹•t™½È±¥¹”¥¸Í½ÉÑ•¡…‘©…•¹Ñ}‰å}±¥¹”¥t°(€€€€€€€€€€€€‰ÑÉ…©•Ñ½Éå}¥‘•¹Ñ¥Ñå}•Ù¥‘•¹”ˆè•Ù¥‘•¹”°(€€€€€€€€€€€€‰µ…Ñ¡•Í}½Ñ¡•É}½™™¥¥…±}Ñ…Í¬ˆè‰½½°¡½Ñ¡•É}Ñ…Í­}µ…Ñ¡•Ì¤°(€€€€€€€€€€€€‰µ…Ñ¡¥¹}½Ñ¡•É}Ñ…Í­}¥‘ÌˆèÍ½ÉÑ•¡íÉ½Ýl‰Ñ…Í­}¥‰t™½ÈÉ½Ü¥¸½Ñ¡•É}Ñ…Í­}µ…Ñ¡•Íô¤°(€€€€€€€€€€€€‰µ…Ñ¡•Í}½Ñ¡•É}Í½ÕÉ•}½ÉÁÕÍ}Ñ…Í¬ˆè‰½½°¡Í½ÕÉ•}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í­Ì¤°(€€€€€€€€€€€€‰µ…Ñ¡¥¹}½Ñ¡•É}Í½ÕÉ•}½ÉÁÕÍ}Ñ…Í­}¥‘ÌˆèÍ½ÕÉ•}¥‘•¹Ñ¥Ñå}µ…Ñ¡•Í}½Ñ¡•É}Ñ…Í­Ì°(€€€€€€€€€€€€‰•á…Ñ}Í¹…ÁÍ¡½Ñ}™½Õ¹ˆè‰½½°¡•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ¤°(€€€€€€€€€€€€‰Í¹…ÁÍ¡½Ñ}Í•…É¡}É•ÍÕ±ÐˆèÍ¹…ÁÍ¡½Ñ}Í•…É¡l‰Í•…É¡}É•ÍÕ±Ð‰t°(€€€€€€€€€€€€‰É…Ý}¥‘•¹Ñ¥Ñå}Ù…±Õ•Í}É•‘…Ñ•ˆèQÉÕ”°(€€€€€€€ô(€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ˆÁ„á•…•|É}™½É•¹Í¥}ÁÉ½Ù•¹…¹”¹©Í½¸ˆ°™½É•¹Í¥Œ¤((€€€€€€€ÍÁ±¥Ð€ô}±½…‘}©Í½¸¡Á…Ñ¡Íl‰ÍÁ±¥Ñ}µ…¹¥™•ÍÐ‰t¤(€€€€€€€ÑÉ…¥¹}±…‰•±}Ñ…Í­Ì€ôì(€€€€€€€€€€€ÍÑÈ¡É½Ýl‰Ñ…Í­}¥‰t¤(€€€€€€€€€€€™½ÈÉ½Ü¥¸}±½…‘}©Í½¹°¡Á…Ñ¡Íl‰ÍÑ…•}‰}±…‰•±Ì‰t¤(€€€€€€€€€€€¥˜ÍÑÈ¡É½Ýl‰ÍÁ±¥Ð‰t¤€ôô€‰ÑÉ…¥¸ˆ(€€€€€€€ô(€€€€€€€Ñ•…¡•É}Í½ÕÉ•}Ñ…Í­Ì€ôì(€€€€€€€€€€€ÍÑÈ¡É½Ýl‰Ñ…Í­}¥‰t¤(€€€€€€€€€€€™½ÈÉ½Ü¥¸}±½…‘}©Í½¹°¡Á…Ñ¡Íl‰•™™•Ñ¥Ù•}µ•µ½Éå}‰…¹¬‰t¤(€€€€€€€€€€€¥˜‰½½°¡É½Ü¹•Ð ‰•±¥¥‰±•}™½É}ÍÑ…•}ˆˆ¤¤(€€€€€€€ô(€€€€€€€½¹Ñ…µ¥¹…Ñ¥½¸€ôÑÉ…¥¹¥¹}½¹Ñ…µ¥¹…Ñ¥½¹}É•Á½ÉÐ (€€€€€€€€€€€Ñ…Í­}¥õÑ…Í­}¥°(€€€€€€€€€€€ÑÉ…¥¹}Ñ…Í­}¥‘ÌõÍÁ±¥Ñl‰ÑÉ…¥¹}Ñ…Í­}¥‘Ì‰t°(€€€€€€€€€€€ÑÉ…¹Í¥Ñ¥½¹}Á…É•¹Ñ}Ñ…Í­}¥‘ÌõÑÉ…¹Í¥Ñ¥½¹}Á…É•¹Ñ}Ñ…Í­}¥‘Ì°(€€€€€€€€€€€ÑÉ…¥¹}±…‰•±}Ñ…Í­}¥‘ÌõÍ½ÉÑ•¡ÑÉ…¥¹}±…‰•±}Ñ…Í­Ì¤°(€€€€€€€€€€€Ñ•…¡•É}Í½ÕÉ•}Ñ…Í­}¥‘ÌõÍ½ÉÑ•¡Ñ•…¡•É}Í½ÕÉ•}Ñ…Í­Ì¤°(€€€€€€€€¤(€€€€€€€½¹Ñ…µ¥¹…Ñ¥½¸¹ÕÁ‘…Ñ” (€€€€€€€€€€€ì(€€€€€€€€€€€€€€€€‰ÍÑ…•}‰}ÍÁ±¥Ðˆè€‰Ù…±¥‘…Ñ¥½¸ˆ¥˜Ñ…Í­}¥¥¸Í•Ð¡ÍÁ±¥Ñl‰Ù…±¥‘…Ñ¥½¹}Ñ…Í­}¥‘Ì‰t¤•±Í”€‰Õ¹­¹½Ý¸ˆ°(€€€€€€€€€€€€€€€€‰‘•¥Í¥½¹}•á…µÁ±•}½Õ¹Ðˆè±•¸¡ˆÁ}•á…µÁ±•Ì¤°(€€€€€€€€€€€€€€€€‰•áÀÀÈÁ}ÅÕ•Éå}½Õ¹Ðˆè•áÀÀÈÁ}‰å}Ñ…Í­mÑ…Í­}¥‘t°(€€€€€€€€€€€€€€€€‰•áÀÀÈÑ…}…Õ‘¥Ñ}ÍÑ…Ñ•}½Õ¹Ðˆè•áÀÀÈÑ…}‰å}Ñ…Í­mÑ…Í­}¥‘t°(€€€€€€€€€€€ô(€€€€€€€€¤(€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÑÉ…¥¹¥¹}½¹Ñ…µ¥¹…Ñ¥½¹}…Õ‘¥Ð¹©Í½¸ˆ°½¹Ñ…µ¥¹…Ñ¥½¸¤((€€€€€€€‰É…¹ €ôÍ•±•Ñ}ÁÉ•™±¥¡Ñ}‰É…¹  (€€€€€€€€€€€µ¥Íµ…Ñ¡}Ñ…Í­}½Õ¹Ðõ¥¹Ð¡½ÉÁÕÍl‰¥‘•¹Ñ¥Ñå}µ¥Íµ…Ñ¡}½Õ¹Ð‰t¤°(€€€€€€€€€€€•á…Ñ}Í¹…ÁÍ¡½Ñ}™½Õ¹õ‰½½°¡•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ¤°(€€€€€€€€€€€ÑÉ…¥¹¥¹}½¹Ñ…µ¥¹…Ñ•õ‰½½°¡½¹Ñ…µ¥¹…Ñ¥½¹l‰½¹Ñ…µ¥¹…Ñ•Í}ÑÉ…¥¹¥¹œ‰t¤°(€€€€€€€€¤(€€€€€€€ÅÕ…É…¹Ñ¥¹”€ô9½¹”(€€€€€€€ÅÕ…É…¹Ñ¥¹•}Í•¹Ñ¥¹•°€ô9½¹”(€€€€€€€¥˜‰É…¹ €ôô€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}Ñ…Í­}ÅÕ…É…¹Ñ¥¹•}É•…‘äˆè(€€€€€€€€€€€ÅÕ…É…¹Ñ¥¹”€ô‰Õ¥±‘}ÅÕ…É…¹Ñ¥¹•}µ…¹¥™•ÍÐ¡•áÀÀÈÑ…}É½ÝÌ°ÅÕ…É…¹Ñ¥¹•‘}Ñ…Í­}¥õÑ…Í­}¥¤(€€€€€€€€€€€¥˜ÅÕ…É…¹Ñ¥¹•l‰É•Ñ…¥¹•‘}ÍÑ…Ñ•}½Õ¹Ð‰t€„ô¥¹Ð¡•áÁ•Ñ•‘l‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}ÍÑ…Ñ•Ì‰t¤½ÈÅÕ…É…¹Ñ¥¹•l‰É•Ñ…¥¹•‘}Ñ…Í­}½Õ¹Ð‰t€„ô¥¹Ð¡•áÁ•Ñ•‘l‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}Ñ…Í­Ì‰t¤è(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰AÉ½Ù•¹…¹”µÙ…±¥ÅÕ…É…¹Ñ¥¹”µ…¹¥™•ÍÐ½Õ¹Ð‘¥™™•ÉÌˆ¤(€€€€€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}½¹•}ÍÑ•Á}µ…¹¥™•ÍÑ}ØÄ¹©Í½¸ˆ°ÅÕ…É…¹Ñ¥¹”¤(€€€€€€€€€€€Á…É•¹Ñ}Í•¹Ñ¥¹•°€ô}±½…‘}©Í½¸¡Á…Ñ¡Íl‰•áÀÀÈÑÉ}Í•¹Ñ¥¹•°‰t¤(€€€€€€€€€€€ÅÕ…É…¹Ñ¥¹•}Í•¹Ñ¥¹•°€ô‰Õ¥±‘}ÅÕ…É…¹Ñ¥¹•}Í•¹Ñ¥¹•° (€€€€€€€€€€€€€€€}µ…¹¥™•ÍÑ}É½ÝÌ¡Á…É•¹Ñ}Í•¹Ñ¥¹•°¤°(€€€€€€€€€€€€€€€É•Ñ…¥¹•‘}ÍÑ…Ñ•}¥‘ÌõmÉ½Ýl‰ÍÑ…Ñ•}•á…µÁ±•}¥‰t™½ÈÉ½Ü¥¸ÅÕ…É…¹Ñ¥¹•l‰É½ÝÌ‰ut°(€€€€€€€€€€€€€€€ÅÕ…É…¹Ñ¥¹•‘}Ñ…Í­}¥õÑ…Í­}¥°(€€€€€€€€€€€€¤(€€€€€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}Í•¹Ñ¥¹•±}µ…¹¥™•ÍÐ¹©Í½¸ˆ°ÅÕ…É…¹Ñ¥¹•}Í•¹Ñ¥¹•°¤(€€€€€€€€€€€‰…Í•}½¹ÑÉ…ÑÌ€ô}±½…‘}©Í½¸¡½¹ÑÉ…Ñ}µ…¹¥™•ÍÑ}Á…Ñ ¤(€€€€€€€€€€€É•Ñ…¥¹•‘}¥‘Ì€ôíÍÑÈ¡É½Ýl‰ÍÑ…Ñ•}•á…µÁ±•}¥‰t¤™½ÈÉ½Ü¥¸ÅÕ…É…¹Ñ¥¹•l‰É½ÝÌ‰uô(€€€€€€€€€€€½¹ÑÉ…Ñ}É½ÝÌ€ôl(€€€€€€€€€€€€€€€‘¥Ð¡É½Ü¤(€€€€€€€€€€€€€€€™½ÈÉ½Ü¥¸}µ…¹¥™•ÍÑ}É½ÝÌ¡‰…Í•}½¹ÑÉ…ÑÌ¤(€€€€€€€€€€€€€€€¥˜ÍÑÈ¡É½Ýl‰ÍÑ…Ñ•}•á…µÁ±•}¥‰t¤¥¸É•Ñ…¥¹•‘}¥‘Ì(€€€€€€€€€€€t(€€€€€€€€€€€¥˜±•¸¡½¹ÑÉ…Ñ}É½ÝÌ¤€„ô¥¹Ð¡•áÁ•Ñ•‘l‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}ÍÑ…Ñ•Ì‰t¤è(€€€€€€€€€€€€€€€É…¥Í”Y…±Õ•ÉÉ½È ‰¥±Ñ•É•É•Á±…ä½¹ÑÉ…Ð½Õ¹Ð‘¥™™•ÉÌˆ¤(€€€€€€€€€€€½¹ÑÉ…Ñ}Á…å±½…€ôì(€€€€€€€€€€€€€€€€‰™½Éµ…Ðˆè€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}É•Á±…å}½¹ÑÉ…Ñ}µ…¹¥™•ÍÑ|Ù Í}ØÄˆ°(€€€€€€€€€€€€€€€€‰Í½ÕÉ•}µ…¹¥™•ÍÑ}Í¡„ÈÔØˆèÍ¡„ÈÔÙ}™¥±”¡½¹ÑÉ…Ñ}µ…¹¥™•ÍÑ}Á…Ñ ¤°(€€€€€€€€€€€€€€€€‰ÅÕ…É…¹Ñ¥¹•‘}Ñ…Í­}¥ˆèÑ…Í­}¥°(€€€€€€€€€€€€€€€€‰É½Ý}½Õ¹Ðˆè±•¸¡½¹ÑÉ…Ñ}É½ÝÌ¤°(€€€€€€€€€€€€€€€€‰É½ÝÌˆè½¹ÑÉ…Ñ}É½ÝÌ°(€€€€€€€€€€€ô(€€€€€€€€€€€½¹ÑÉ…Ñ}Á…å±½…‘l‰µ…¹¥™•ÍÑ}Í¡„ÈÔØ‰t€ô…¹½¹¥…±}¡…Í ¡½¹ÑÉ…Ñ}Á…å±½…¤(€€€€€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}É•Á±…å}½¹ÑÉ…Ñ}µ…¹¥™•ÍÐ¹©Í½¸ˆ°½¹ÑÉ…Ñ}Á…å±½…¤((€€€€€€€‘•¥Í¥½¸€ôì(€€€€€€€€€€€€‰™½Éµ…Ðˆè€‰…ÁÁÝ½É±‘}ÁÉ½Ù•¹…¹•}ÁÉ•™±¥¡Ñ}‘•¥Í¥½¹|Ù Í}ØÄˆ°(€€€€€€€€€€€€‰‘•¥Í¥½¹}‰É…¹ ˆè‰É…¹ °(€€€€€€€€€€€€‰½ÉÁÕÍ}¥‘•¹Ñ¥Ñå}µ¥Íµ…Ñ¡}Ñ…Í­}½Õ¹Ðˆè½ÉÁÕÍl‰¥‘•¹Ñ¥Ñå}µ¥Íµ…Ñ¡}½Õ¹Ð‰t°(€€€€€€€€€€€€‰™…¥±ÕÉ•}±…ÍÍ¥™¥…Ñ¥½¸ˆè±…ÍÍ¥™¥…Ñ¥½¸°(€€€€€€€€€€€€‰Í¹…ÁÍ¡½Ñ}Í•…É¡}É•ÍÕ±ÐˆèÍ¹…ÁÍ¡½Ñ}Í•…É¡l‰Í•…É¡}É•ÍÕ±Ð‰t°(€€€€€€€€€€€€‰Í¹…ÁÍ¡½Ñ}™½Õ¹ˆè‰½½°¡•á…Ñ}Ñ…Í­}Í¹…ÁÍ¡½ÑÌ¤°(€€€€€€€€€€€€‰ÑÉ…¥¹¥¹}½¹Ñ…µ¥¹…Ñ•ˆè‰½½°¡½¹Ñ…µ¥¹…Ñ¥½¹l‰½¹Ñ…µ¥¹…Ñ•Í}ÑÉ…¥¹¥¹œ‰t¤°(€€€€€€€€€€€€‰ÅÕ…É…¹Ñ¥¹•}…±±½Ý•ˆè‰É…¹ €ôô€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}Ñ…Í­}ÅÕ…É…¹Ñ¥¹•}É•…‘äˆ°(€€€€€€€€€€€€‰É•Á±…å}µ½‘”ˆè€ (€€€€€€€€€€€€€€€€‰½É¥¥¹…±|ÐÕ}•á…Ñ}Í¹…ÁÍ¡½Ðˆ(€€€€€€€€€€€€€€€¥˜‰É…¹ €ôô€‰•á…Ñ}¡¥ÍÑ½É¥…±}Í¹…ÁÍ¡½Ñ}™½Õ¹‘}Á•¹‘¥¹}É•Á±…äˆ(€€€€€€€€€€€€€€€•±Í”€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘|ÐÁ}ÍÑ…Ñ•}ÅÕ…É…¹Ñ¥¹”ˆ(€€€€€€€€€€€€€€€¥˜‰É…¹ €ôô€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}Ñ…Í­}ÅÕ…É…¹Ñ¥¹•}É•…‘äˆ(€€€€€€€€€€€€€€€•±Í”€‰‰±½­•ˆ(€€€€€€€€€€€€¤°(€€€€€€€€€€€€‰É•Á±…å}…±±½Ý•ˆè‰É…¹ ¥¸ì‰•á…Ñ}¡¥ÍÑ½É¥…±}Í¹…ÁÍ¡½Ñ}™½Õ¹‘}Á•¹‘¥¹}É•Á±…äˆ°€‰ÁÉ½Ù•¹…¹•}Ù…±¥‘}Ñ…Í­}ÅÕ…É…¹Ñ¥¹•}É•…‘ä‰ô°(€€€€€€€€€€€€‰ÅÕ…É…¹Ñ¥¹•}µ…¹¥™•ÍÑ}Í¡„ÈÔØˆèÅÕ…É…¹Ñ¥¹”¹•Ð ‰µ…¹¥™•ÍÑ}Í¡„ÈÔØˆ¤¥˜ÅÕ…É…¹Ñ¥¹”•±Í”9½¹”°(€€€€€€€€€€€€‰Í•¹Ñ¥¹•±}µ…¹¥™•ÍÑ}Í¡„ÈÔØˆèÅÕ…É…¹Ñ¥¹•}Í•¹Ñ¥¹•°¹•Ð ‰µ…¹¥™•ÍÑ}Í¡„ÈÔØˆ¤¥˜ÅÕ…É…¹Ñ¥¹•}Í•¹Ñ¥¹•°•±Í”9½¹”°(€€€€€€€€€€€€‰Í¥•¹Ñ¥™¥}Á…É…µ•Ñ•É}¡…¹•ˆè…±Í”°(€€€€€€€€€€€€‰ÅÝ•¹}¥µÁ½ÉÑ}½É}™½ÉÝ…É‘}½Õ¹Ðˆè€À°(€€€€€€€ô(€€€€€€€…Ñ½µ¥}ÝÉ¥Ñ•}©Í½¸¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ•™±¥¡Ñ}‘•¥Í¥½¸¹©Í½¸ˆ°‘•¥Í¥½¸¤(€€€€€€€…ÑÑ•µÁÐ¹ÁÉ½É•ÍÌ¡±…Ñ•ÍÑ}Ù…±¥‘…Ñ•‘}¡•­Á½¥¹ÐõÍÑÈ¡…ÉÌ¹…ÉÑ¥™…Ñ}‘¥È€¼€‰ÁÉ•™±¥¡Ñ}‘•¥Í¥½¸¹©Í½¸ˆ¤¤(€€€€€€€ÁÉ¥¹Ð¡©Í½¸¹‘ÕµÁÌ¡‘•¥Í¥½¸°¥¹‘•¹ÐôÈ°Í½ÉÑ}­•åÌõQÉÕ”¤¤(()¥˜}}¹…µ•}|€ôô€‰}}µ…¥¹}|ˆè(€€€µ…¥¸ ¤