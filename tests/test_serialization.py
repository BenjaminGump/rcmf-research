from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from rcmf.utils.serialization import to_jsonable


class ToyEnum(Enum):
    VALUE = "value"


class PlainObject:
    def __init__(self) -> None:
        self.public = "ok"
        self._private = "hidden"


class PydanticLike:
    def dict(self) -> dict[str, object]:
        return {"nested": PlainObject(), "path": Path("x/y")}


def test_to_jsonable_handles_common_object_shapes() -> None:
    value = {
        "plain": PlainObject(),
        "model": PydanticLike(),
        "enum": ToyEnum.VALUE,
        "set": {"b", "a"},
    }

    converted = to_jsonable(value)

    assert converted == {
        "plain": {"public": "ok"},
        "model": {"nested": {"public": "ok"}, "path": str(Path("x/y"))},
        "enum": "value",
        "set": ["a", "b"],
    }
    json.dumps(converted)
