"""Plone control-panel form for audit logging policy."""

from plone.app.registry.browser import controlpanel
from plone.app.z3cform.widgets.orderedselect import OrderedSelectFieldWidget
from plone.app.z3cform.widgets.password import PasswordFieldWidget
from plone.z3cform import layout

from .interfaces import ISettings


class AuditLoggingEditForm(controlpanel.RegistryEditForm):
    schema = ISettings
    id = "AuditLoggingEditForm"
    label = "Persistent audit logging"
    description = (
        "Choose the content types whose lifecycle events should be recorded. "
        "No content type is enabled by default."
    )

    def updateFields(self):
        super().updateFields()
        self.fields["enabled_content_types"].widgetFactory = OrderedSelectFieldWidget
        self.fields["database_url"].widgetFactory = PasswordFieldWidget


AuditLoggingControlPanel = layout.wrap_form(
    AuditLoggingEditForm,
    controlpanel.ControlPanelFormWrapper,
)


def enabled_content_types(context):
    """Return configured portal type IDs without creating registry records."""
    from plone.registry.interfaces import IRegistry
    from zope.component import queryUtility

    registry = queryUtility(IRegistry)
    if registry is None:
        return frozenset()
    settings = registry.forInterface(ISettings, check=False)
    return frozenset(settings.enabled_content_types or ())


def logging_enabled(context) -> bool:
    return getattr(context, "portal_type", None) in enabled_content_types(context)


class LoggingEnabled:
    """Traversal view used by the toolbar action availability expression."""

    def __init__(self, context, request):
        self.context = context

    def __call__(self):
        return logging_enabled(self.context)
