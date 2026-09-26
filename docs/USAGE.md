# Usage guide

## Lifecycle logging

Lifecycle subscribers are registered for Plone contentish objects but remain
opt-in. Select types under **Content types with audit logging** in Site Setup.
Added and modified events then record object metadata; modified events include
before/after changes where a baseline is available.

## Application events

```python
from zopyx.plone.persistentlogger.api import log_event

log_event(
    context,
    "Payment captured",
    event_type="billing.payment.captured",
    severity="info",
    details={"payment_id": payment_id, "amount": amount},
)
```

Keep details JSON-compatible and avoid credentials in comments, targets, or
URLs. Details are redacted and bounded, but callers should still avoid passing
secrets unnecessarily.

### Optional asynchronous delivery

Synchronous writes are the default. To enable asynchronous delivery, install
the `taskqueue2` extra, configure a consumer with the same
`HUEY_TASKQUEUE_URL`, and select **collective.taskqueue2** under **Audit write
delivery** in the site control panel:

```python
row = log_event(context, "Payment captured")
assert row["write_status"] == "enqueued"
```

This status means the immutable event envelope was accepted by the queue, not
that the audit row is already persisted. The worker opens a fresh transaction
and uses the original event ID, so retries are idempotent. Do not enable async
mode without a running consumer and monitoring for retries or failed tasks.

## Site-wide notifications

For events emitted by another add-on, publish
`PersistentLoggerNotification` through `zope.event`:

```python
from zope.event import notify
from zopyx.plone.persistentlogger import PersistentLoggerNotification

notify(PersistentLoggerNotification(
    "Payment captured",
    event_type="billing.payment.captured",
    actor="billing-service",
    details={"payment_id": payment_id},
))
```

During a normal Plone request the subscriber resolves the current site and
stores the record under the Plone root. Jobs running outside a request should
pass `site=portal`. This stream is explicit application logging and does not
depend on the lifecycle content-type selection.

## Audit view

The **Logging** object action opens `@@persistent-log`, which uses AG Grid
Enterprise's server-side row model. It provides quick search, server-side
sortable/resizable columns, server-side filtering, pagination, localized
timestamps, a JSON details dialog, JSON/CSV exports, and a **No matching data
found** state. The browser requests only the current row block; the endpoint
returns `rows` and the total matching row count.

AG Grid Enterprise requires an appropriate production license. The add-on
does not embed a license key; configure the key in the deployment's frontend
asset strategy according to AG Grid's licensing instructions.

Exports use the current search text and require the export permission. CSV
formula-like values are escaped before download.

## Integrity checks

Open `@@persistent-log-integrity` for JSON integrity status. A successful
result includes event count and chain head; a failed result identifies the
broken event when possible. Preserve original storage before attempting repair:
verification is diagnostic and does not make external backups.

## Retention workflow

1. Configure and save a `RetentionPolicy`.
2. POST to `@@persistent-log-retention/preview` with an actor.
3. Review the preview ID, event IDs, digest, and expiry.
4. POST that preview ID to `@@persistent-log-retention/delete` with the same
   actor and a human-readable reason.

The preview is short-lived, actor-bound, and excludes active legal holds.
Deletion is journaled and reseals the surviving local chain. A held event
blocks deletion rather than being silently removed.
