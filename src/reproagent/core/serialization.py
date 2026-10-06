from __future__ import annotations

import hashlib
import json
import math
import types
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Union, get_args, get_origin, get_type_hints

from . import models

REGISTRY = {
    "task": models.TaskResult, "request": models.TaskRequest,
    "contracts": models.IssueContract, "snapshots": models.CodeSnapshot,
    "environments": models.EnvironmentSnapshot, "candidates": models.Candidate,
    "runs": models.ExecutionResult, "verdicts": models.Verdict,
    "events": models.TaskEvent, "manifest": models.ArtifactManifest,
}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def parse_json(text: str) -> dict[str, Any]:
    def invalid(value):
        raise ValueError(f"non-finite JSON value: {value}")
    data = json.loads(text, object_pairs_hook=_pairs, parse_constant=invalid)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    return data


def _plain(obj):
    if is_dataclass(obj):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (tuple, list)):
        return [_plain(v) for v in obj]
    if isinstance(obj, dict):
        if not all(isinstance(k, str) for k in obj):
            raise TypeError("JSON keys must be strings")
        return {k: _plain(v) for k, v in obj.items()}
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float) and math.isfinite(obj):
        return obj
    raise TypeError(f"not a JSON value: {type(obj).__name__}")


def canonical_bytes(data: dict[str, Any]) -> bytes:
    return json.dumps(_plain(data), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def canonical_hash(data: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(data)).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode_record(obj) -> dict[str, Any]:
    if type(obj) not in REGISTRY.values():
        raise TypeError(f"record type cannot be persisted: {type(obj).__name__}")
    return {"schema_version": 1, **_plain(obj)}


def _convert(tp, value, path):
    if tp is Any:
        return value
    origin, args = get_origin(tp), get_args(tp)
    if origin in (Union, types.UnionType):
        for option in args:
            try:
                return _convert(option, value, path)
            except (ValueError, TypeError):
                pass
        raise ValueError(f"invalid {path}")
    if tp is type(None):
        if value is not None:
            raise ValueError(f"{path} must be null")
        return None
    if origin in (tuple, list):
        if not isinstance(value, list):
            raise ValueError(f"{path} must be an array")
        result = [_convert(args[0], v, path) for v in value]
        return tuple(result) if origin is tuple else result
    if origin is dict:
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        return {k: _convert(args[1], v, f"{path}.{k}") for k, v in value.items()}
    if tp is Path:
        if not isinstance(value, str):
            raise ValueError(f"{path} must be a path string")
        return Path(value)
    if isinstance(tp, type) and issubclass(tp, Enum):
        return tp(value)
    if isinstance(tp, type) and is_dataclass(tp):
        if not isinstance(value, dict):
            raise ValueError(f"{path} must be an object")
        hints = get_type_hints(tp)
        extras = set(value) - set(hints)
        if extras:
            raise ValueError(f"unknown {path} fields: {sorted(extras)}")
        try:
            return tp(**{k: _convert(hints[k], v, f"{path}.{k}") for k, v in value.items()})
        except TypeError as exc:
            raise ValueError(f"invalid {path}: {exc}") from exc
    if tp in (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (tp is int and not isinstance(value, int)):
            raise ValueError(f"{path} must be a finite {tp.__name__}")
        return tp(value)
    if tp in (str, bool):
        if not isinstance(value, tp):
            raise ValueError(f"{path} must be {tp.__name__}")
        return value
    raise ValueError(f"unsupported field type at {path}: {tp}")


def decode_record(kind: str, data: dict[str, Any]):
    if data.get("schema_version") != 1 or isinstance(data.get("schema_version"), bool):
        raise ValueError("unsupported schema_version")
    if kind not in REGISTRY:
        raise ValueError(f"unknown record kind: {kind}")
    return _convert(REGISTRY[kind], {k: v for k, v in data.items() if k != "schema_version"}, kind)
