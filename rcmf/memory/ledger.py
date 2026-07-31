from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import torch
from safetensors.torch import load_file, save_file

from rcmf.memory.state import MemoryDelta, MemoryState
from rcmf.schemas import MemoryRecord
from rcmf.utils.serialization import append_jsonl, read_jsonl, sha256_file, sha256_text


@dataclass
class LedgerEvent:
    event_id: str
    action: str
    memory_id: str
    status: str
    created_at: str
    benchmark: str | None = None
    episode_id: str | None = None
    task_id: str | None = None
    raw_trajectory: dict[str, Any] = field(default_factory=dict)
    experience_text: str = ""
    outcome: float = 0.0
    success: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
    compiler_version: str = ""
    checksum: str = ""
    delta_path: str = ""
    supersedes: str | None = None
    replaced_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> "LedgerEvent":
        return cls(
            event_id=str(values["event_id"]),
            action=str(values["action"]),
            memory_id=str(values["memory_id"]),
            status=str(values["status"]),
            created_at=str(values["created_at"]),
            benchmark=values.get("benchmark"),
            episode_id=values.get("episode_id"),
            task_id=values.get("task_id"),
            raw_trajectory=dict(values.get("raw_trajectory", {})),
            experience_text=str(values.get("experience_text", "")),
            outcome=float(values.get("outcome", 0.0)),
            success=bool(values.get("success", False)),
            metadata=dict(values.get("metadata", {})),
            compiler_version=str(values.get("compiler_version", "")),
            checksum=str(values.get("checksum", "")),
            delta_path=str(values.get("delta_path", "")),
            supersedes=values.get("supersedes"),
            replaced_by=values.get("replaced_by"),
        )


