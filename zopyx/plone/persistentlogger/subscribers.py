"""Opt-in lifecycle subscribers controlled by the registry setting."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from .api import log_event, search_events
from .controlpanel import logging_enabled
from .serialization import normalize


_WRITING_AUDIT = ContextVar("zopyx.plone.persistentlogger.writing", default=False)

_METADATA_FIELDS = (
    "title", "description", "subject", "language", "rights", "effective",
    "expiration", "creators", "contributors", "location", "exclude_from_nav",
    "allowDiscussion", "layout", "short_name",
)


def _actor() -> str:
    try:
        import plone.api
        user = plone.api.user.get_current()
        return user.getUserName() if user else "system"
    except Exception:
        return "system"


def _metadata_value(value: Any) -> Any:
    """Return a redacted JSON-compatible metadata value, or skip it."""
    try:
        return normalize(value)
    except Exception:
        return None


def _metadata_snapshot(context: Any) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "portal_type": str(getattr(context, "portal_type", "")),
        "id": str(getattr(context, "id", getattr(context, "__name__", ""))),
    }
    for name in _METADATA_FIELDS:
        try:
            value = context.get(name) if hasattr(context, "get") else getattr(context, name, None)
            if value is None:
                value = getattr(context, name, None)
        except Exception:
            value = getattr(context, name, None)
        safe = _metadata_value(value)
        if safe is not None:
            snapshot[name] = safe
    return snapshot


def _previous_snapshot(context: Any) -> dict[str, Any] | None:
    for row in search_events(context, limit=1000, sort="desc"):
        details = row.get("details")
        if isinstance(details, dict) and isinstance(details.get("snapshot_after"), dict):
            return details["snapshot_after"]
    return None


def _historical_snapshot(context: Any) -> dict[str, Any] | None:
    """Read the last committed object state when no audit baseline exists."""
    try:
        jar = context._p_jar
        history = jar.db().history(context._p_oid, size=1)
        if not history:
            return None
        connection = jar.db().open(before=history[0]["id"])
        try:
            previous = connection.get(context._p_oid)
            return _metadata_snapshot(previous) if previous is not None else None
        finally:
            connection.close()
    except Exception:
        return None


def _metadata_diff(before: dict[str, Any] | None, after: dict[str, Any]) -> dict[str, Any]:
    if before is None:
        return {
            "baseline_available": False,
            "added": {},
            "removed": {},
            "changed": {},
        }
    added = {key: value for key, value in after.items() if key not in before}
    removed = {key: value for key, value in before.items() if key not in after}
    changed = {
        key: {"before": before[key], "after": value}
        for key, value in after.items()
        if key in before and before[key] != value
    }
    return {
        "baseline_available": True,
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def _event_details(context: Any, *, before: dict[str, Any] | None = None) -> dict[str, Any]:
    snapshot = _metadata_snapshot(context)
    return {
        "portal_type": snapshot["portal_type"],
        "id": snapshot["id"],
        "changes": _metadata_diff(before, snapshot),
        "snapshot_after": snapshot,
    }


def _changed_fields(event: Any) -> list[str]:
    fields: set[str] = set()
    for description in getattr(event, "descriptions", ()):
        fields.update(str(name) for name in getattr(description, "attributes", ()))
        fields.update(str(name) for name in getattr(description, "keys", ()))
    return sorted(fields)


def log_added(context: Any, event: Any) -> None:
    if not logging_enabled(context) or _WRITING_AUDIT.get():
        return
    token = _WRITING_AUDIT.set(True)
    try:
        log_event(
            context,
            "Content item created",
            event_type="plone.content.created",
            actor=_actor(),
            details=_event_details(context, before={}),
        )
    finally:
        _WRITING_AUDIT.reset(token)


def log_modified(context: Any, event: Any) -> None:
    if not logging_enabled(context) or _WRITING_AUDIT.get():
        return
    token = _WRITING_AUDIT.set(True)
    try:
        before = _previous_snapshot(context) or _historical_snapshot(context)
        details = _event_details(context, before=before)
        fields = _changed_fields(event)
        if fields:
            details["changed_fields"] = fields
        log_event(
            context,
            "Content item modified",
            event_type="plone.content.modified",
            actor=_actor(),
            details=details,
        )
    finally:
        _WRITING_AUDIT.reset(token)
