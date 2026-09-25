"""Public exception taxonomy for the audit logger."""


class AuditError(Exception):
    """Base class for expected audit logger failures."""


class ValidationError(AuditError, ValueError):
    """An input is not valid for the audit contract."""


class AuthorizationError(AuditError, PermissionError):
    """The current principal cannot perform the operation."""


class BackendUnavailable(AuditError):
    """The configured backend cannot accept the operation."""


class IntegrityError(AuditError):
    """Stored evidence fails local integrity verification."""


class DuplicateEvent(AuditError):
    """An event UUID is already present with a different payload."""


class IdempotentReplay(AuditError):
    """An identical event UUID was submitted more than once."""


class ConfigurationError(AuditError):
    """The logger is not configured sufficiently to operate."""


class PreviewExpired(AuditError):
    """A deletion preview is absent or no longer usable."""


class HoldConflict(AuditError):
    """A legal hold blocks the requested operation."""