class MemoryLedger:
    """Append-only metadata ledger with safetensors delta shards."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.delta_dir = self.root / "deltas"
        self.events_path = self.root / "ledger.jsonl"
        self.delta_dir.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _delta_path(self, memory_id: str) -> Path:
        return self.delta_dir / f"{memory_id}.safetensors"

    def _write_delta(self, delta: MemoryDelta) -> tuple[Path, str]:
        path = self._delta_path(delta.memory_id)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        save_file(
            {
                "delta_v": delta.delta_v.detach().cpu().to(dtype=torch.float32),
                "delta_c": delta.delta_c.detach().cpu().to(dtype=torch.float32),
            },
            tmp_path,
            metadata={"memory_id": delta.memory_id},
        )
        tmp_path.replace(path)
        return path, sha256_file(path)

    def load_delta(self, memory_id: str, event: LedgerEvent | None = None) -> MemoryDelta:
        if event is None:
            event = self.active_events()[memory_id]
        path = self.root / event.delta_path if not Path(event.delta_path).is_absolute() else Path(event.delta_path)
        tensors = load_file(path)
        delta = MemoryDelta(
            memory_id=memory_id,
            delta_v=tensors["delta_v"],
            delta_c=tensors["delta_c"],
            metadata={"ledger_event_id": event.event_id},
        )
        actual_checksum = sha256_file(path)
        if event.checksum and actual_checksum != event.checksum:
            raise ValueError(f"Delta checksum mismatch for {memory_id}: {path}")
        return delta

    def append_event(self, event: LedgerEvent) -> None:
        append_jsonl(self.events_path, event.to_dict())

    def events(self) -> list[LedgerEvent]:
        return [LedgerEvent.from_dict(row) for row in read_jsonl(self.events_path)]

    def active_events(self) -> dict[str, LedgerEvent]:
        active: dict[str, LedgerEvent] = {}
        for event in self.events():
            if event.action in {"ADD", "REPLACE_ADD"} and event.status == "active":
                active[event.memory_id] = event
            elif event.action in {"DELETE", "SUPERSEDE"}:
                active.pop(event.memory_id, None)
        return active

    def add_record(
        self,
        record: MemoryRecord,
        delta: MemoryDelta,
        state: MemoryState | None = None,
        compiler_version: str = "rcmf-v1",
        action: str = "ADD",
        supersedes: str | None = None,
    ) -> LedgerEvent:
        if record.memory_id != delta.memory_id:
            raise ValueError("record.memory_id and delta.memory_id must match")
        delta_path, delta_checksum = self._write_delta(delta)
        event = LedgerEvent(
            event_id=str(uuid4()),
            action=action,
            memory_id=record.memory_id,
            status="active",
            created_at=self._now(),
            benchmark=record.benchmark,
            episode_id=record.episode_id,
            task_id=record.task_id,
            raw_trajectory=record.raw_trajectory,
            experience_text=record.experience_text,
            outcome=record.outcome,
            success=record.success,
            metadata=dict(record.metadata),
            compiler_version=compiler_version,
            checksum=delta_checksum,
            delta_path=str(delta_path.relative_to(self.root)),
            supersedes=supersedes,
        )
        self.append_event(event)
        if state is not None:
            state.add(delta)
        return event

    def delete(self, memory_id: str, state: MemoryState | None = None) -> LedgerEvent:
        active = self.active_events()
        if memory_id not in active:
            raise KeyError(f"Cannot delete inactive memory: {memory_id}")
        event = active[memory_id]
        if state is not None:
            state.remove(self.load_delta(memory_id, event))
        tombstone = LedgerEvent(
            event_id=str(uuid4()),
            action="DELETE",
            memory_id=memory_id,
            status="deleted",
            created_at=self._now(),
            benchmark=event.benchmark,
            episode_id=event.episode_id,
            task_id=event.task_id,
            metadata={"deleted_event_id": event.event_id},
        )
        self.append_event(tombstone)
        return tombstone

    def replace(
        self,
        memory_id: str,
        new_record: MemoryRecord,
        new_delta: MemoryDelta,
        state: MemoryState | None = None,
        compiler_version: str = "rcmf-v1",
    ) -> tuple[LedgerEvent, LedgerEvent]:
        active = self.active_events()
        if memory_id not in active:
            raise KeyError(f"Cannot replace inactive memory: {memory_id}")
        old_event = active[memory_id]
        if state is not None:
            state.remove(self.load_delta(memory_id, old_event))
        supersede = LedgerEvent(
            event_id=str(uuid4()),
            action="SUPERSEDE",
            memory_id=memory_id,
            status="superseded",
            created_at=self._now(),
            benchmark=old_event.benchmark,
            episode_id=old_event.episode_id,
            task_id=old_event.task_id,
            metadata={"superseded_event_id": old_event.event_id},
            replaced_by=new_record.memory_id,
        )
        self.append_event(supersede)
        add_event = self.add_record(
            new_record,
            new_delta,
            state=state,
            compiler_version=compiler_version,
            action="REPLACE_ADD",
            supersedes=memory_id,
        )
        return supersede, add_event

    def rebuild_state(
        self,
        rank: int,
        program_dim: int,
        device: torch.device | str | None = None,
    ) -> MemoryState:
        state = MemoryState(rank=rank, program_dim=program_dim, device=device)
        for memory_id, event in self.active_events().items():
            state.add(self.load_delta(memory_id, event))
        return state

    def iter_records(self, active_only: bool = True) -> Iterable[MemoryRecord]:
        events = self.active_events().values() if active_only else self.events()
        for event in events:
            yield MemoryRecord(
                memory_id=event.memory_id,
                benchmark=event.benchmark or "",
                episode_id=event.episode_id or "",
                task_id=event.task_id or "",
                raw_trajectory=event.raw_trajectory,
                experience_text=event.experience_text,
                outcome=event.outcome,
                success=event.success,
                metadata=dict(event.metadata),
            )

    @staticmethod
    def record_checksum(record: MemoryRecord) -> str:
        return sha256_text(record.experience_text)

