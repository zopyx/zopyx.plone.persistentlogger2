"""Public application services."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from .errors import ValidationError
from .delivery import enqueue
from .models import LogEvent, RetentionPolicy
from .repository import LogRepository, ZODBRepository, object_uid


def _current_actor() -> str:
    try:
        import plone.api

        user = plone.api.user.get_current()
        name = user.getUserName() if user else None
        if name:
            return name
    except Exception:
        pass
    return "system"


def repository_for(context: Any) -> LogRepository:
    """Select the configured backend; selection is the only backend detail here."""
    return ZODBRepository(context)


def log_event(
    context: Any,
    comment: str,
    *,
    event_type: str = "application",
    severity: str = "info",
    actor: str | None = None,
    target: str | None = None,
    info_url: str | None = None,
    details: Any = None,
    occurred_at: datetime | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    event = LogEvent(
        comment=comment,
        object_uid=object_uid(context),
        event_type=event_type,
        severity=severity,
        actor=actor or _current_actor(),
        target=target,
        info_url=info_url,
        details=details,
        occurred_at=occurred_at,
        **({"created_at": created_at} if created_at is not None else {}),
    )
    from .controlpanel import audit_write_mode

    if audit_write_mode(context) == "taskqueue2":
        return enqueue(context, event)
    return repository_for(context).append(event)


def search_events(context: Any, **filters: Any) -> list[dict[str, Any]]:
    return repository_for(context).search(**filters)


def verify_integrity(context: Any) -> dict[str, Any]:
    return repository_for(context).verify()


def set_retention_policy(
    context: Any, policy: RetentionPolicy, *, actor: str
) -> RetentionPolicy:
    return repository_for(context).set_policy(policy, actor=actor)


def export_events(
    context: Any,
    *,
    format: str = "json",
    max_rows: int = 10_000,
    max_bytes: int = 5 * 1024 * 1024,
    **filters: Any,
) -> tuple[bytes, str, str]:
    if format not in {"json", "csv"}:
        raise ValidationError("format must be json or csv")
    if not isinstance(max_rows, int) or not 0 < max_rows <= 100_000:
        raise ValidationError("max_rows is out of bounds")
    rows = repository_for(context).search(**{**filters, "limit": max_rows})
    if format == "json":
        payload = json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        content_type = "application/json"
    else:
        stream = io.StringIO(newline="")
        fields = [
            "event_id",
            "object_uid",
            "created_at",
            "occurred_at",
            "actor",
            "event_type",
            "severity",
            "target",
            "comment",
            "info_url",
            "details",
            "schema_version",
            "sequence",
            "previous_digest",
            "integrity_digest",
        ]
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            safe = dict(row)
            safe["details"] = json.dumps(
                safe.get("details"), ensure_ascii=False, sort_keys=True
            )
            for key, value in list(safe.items()):
                if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"}:
                    safe[key] = "'" + value
            writer.writerow(safe)
        payload = stream.getvalue().encode("utf-8")
        content_type = "text/csv; charset=utf-8"
    if len(payload) > max_bytes:
        raise ValidationError("export exceeds the configured byte budget")
    return payload, content_type, f"audit-{object_uid(context)}.{format}"


def clear(*args: Any, **kwargs: Any) -> None:
    raise ValidationError(
        "clear is removed; use retention preview and governed deletion"
    )
