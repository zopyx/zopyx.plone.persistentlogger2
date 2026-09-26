# API reference

The public services in `zopyx.plone.persistentlogger.api` are independent of
browser views and can be called from application code.

## Event creation

```python
from datetime import UTC, datetime
from zopyx.plone.persistentlogger.api import log_event

row = log_event(
    context,
    "Invoice approved",
    event_type="business.invoice.approved",
    severity="info",
    actor="alice",
    target="INV-42",
    info_url="/invoices/INV-42",
    details={"invoice_id": "INV-42", "approval_level": 2},
    occurred_at=datetime.now(UTC),
)
```

```python
log_event(
    context,
    comment,
    *,
    event_type="application",
    severity="info",
    actor=None,
    target=None,
    info_url=None,
    details=None,
    occurred_at=None,
    created_at=None,
)
```

`context` determines the object scope. Severity is `debug`, `info`, `warning`,
`error`, or `critical`; `warn` is normalized to `warning`. `info_url` accepts
HTTPS or relative URLs. Omitted actors resolve to the current user, then to
`system`.

Details are normalized recursively: datetimes become ISO strings, tuples
become lists, and sensitive keys are replaced before storage. Oversized detail
payloads are rejected. The returned row includes `event_id`, `sequence`,
`previous_digest`, `integrity_digest`, and `write_status`.

In synchronous mode, `log_event()` returns the persisted row. With global
`audit_write_mode=taskqueue2`, it returns the validated event payload after
queue acceptance with `write_status="enqueued"`; the row may not yet be
searchable. Queue rejection raises a configuration error and does not silently
fall back to synchronous persistence.

## Search and verification

```python
from zopyx.plone.persistentlogger.api import search_events, verify_integrity

rows = search_events(
    context,
    actor="alice",
    event_type="business.invoice.approved",
    quick="invoice",
    sort="desc",
    limit=100,
    offset=0,
)
integrity = verify_integrity(context)
```

Search supports exact `event_id`, `actor`, `target`, `event_type`, `severity`,
and `schema_version`; `quick` searches comment, actor, and event type. `from`
and `to` are inclusive ISO timestamp filters. Results are newest first unless
`sort="asc"` is provided. The default limit is 100 and the repository clamps
limits to 1,000.

`verify_integrity` returns `ok: true` with count and chain head for a valid
stream. A failed result identifies the broken event when possible.

## Export

```python
from zopyx.plone.persistentlogger.api import export_events

payload, content_type, filename = export_events(
    context,
    format="csv",
    max_rows=5000,
    max_bytes=5 * 1024 * 1024,
    actor="alice",
)
```

Supported formats are `json` and `csv`. `max_rows` must be 1–100,000; the
default byte budget is 5 MiB. CSV values beginning with `=`, `+`, `-`, or `@`
are prefixed to reduce spreadsheet formula injection risk. Invalid formats or
budgets raise `ValidationError`.

## Retention and governance

```python
from zopyx.plone.persistentlogger.api import set_retention_policy
from zopyx.plone.persistentlogger.models import RetentionPolicy

set_retention_policy(
    context,
    RetentionPolicy(
        enabled=True,
        older_than_days=365,
        max_entries=100000,
        legal_basis="Documented retention policy",
    ),
    actor="admin",
)
```

Repositories provide `create_preview` and `delete_preview`. A preview is bound
to its actor and expiry. Deletion requires the preview ID, the same actor, and
a non-empty reason. Active legal holds are excluded from previews and block
deletion if a selected event becomes held. `api.clear` always raises
`ValidationError`.

## Site-wide notifications

Other add-ons can publish a notification through the standard `zope.event`
channel:

```python
from zope.event import notify
from zopyx.plone.persistentlogger import PersistentLoggerNotification

notify(PersistentLoggerNotification(
    "Invoice approved",
    event_type="business.invoice.approved",
    actor="billing-service",
    details={"invoice_id": "INV-42"},
))
```

The persistent logger subscriber resolves the current Plone site with
`zope.component.hooks.getSite()` and sends the event through `log_event` using
the site root as its context. For code outside a request, pass `site=portal`:

```python
notify(PersistentLoggerNotification(
    "Nightly import completed",
    event_type="integration.import.completed",
    actor="nightly-job",
    details={"count": 1250},
    site=portal,
))
```

The notification event supports `comment`, `event_type`, `severity`, `actor`,
`target`, `info_url`, `details`, `occurred_at`, and `site`. The subscriber is
explicit application logging, so it is not gated by the lifecycle content-type
allow-list.

## Repository contract

`repository_for(context)` is the application API's backend seam. Plone uses
`ZODBRepository`; `MemoryRepository` is the reference implementation;
`SQLiteRepository`, `DuckDBRepository`, and `PostgresRepository` implement the
RDBMS contract for local, embedded, and integration use.
