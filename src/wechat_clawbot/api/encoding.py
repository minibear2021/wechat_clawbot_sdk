from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any


def to_wire_dict(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, dict):
        encoded: dict[str, Any] = {}
        for key, item in value.items():
            wire_item = to_wire_dict(item)
            if wire_item is not None:
                encoded[key] = wire_item
        return encoded
    if isinstance(value, list):
        encoded_items: list[Any] = []
        for item in value:
            wire_item = to_wire_dict(item)
            if wire_item is not None:
                encoded_items.append(wire_item)
        return encoded_items
    if isinstance(value, Enum):
        return value.value
    return value
