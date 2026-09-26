# zopyx.plone.persistentlogger

Object-scoped, tamper-evident audit logging for Plone 6.2+ and Python
3.14.

The add-on records structured lifecycle and application events in an
object-local audit stream. Events are validated, sensitive values are
redacted, details are canonically serialized, and each stream has a
local SHA-256 integrity chain.

The integrity chain detects missing or changed records. It is not a
substitute for external immutability, WORM storage, non-repudiation, or
legal advice.

## Features

- public `log_event` application service;
- opt-in content-type lifecycle logging from the control panel;
- create/modify metadata snapshots and before/after diffs;
- Plone toolbar access to the object audit view;
- AG Grid Enterprise server-side row model with search, server-side sorting,
  filtering, pagination, localized timestamps, structured-details popup, and
  CSV/JSON export;
- integrity verification, retention previews, legal holds, and governed
  deletion;
- ZODB storage by default, SQLite for local tests, and PostgreSQL via
  psycopg;
- transactional outbox primitives for retry and dead-letter handling;
- optional asynchronous audit delivery through `collective.taskqueue2`, with
  synchronous delivery kept as the default.

The audit view uses AG Grid Enterprise's server-side row model. A production
deployment therefore needs an AG Grid Enterprise license and the corresponding
license-key configuration; the grid requests only the visible row blocks from
the server.

## Installation and activation

Install the package into the Plone environment using the project's normal
package workflow. Its z3c.autoinclude entry point loads the package ZCML.
Activate the **Persistent audit logger** GenericSetup profile from the Plone
Add-ons control panel. This registers the control panel, object actions,
permissions, registry schema, and lifecycle subscribers.

After activation, open **Persistent audit logging** from Site Setup. The
global switch is enabled by default, but lifecycle logging is opt-in per
content type: no type is logged until it is selected.

## Usage

Log an application event from Python:

```python
from zopyx.plone.persistentlogger.api import log_event

row = log_event(
    context,
    "Invoice approved",
    event_type="business.invoice.approved",
    actor="alice",
    target="INV-42",
    details={"invoice_id": "INV-42", "approval_level": 2},
)
```

When `actor` is omitted, the current Plone user name is used; if no user is
available, the actor is `system`. Details are normalized and redacted before
storage. Avoid passing credentials in comments, targets, or URLs even though
known sensitive detail keys are redacted.

See [docs/USAGE.md](docs/USAGE.md) for lifecycle logging, the audit view,
integrity checks, exports, and retention examples.

Other add-ons can publish a site-wide notification with `zope.event`:

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

The notification subscriber resolves the current Plone site and logs the
event under that site root. Background jobs should pass `site=portal` on the
notification explicitly.

## Plone views and permissions

For an object at `/Plone/path/to/object`:

`@@persistent-log`  
AG Grid audit view.

`@@persistent-log-data`  
JSON data endpoint used by the grid.

`@@persistent-log-export?format=json`  
JSON export.

`@@persistent-log-export?format=csv`  
CSV export.

`@@persistent-log-integrity`  
Integrity status JSON.

`@@persistent-log-retention/preview`  
POST-only retention preview.

`@@persistent-log-retention/delete`  
POST-only governed deletion.

`@@persistentlogger-controlpanel`  
Site-root settings UI; requires `Manage portal`.

`@@persistentlogger-controlpanel-save`  
CSRF-protected settings POST endpoint; requires `Manage portal`.

The object view and data endpoint require `view_audit_log`; exports require
`export_audit_log`; integrity requires `verify_audit_integrity`; retention
preview and deletion require their corresponding permissions. Retention and
control-panel mutations reject non-POST requests and validate CSRF tokens.

## Configuration

Open `@@persistentlogger-controlpanel` as a site manager and select the
content types that should be logged. The settings page is rendered with
the SurveyJS 3 Form Library and saved through a CSRF-protected JSON
endpoint. Logging is disabled for all types until explicitly enabled.
The optional database URL is stored as a password-style control-panel
field and is never returned by health responses.

| Setting | Values / range | Default | Meaning |
|---|---|---:|---|
| `audit_logging_enabled` | Boolean | `true` | Global on/off switch. |
| `backend` | `zodb`, `rdbms` | `zodb` | Storage backend selection. |
| `transaction_mode` | `joined`, `outbox`, `independent` | `outbox` | Transaction behavior for supported adapters. |
| `audit_write_mode` | `sync`, `taskqueue2` | `sync` | Inline persistence or taskqueue2 delivery. |
| `detail_limit` | 1,024–100,000 bytes | `65536` | Maximum serialized details size. |
| `enabled_content_types` | Set of Plone type IDs | empty | Lifecycle types to log. |
| `database_url` | Optional, max 2,048 chars | empty | Password-style RDBMS URL. |

