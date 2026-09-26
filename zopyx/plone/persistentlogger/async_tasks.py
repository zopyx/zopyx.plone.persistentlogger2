"""Optional taskqueue2 worker for audit event persistence."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .errors import ConfigurationError, IdempotentReplay
from .models import LogEvent

try:  # Keep the normal synchronous installation independent of taskqueue2.
    from collective.taskqueue2.huey_config import huey_taskqueue
except ImportError:  # pragma: no cover - exercised in optional-dependency tests
    huey_taskqueue = None


def _event_from_payload(payload: dict[str, Any]) -> LogEvent:
    value = dict(payload)
    value["event_id"] = UUID(value["event_id"])
    from datetime import datetime

    value["created_at"] = datetime.fromisoformat(value["created_at"])
    if value.get("occurred_at"):
        value["occurred_at"] = datetime.fromisoformat(value["occurred_at"])
    return LogEvent(**value)


def persist_event(repository: Any, event: LogEvent) -> dict[str, Any]:
    """Append an event and treat identical redelivery as success."""
    try:
        return repository.append(event)
    except IdempotentReplay:
        existing = repository.get(event.event_id)
        if existing is None:
            raise
        return {**existing, "write_status": "already_persisted"}


def _persist_audit_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist one queued event in a fresh Plone transaction."""
    import transaction
    import Zope2
    from zope.component.hooks import setSite

    from .api import repository_for

    manager = transaction.manager
    manager.begin()
    app = Zope2.app()
    site = app.restrictedTraverse(payload["site_path"], None)
    if site is None:
        manager.abort()
        app._p_jar.close()
        raise ValueError(f"No Plone site at {payload['site_path']}")
    setSite(site)
    try:
        context = site.restrictedTraverse(payload["context_path"], None)
        if context is None:
            raise ValueError(f"No context at {payload['context_path']}")
        row = persist_event(repository_for(context), _event_from_payload(payload["event"]))
        manager.commit()
        return row
    except Exception:
        manager.abort()
        raise
    finally:
        setSite(None)
        app._p_jar.close()


if huey_taskqueue is not None:  # pragma: no cover - exercised with taskqueue2 extra

    @huey_taskqueue.task()
    def persist_audit_event(**payload: Any) -> dict[str, Any]:
        return _persist_audit_event(payload)

else:  # pragma: no cover

    def persist_audit_event(**payload: Any) -> dict[str, Any]:
        del payload
        raise ConfigurationError(
            "taskqueue2 support is not installed; install the taskqueue2 extra"
        )
