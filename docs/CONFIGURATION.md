# Configuration reference

Open `@@persistentlogger-controlpanel` at the site root with `Manage portal`.
The SurveyJS form is stored in the Plone registry and saved through the
CSRF-protected `@@persistentlogger-controlpanel-save` POST endpoint.

| Setting | Values / range | Default | Meaning |
|---|---|---:|---|
| `audit_logging_enabled` | Boolean | `true` | Global switch; lifecycle logging also needs selected types. |
| `backend` | `zodb`, `rdbms` | `zodb` | Storage backend selection. |
| `transaction_mode` | `joined`, `outbox`, `independent` | `outbox` | Transaction behavior for supported adapters. |
| `audit_write_mode` | `sync`, `taskqueue2` | `sync` | Event delivery mode; async mode requires the optional taskqueue2 consumer. |
| `detail_limit` | 1,024–100,000 bytes | `65536` | Maximum serialized detail size. |
| `enabled_content_types` | Set of Plone type IDs | empty | Lifecycle types to log. |
| `database_url` | Optional, max 2,048 chars | empty | Password-style RDBMS URL. |

## Global behavior of each option

All settings are site-wide registry values. They are not stored per object and
are not selected independently for individual audit streams.

### `audit_logging_enabled`

This is the global master switch. When `false`, lifecycle logging is disabled
for every content type, even if types remain selected in
`enabled_content_types`. It does not prevent an application from calling
`log_event` directly; callers should apply their own global policy to direct
application events. Default: `true`.

### `backend`

This selects the configured storage family and accepts only `zodb` or
`rdbms`. `zodb` is the default and stores object-local streams in ZODB
annotations. The repository contract also has SQLite, DuckDB, and PostgreSQL
adapters for local, embedded, and integration use. The current standard Plone
service boundary uses `ZODBRepository`; changing this registry value alone
does not migrate existing records or create a database schema.

### `transaction_mode`

This controls how a backend coordinates writes and accepts `joined`, `outbox`,
or `independent`:

- `joined` keeps the write with the current transaction where supported;
- `outbox` treats delivery as retriable work with dead-letter handling; and
- `independent` commits separately where the adapter supports it.

The default is `outbox`. The setting does not retroactively change existing
events or transaction history.

### `audit_write_mode`

`sync` (the default) appends through the repository before `log_event()`
returns. `taskqueue2` serializes and queues the complete event envelope; the
caller receives `write_status="enqueued"`, and a worker later opens a fresh
transaction and appends the event using its original ID. Replayed tasks are
idempotent.

Install the optional `taskqueue2` extra and configure `HUEY_TASKQUEUE_URL` and
`HUEY_CONSUMER=1` in the consumer environment. Queue URLs may use the
taskqueue2-supported Redis, SQLite, memory, or filesystem schemes; use Redis
for production. Queue configuration is intentionally external to the Plone
registry. Selecting async mode without taskqueue2 or a usable queue raises a
configuration error rather than silently falling back to synchronous storage.

### `detail_limit`

This is a global maximum for the UTF-8 serialized size of each event's
`details` value. Valid values are 1,024 through 100,000 bytes; the default is
65,536. It applies after normalization and redaction, and oversized payloads
are rejected rather than truncated. It does not limit comments, targets, or
URLs.

### `enabled_content_types`

This is the global allow-list for lifecycle subscribers. It contains Plone
type IDs such as `Document` and defaults to an empty set. Added and modified
events are logged only when the global switch is enabled and the object's
`portal_type` is in this set. It has no effect on explicit `log_event` calls.

### `database_url`

This is the optional global connection URL for an RDBMS deployment. It may be
empty and is limited to 2,048 characters. The control panel renders it as a
password-style field, and health responses do not expose it. Configure the
matching optional driver and validate connectivity separately; saving a URL
does not migrate ZODB data or change existing repository records.

## Validation and upgrades

The save endpoint requires POST and a valid Plone authenticator token. It
rejects invalid booleans, storage values, transaction modes, detail limits,
and content-type lists. A null content-type selection becomes an empty set.

GenericSetup registers the schema with `purge="False"`, so profile upgrades do
not overwrite existing registry values. Lifecycle logging remains opt-in.

## Permissions

The package defines `view_audit_log`, `search_audit_logs`, `export_audit_log`,
`manage_audit_retention`, `preview_audit_deletion`,
`execute_audit_deletion`, `manage_legal_holds`, and
`verify_audit_integrity`. Site settings require `Manage portal`; state-changing
browser endpoints require POST and CSRF validation in addition to permissions.

## Recommended rollout

1. Install and activate the add-on profile.
2. Enable collection only when the site is ready.
3. Select lifecycle content types explicitly.
4. Confirm the object action and integrity endpoint for a test object.
5. Separate export and retention permissions from view access.
6. Install and verify taskqueue2 plus a consumer before selecting async write
   delivery.
7. Install the matching optional driver and run adapter contract tests before
   production RDBMS use.
