"""SurveyJS-backed control panel and logging policy helpers."""

from __future__ import annotations

import json
from urllib.parse import quote

from Products.CMFCore.utils import getToolByName
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

from .errors import ValidationError
from .interfaces import ISettings


def _registry():
    from plone.registry.interfaces import IRegistry
    from zope.component import queryUtility
    return queryUtility(IRegistry)


def enabled_content_types(context):
    registry = _registry()
    if registry is None:
        return frozenset()
    settings = registry.forInterface(ISettings, check=False)
    return frozenset(settings.enabled_content_types or ())


def logging_enabled(context) -> bool:
    return getattr(context, "portal_type", None) in enabled_content_types(context)


class AuditLoggingControlPanel:
    """Render and persist audit settings through SurveyJS Form Library."""

    index = ViewPageTemplateFile("browser/templates/controlpanel.pt")

    def __init__(self, context, request):
        self.context, self.request = context, request

    @property
    def save_url(self):
        url = f"{self.context.absolute_url()}/@@persistentlogger-controlpanel-save"
        token = self.request.form.get("_authenticator")
        if not token:
            from plone.protect import createToken
            token = createToken()
        return f"{url}?_authenticator={quote(str(token))}" if token else url

    def _settings(self):
        registry = _registry()
        return registry.forInterface(ISettings, check=False) if registry else None

    def _content_type_choices(self):
        portal_types = getToolByName(self.context, "portal_types")
        choices = []
        for type_id in portal_types.listContentTypes():
            type_info = portal_types.get(type_id)
            choices.append({"value": type_id, "text": type_info.Title() if type_info else type_id})
        return sorted(choices, key=lambda item: item["text"].casefold())

    def survey(self):
        settings = self._settings()
        return {
            "title": "Persistent audit logging",
            "description": "Choose the content types whose lifecycle events should be recorded.",
            "showQuestionNumbers": "off",
            "showCompletedPage": True,
            "completedHtml": "<div class='message success'>Settings saved.</div>",
            "completeText": "Save settings",
            "pages": [{"name": "settings", "elements": [
                {"type": "radiogroup", "name": "backend", "title": "Backend", "isRequired": True,
                 "choices": [{"value": "zodb", "text": "ZODB"}, {"value": "rdbms", "text": "RDBMS"}]},
                {"type": "dropdown", "name": "transaction_mode", "title": "Transaction mode",
                 "description": "Joined writes with the current transaction; outbox queues delivery; independent commits separately.",
                 "choices": ["joined", "outbox", "independent"]},
                {"type": "text", "name": "detail_limit", "title": "Detail byte limit", "inputType": "number",
                 "description": "Maximum UTF-8 size of serialized event details; larger details are rejected.",
                 "min": 1024, "max": 1048576, "isRequired": True},
                {"type": "checkbox", "name": "enabled_content_types", "title": "Content types with audit logging",
                 "description": "Select the Plone content types whose lifecycle events should be logged.",
                 "colCount": 3, "choices": self._content_type_choices()},
                {"type": "text", "name": "database_url", "title": "Database URL", "inputType": "password",
                 "description": "Optional RDBMS URL.", "visibleIf": "{backend} = 'rdbms'"},
            ]}],
            "data": {
                "backend": getattr(settings, "backend", "zodb"),
                "transaction_mode": getattr(settings, "transaction_mode", "outbox"),
                "detail_limit": getattr(settings, "detail_limit", 65536),
                "enabled_content_types": sorted(getattr(settings, "enabled_content_types", ()) or ()),
                "database_url": getattr(settings, "database_url", "") or "",
            },
        }

    def __call__(self):
        return self.index()


class AuditLoggingControlPanelSave:
    """CSRF-protected JSON endpoint used by the SurveyJS control panel."""

    def __init__(self, context, request):
        self.context, self.request = context, request

    def __call__(self):
        if getattr(self.request, "method", "GET").upper() != "POST":
            raise ValidationError("state-changing operations require POST")
        from plone.protect import CheckAuthenticator
        CheckAuthenticator(self.request)
        try:
            data = json.loads(self.request.stdin.read() or "{}")
        except (TypeError, ValueError) as exc:
            raise ValidationError("request body must be JSON") from exc
        registry = _registry()
        if registry is None:
            raise ValidationError("Plone registry is unavailable")
        settings = registry.forInterface(ISettings, check=False)
        backend = str(data.get("backend", "zodb"))
        transaction_mode = str(data.get("transaction_mode", "outbox"))
        if backend not in {"zodb", "rdbms"} or transaction_mode not in {"joined", "outbox", "independent"}:
            raise ValidationError("invalid storage settings")
        try:
            detail_limit = int(data.get("detail_limit", 65536))
        except (TypeError, ValueError) as exc:
            raise ValidationError("detail_limit must be an integer") from exc
        if not 1024 <= detail_limit <= 1048576:
            raise ValidationError("detail_limit is out of bounds")
        enabled = data.get("enabled_content_types", ()) or ()
        if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
            raise ValidationError("enabled_content_types must be a list of strings")
        settings.backend = backend
        settings.transaction_mode = transaction_mode
        settings.detail_limit = detail_limit
        settings.enabled_content_types = set(enabled)
        settings.database_url = str(data.get("database_url", "") or "")
        self.request.response.setHeader("Content-Type", "application/json; charset=utf-8")
        return json.dumps({"ok": True})


class LoggingEnabled:
    def __init__(self, context, request):
        self.context = context

    def __call__(self):
        return logging_enabled(self.context)