GenericSetup does not overwrite existing registry values during profile
upgrades. See [docs/CONFIGURATION.md](docs/CONFIGURATION.md) for validation,
permissions, and rollout guidance.

All options are global site settings. `audit_logging_enabled` is the master
lifecycle switch; `enabled_content_types` is the lifecycle allow-list;
`detail_limit` rejects oversized normalized details; `backend` and
`transaction_mode` select the storage family and write strategy for supported
adapters; and `database_url` supplies an optional RDBMS connection without
migrating existing ZODB records. Direct `log_event` calls are not disabled by
the lifecycle content-type allow-list.

`audit_write_mode=sync` keeps the normal contract: `log_event()` returns after
the repository accepts the event. `audit_write_mode=taskqueue2` returns a
validated event envelope with `write_status="enqueued"`; the final row is
written by a consumer and may not yet be searchable. Event IDs make retries
idempotent. Install the optional extra and configure the consumer outside the
Plone registry, for example:

```shell
uv sync --extra taskqueue2
export HUEY_TASKQUEUE_URL=redis://localhost:6379/0
export HUEY_CONSUMER=1
```

Use taskqueue2's `HUEY_WORKERS`, `HUEY_WORKER_TYPE`, retry/backoff, and logging
settings for deployment operations. Redis is recommended for production;
SQLite or memory queues are intended for development. Queue failures are
reported and never silently changed to synchronous writes.

## Application API

The main service is:

    from zopyx.plone.persistentlogger.api import log_event

    log_event(
        context,
        "Invoice approved",
        event_type="business.invoice.approved",
        actor="alice",
        details={"invoice_id": "INV-42"},
    )

The API also exposes `search_events`, `verify_integrity`,
`export_events`, `set_retention_policy`, and `repository_for`. All
state-changing retention operations require a preview and a reason.

The full `log_event` signature is:

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

Search supports exact `event_id`, `actor`, `target`, `event_type`,
`severity`, and `schema_version` filters, plus `quick`, `from`, `to`, `sort`,
`limit`, and `offset`. The default limit is 100 and repository limits are
clamped to 1,000. Exports accept JSON or CSV, limit rows to 1–100,000, and
enforce a default 5 MiB byte budget. CSV formula-like values are escaped.

`clear(...)` is intentionally unavailable and raises `ValidationError`; use
retention preview and governed deletion instead. See [docs/API.md](docs/API.md)
for signatures, validation, repository behavior, and retention examples.

The public entry point is `log_event(context, comment, ...)`. Events are
validated, recursively redacted, canonically serialized, and stored in a
lazy ZODB annotation stream by default. The repository boundary is
deliberately small so an RDBMS adapter can provide joined, outbox, or
explicitly independent transaction semantics without changing
application code.

Local SHA-256 chains detect changes and missing links; they do not
provide external immutability, WORM storage, non-repudiation, or legal
admissibility.

## Demo Plone site

The repository includes a UV-based Plone 6.2 bootstrap. It installs
Python 3.14, resolves the optional demo dependencies, creates a Zope
instance, adds the `Plone` site, installs and activates the Barceloneta
theme plus the add-on GenericSetup profile, creates a demo document, and
records an audit event:

    ./scripts/bootstrap-demo.sh

Start the generated instance with the command printed by the script, or:

    uv run runwsgi -v instance/etc/zope.ini

For foreground development mode use:

    make dev
    make dev RELOAD=1

The reload mode uses `watchfiles` to restart the WSGI process when
project files change.

The default development credentials are `admin` / `admin`. Override
`PLONE_PASSWORD` before bootstrapping when using a non-disposable
instance.

## Development

Install the test environment and run the standard checks:

    make install
    make check
    make test
    make coverage
    make integration
    make test-rdbms
    make test-postgres
    make build

The test suite uses pytest with branch coverage. CI enforces 100% package
coverage and builds the distribution on every push and pull request.
The integration target starts a disposable PostgreSQL container with
Testcontainers.

## Releases

Create a version tag and push it to publish through PyPI trusted
publishing:

    git tag -a v1.0.0 -m "Release v1.0.0"
    git push origin v1.0.0

The release workflow expects a configured PyPI `pypi` environment with
trusted publishing enabled for this GitHub repository.
