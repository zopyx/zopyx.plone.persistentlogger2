"""Backend-neutral, validated audit domain models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from .errors import ValidationError
from .serialization import bounded_details


class Severity(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


def utc(value: datetime, field_name: str = "timestamp") -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _bounded_text(name: str, value: Any, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValidationError(f"{name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class LogEvent:
    comment: str
    object_uid: str
    event_type: str = "application"
    severity: Severity | str = Severity.INFO
    actor: str = "system"
    target: str | None = None
    info_url: str | None = None
    details: Any = None
    occurred_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    event_id: UUID = field(default_factory=uuid4)
    schema_version: int = 1

    def __post_init__(self) -> None:
        _bounded_text("object_uid", self.object_uid, 1024, required=True)
        _bounded_text("comment", self.comment, 4000, required=True)
        _bounded_text("event_type", self.event_type, 100, required=True)
        _bounded_text("actor", self.actor, 255, required=True)
        if self.target is not None:
            _bounded_text("target", self.target, 2048)
        if not isinstance(self.event_id, UUID):
            raise ValidationError("event_id must be a UUID")
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int) or self.schema_version <= 0:
            raise ValidationError("schema_version must be a positive integer")
        try:
            severity = Severity("warning" if str(self.severity).casefold() == "warn" else str(self.severity).casefold())
        except ValueError as exc:
            raise ValidationError("invalid severity") from exc
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "created_at", utc(self.created_at, "created_at"))
        if self.occurred_at is not None:
            object.__setattr__(self, "occurred_at", utc(self.occurred_at, "occurred_at"))
        if self.info_url is not None:
            _bounded_text("info_url", self.info_url, 2048)
            parsed = urlparse(self.info_url)
            if (parsed.scheme not in {"", "https"}
                    or (parsed.scheme == "https" and not parsed.netloc)
                    or (not parsed.scheme and parsed.netloc)):
                raise ValidationError("info_url must be an https or configured relative URL")
        object.__setattr__(self, "details", bounded_details(self.details))

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["event_id"] = str(self.event_id)
        result["created_at"] = self.created_at.isoformat()
        result["occurred_at"] = self.occurred_at.isoformat() if self.occurred_at else None
        result["severity"] = self.severity.value
        return result


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    enabled: bool = False
    older_than_days: int = 365
    max_entries: int = 100_000
    policy_id: str | None = None
    legal_basis: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValidationError("enabled must be boolean")
        if isinstance(self.older_than_days, bool) or not isinstance(self.older_than_days, int) or self.older_than_days <= 0:
            raise ValidationError("older_than_days must be positive")
        if isinstance(self.max_entries, bool) or not isinstance(self.max_entries, int) or not 0 < self.max_entries <= 1_000_000:
            raise ValidationError("max_entries is out of bounds")
        if self.legal_basis is not None:
            _bounded_text("legal_basis", self.legal_basis, 500)


@dataclass(frozen=True, slots=True)
class LegalHold:
    hold_id: UUID = field(default_factory=uuid4)
    object_uid: str = ""
    event_id: UUID | None = None
    reason: str = ""
    actor: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    released_at: datetime | None = None
    released_by: str | None = None

    def __post_init__(self) -> None:
        _bounded_text("object_uid", self.object_uid, 1024, required=True)
        _bounded_text("reason", self.reason, 4000, required=True)
        _bounded_text("actor", self.actor, 255, required=True)
        object.__setattr__(self, "created_at", utc(self.created_at, "created_at"))
        if self.released_at is not None:
            object.__setattr__(self, "released_at", utc(self.released_at, "released_at"))
        if self.released_by is not None:
            _bounded_text("released_by", self.released_by, 255, required=True)


@dataclass(frozen=True, slots=True)
class DeletionPreview:
    preview_id: UUID
    object_uid: str
    event_ids: tuple[UUID, ...]
    selection_digest: str
    actor: str
    created_at: datetime
    expires_at: datetime

    def is_expired(self, now: datetime | None = None) -> bool:
        return utc(now or datetime.now(UTC), "now") >= self.expires_at
