"""Small browser adapters with explicit read/mutation boundaries."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

from .api import export_events, search_events, verify_integrity
from .errors import ValidationError


def json_response(request: Any, value: Any, status: int = 200) -> str:
    request.response.setStatus(status)
    request.response.setHeader("Content-Type", "application/json; charset=utf-8")
    request.response.setHeader("Cache-Control", "no-store")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _post(request: Any) -> None:
    if getattr(request, "method", "GET").upper() != "POST":
        raise ValidationError("state-changing operations require POST")
    try:
        from plone.protect.interfaces import ICheckAuthenticator

        ICheckAuthenticator(request).validate()
    except ImportError:
        # Pure unit tests can use a request double.  Real Plone installations
        # always provide the authenticator adapter.
        return


class AuditData:
    def __init__(self, context: Any, request: Any):
        self.context, self.request = context, request

    def __call__(self) -> str:
        filters = {
            key: self.request.form.get(key)
            for key in ("actor", "event_type", "severity", "quick", "from", "to")
            if self.request.form.get(key)
        }
        return json_response(
            self.request, {"rows": search_events(self.context, limit=100, **filters)}
        )


class AuditLog:
    """Accessible AG Grid shell for the current object's audit stream."""

    index = ViewPageTemplateFile("browser/templates/audit_log.pt")

    def __init__(self, context: Any, request: Any):
        self.context, self.request = context, request

    @property
    def data_url(self) -> str:
        return f"{self.context.absolute_url()}/@@persistent-log-data"

    @property
    def export_url(self) -> str:
        return f"{self.context.absolute_url()}/@@persistent-log-export"

    @property
    def timezone(self) -> str:
        from plone.registry.interfaces import IRegistry
        from zope.component import queryUtility

        registry = queryUtility(IRegistry)
        return (
            registry.get("plone.portal_timezone", "UTC") if registry else "UTC"
        ) or "UTC"

    def __call__(self) -> str:
        return self.index()


class AuditIntegrity:
    def __init__(self, context: Any, request: Any):
        self.context, self.request = context, request

    def __call__(self) -> str:
        return json_response(self.request, verify_integrity(self.context))


class AuditExport:
    def __init__(self, context: Any, request: Any):
        self.context, self.request = context, request

    def __call__(self) -> bytes:
        fmt = self.request.form.get("format", "json")
        payload, content_type, filename = export_events(
            self.context, format=fmt, max_rows=10_000
        )
        self.request.response.setHeader("Content-Type", content_type)
        self.request.response.setHeader("Cache-Control", "no-store")
        self.request.response.setHeader(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
        return payload


class AuditRetentionPreview:
    def __init__(self, context: Any, request: Any):
        self.context, self.request = context, request

    def __call__(self) -> str:
        _post(self.request)
        preview = (
            __import__("zopyx.plone.persistentlogger.api", fromlist=["repository_for"])
            .repository_for(self.context)
            .create_preview(actor=str(self.request.form.get("actor", "system")))
        )
        return json_response(
            self.request,
            {
                "preview_id": str(preview.preview_id),
                "event_ids": [str(item) for item in preview.event_ids],
                "selection_digest": preview.selection_digest,
                "expires_at": preview.expires_at.isoformat(),
            },
        )


class AuditRetentionDelete:
    def __init__(self, context: Any, request: Any):
        self.context, self.request = context, request

    def __call__(self) -> str:
        _post(self.request)
        preview_id = self.request.form.get("preview_id")
        if not preview_id:
            raise ValidationError("preview_id is required")
        repo = __import__(
            "zopyx.plone.persistentlogger.api", fromlist=["repository_for"]
        ).repository_for(self.context)
        result = repo.delete_preview(
            UUID(preview_id),
            actor=str(self.request.form.get("actor", "system")),
            reason=str(self.request.form.get("reason", "")),
        )
        return json_response(self.request, result)
