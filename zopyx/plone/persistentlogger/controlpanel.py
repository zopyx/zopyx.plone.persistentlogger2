"""SurveyJS-backed control panel and logging policy helpers."""

from __future__ import annotations

import json
import logging
from urllib.parse import quote

from Products.CMFCore.utils import getToolByName
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile

from .errors import ValidationError
from .interfaces import ISettings

logger = logging.getLogger(__name__)


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
    registry = _registry()
    globally_enabled = True
    if registry is not None:
        settings = registry.forInterface(ISettings, check=False)
        globally_enabled = getattr(settings, "audit_logging_enabled", True)
    return globally_enabled and getattr(context, "portal_type", None) in enabled_content_types(context)


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

    @property
    def redirect_url(self):
        return f"{self.context.absolute_url()}/@@persistentlogger-controlpanel"

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
        logger.info(
            "Loading audit control-panel settings: enabled=%s backend=%s transaction_mode=%s detail_limit=%s content_types=%s",
            getattr(settings, "audit_logging_enabled", True),
            getattr(settings, "backend", None) or "zodb",
            getattr(settings, "transaction_mode", None) or "outbox",
            getattr(settings, "detail_limit", 65536),
            sorted(getattr(settings, "enabled_content_types", ()) or ()),
        )
        return {
            "title": "Persistent audit logging",
            "showQuestionNumbers": "off",
            "showCompletedPage": True,
            "completedHtml": "<div class='message success'>Settings saved.</div>",
            "completeText": "Save settings",
            "pages": [{"name": "settings", "elements": [
                {"type": "boolean", "name": "audit_logging_enabled", "title": "Audit logging",
                 "description": "Enable or disable audit logging globally.", "defaultValue": True},
                {"type": "panel", "name": "backend_settings", "title": "Backend", "elements": [
                    {"type": "radiogroup", "name": "backend", "title": "Storage backend", "defaultValue": "zodb", "isRequired": True,
                     "choices": [{"value": "zodb", "text": "ZODB"}, {"value": "rdbms", "text": "RDBMS"}]},
                    {"type": "dropdown", "name": "transaction_mode", "title": "Transaction mode", "defaultValue": "outbox",
                     "description": "Joined writes with the current transaction; outbox queues delivery; independent commits separately.",
                     "choices": ["joined", "outbox", "independent"]},
                    {"type": "text", "name": "database_url", "title": "Database URL", "inputType": "password",
                     "description": "Optional RDBMS URL.", "visibleIf": "{backend} = 'rdbms'"},
                ]},
                {"type": "panel", "name": "logging_settings", "title": "Logging", "elements": [
                    {"type": "text", "name": "detail_limit", "title": "Detail byte limit", "inputType": "number",
                     "description": "Maximum UTF-8 size of serialized event details; larger details are rejected.",
                     "min": 1024, "max": 100000, "isRequired": True},
                    {"type": "checkbox", "name": "enabled_content_types", "title": "Content types with audit logging",
                     "description": "Select the Plone content types whose lifecycle events should be logged.",
                     "colCount": 3, "choices": self._content_type_choices()},
                ]},
            ]}],
            "data": {
                "audit_logging_enabled": getattr(settings, "audit_logging_enabled", True),
                "backend": getattr(settings, "backend", None) or "zodb",
                "transaction_mode": getattr(settings, "transaction_mode", None) or "outbox",
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
        registry.registerInterface(ISettings)
        settings = registry.forInterface(ISettings, check=False)
        audit_logging_enabled = data.get("audit_logging_enabled", True)
        if not isinstance(audit_logging_enabled, bool):
            raise ValidationError("audit_logging_enabled must be boolean")
        backend = str(data.get("backend", "zodb"))
        transaction_mode = str(data.get("transaction_mode", "outbox"))
        if backend not in {"zodb", "rdbms"} or transaction_mode not in {"joined", "outbox", "independent"}:
            raise ValidationError("invalid storage settings")
        try:
            detail_limit = int(data.get("detail_limit", 65536))
        except (TypeError, ValueError) as exc:
            raise ValidationError("detail_limit must be an integer") from exc
        if not 1024 <= detail_limit <= 100000:
            raise ValidationError("detail_limit is out of bounds")
        enabled = data.get("enabled_content_types", [])
        if enabled is None:
            enabled = []
        if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
            raise ValidationError("enabled_content_types must be a list of strings")
        settings.audit_logging_enabled = audit_logging_enabled
        settings.backend = backend
        settings.transaction_mode = transaction_mode
        settings.detail_limit = detail_limit
        settings.enabled_content_types = set(enabled)
        settings.database_url = str(data.get("database_url", "") or "")
        logger.info(
            "Saving audit control-panel settings: enabled=%s backend=%s transaction_mode=%s detail_limit=%s content_types=%s database_url_configured=%s",
            audit_logging_enabled, backend, transaction_mode, detail_limit, sorted(enabled), bool(settings.database_url),
        )
        from Products.statusmessages.interfaces import IStatusMessage
        IStatusMessage(self.request).addStatusMessage("Audit logging settings saved.", type="info")
        self.request.response.redirect(f"{self.context.absolute_url()}/@@persistentlogger-controlpanel")
        return ""


class LoggingEnabled:
    def __init__(self, context, request):
        self.context = context

    def __call__(self):
        return logging_enabled(self.context)
