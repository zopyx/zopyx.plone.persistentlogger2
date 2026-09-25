from zope.interface import Interface
from zope import schema

class ISettings(Interface):
    audit_logging_enabled = schema.Bool(
        title="Audit logging",
        description="Enable or disable audit logging globally.",
        default=True,
    )
    backend = schema.Choice(title="Backend", values=("zodb", "rdbms"), default="zodb")
    transaction_mode = schema.Choice(title="Transaction mode", values=("joined", "outbox", "independent"), default="outbox")
    detail_limit = schema.Int(title="Detail byte limit", default=65536, min=1024, max=100000)
    enabled_content_types = schema.Set(
        title="Content types with audit logging",
        description="Select the Plone content types whose lifecycle events should be logged.",
        required=False,
        value_type=schema.Choice(
            vocabulary="plone.app.vocabularies.ReallyUserFriendlyTypes",
            required=False,
        ),
    )
    database_url = schema.TextLine(
        title="Database URL",
        description="Optional RDBMS URL. It is rendered as a password and never shown in health responses.",
        required=False,
        default="",
        max_length=2048,
    )
