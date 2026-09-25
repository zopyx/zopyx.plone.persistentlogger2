"""Public notification event for site-wide audit records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zope.interface import implementer

from .interfaces import IPersistentLoggerNotification


@implementer(IPersistentLoggerNotification)
@dataclass(frozen=True, slots=True)
class PersistentLoggerNotification:
    """Event other add-ons can publish for the Plone site audit stream.

    When ``site`` is omitted, the subscriber uses the current Plone site from
    ``zope.component.hooks.getSite()``. Supplying ``site`` is recommended for
    jobs or code that runs outside a request.
    """

    comment: str
    event_type: str = "application.notification"
    severity: str = "info"
    actor: str | None = None
    target: str | None = None
    info_url: str | None = None
    details: Any = None
    occurred_at: datetime | None = None
    site: Any | None = None
