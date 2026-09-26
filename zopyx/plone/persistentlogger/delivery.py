"""Audit event delivery selection and taskqueue2 integration."""

from __future__ import annotations

from typing import Any, Callable

from .errors import ConfigurationError
from .models import LogEvent


def _path(context: Any) -> str:
    try:
        parts = [str(part) for part in context.getPhysicalPath() if str(part)]
        return "/" + "/".join(parts) if parts else ""
    except (AttributeError, TypeError):
        return ""


def _site_path(context: Any) -> str:
    try:
        from zope.component.hooks import getSite

        site = getSite()
        if site is not None:
            return _path(site)
    except Exception:
        pass
    return _path(context)


def event_envelope(context: Any, event: LogEvent) -> dict[str, Any]:
    """Return the JSON-compatible payload sent to a worker."""
    context_path = _path(context)
    site_path = _site_path(context)
    if not context_path or not site_path:
        raise ConfigurationError(
            "async audit delivery requires a Plone site and physical context path"
        )
    return {
        "event": event.to_dict(),
        "site_path": site_path,
        "context_path": context_path,
    }


def _task() -> Callable[..., Any]:
    from .async_tasks import persist_audit_event

    return persist_audit_event


def enqueue(context: Any, event: LogEvent) -> dict[str, Any]:
    """Enqueue an event and return an accepted status."""
    payload = event_envelope(context, event)
    task = _task()
    try:
        task(**payload)
    except ConfigurationError:
        raise
    except Exception as exc:
        raise ConfigurationError("taskqueue2 could not accept the audit event") from exc
    return {
        **event.to_dict(),
        "write_status": "enqueued",
    }
