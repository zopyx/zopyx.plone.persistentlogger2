"""Deterministic JSON normalization, redaction, and digest primitives."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable
from uuid import UUID

from .errors import ValidationError

REDACTED = "[REDACTED]"
DEFAULT_SENSITIVE = frozenset({
    "password", "passwd", "secret", "token", "api_key", "authorization",
    "cookie", "session", "private_key",
})


def _key_matches(key: str, patterns: Iterable[str]) -> bool:
    candidate = re.sub(r"[-\s]+", "_", key.casefold())
    return any(pattern.casefold() in candidate for pattern in patterns)


def normalize(value: Any, *, redact: bool = True,
              sensitive_keys: Iterable[str] = DEFAULT_SENSITIVE) -> Any:
    """Return JSON-compatible data while preserving list/dict shape."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("details cannot contain non-finite numbers")
        return value
    if isinstance(value, (datetime, date, UUID, Enum)):
        return value.value if isinstance(value, Enum) else str(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("detail keys must be strings")
            result[key] = (REDACTED if redact and _key_matches(key, sensitive_keys)
                          else normalize(item, redact=redact,
                                         sensitive_keys=sensitive_keys))
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [normalize(item, redact=redact, sensitive_keys=sensitive_keys)
                for item in value]
    raise ValidationError(f"unsupported detail value: {type(value).__name__}")


def canonical(value: Any) -> str:
    """Serialize normalized data with one stable representation."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError("value is not canonical JSON") from exc


def bounded_details(value: Any, limit: int = 64 * 1024,
                    *, sensitive_keys: Iterable[str] = DEFAULT_SENSITIVE) -> Any:
    normalized = normalize(value, sensitive_keys=sensitive_keys)
    if len(canonical(normalized).encode("utf-8")) > limit:
        raise ValidationError(f"details exceed the configured byte limit of {limit}")
    return normalized


def digest(value: Mapping[str, Any]) -> str:
    """Hash event evidence, excluding mutable storage fields."""
    evidence = {key: item for key, item in value.items()
                if key not in {"integrity_digest", "write_status", "storage_status"}}
    return hashlib.sha256(canonical(evidence).encode("utf-8")).hexdigest()


def json_bytes(value: Any) -> bytes:
    return canonical(normalize(value, redact=False)).encode("utf-8")
