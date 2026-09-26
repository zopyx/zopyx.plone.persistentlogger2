# Async audit delivery with `collective.taskqueue2`

Status: planning only. This branch contains no implementation changes.

## Objective

Add an optional asynchronous delivery mode for audit-event writes using
`collective.taskqueue2`, while preserving the current synchronous behavior as
the default. The mode must be selectable from the Plone control panel, and the
complete repository test contract must pass in both modes for ZODB and every
RDBMS/reference backend.

The proposed setting is an explicit two-value option:

```text
audit write mode: sync | taskqueue2
default: sync
```

The name may be adjusted during implementation if it better matches the
existing registry vocabulary, but the value must describe delivery mode rather
than be confused with the existing storage `transaction_mode` setting.

## Why this needs a deliberate boundary

The current application service builds a validated `LogEvent` and immediately
calls `repository.append()`. The ZODB adapter writes annotations in the
current request transaction. SQLite, DuckDB, and PostgreSQL adapters execute
their database operations synchronously. Lifecycle subscribers and
`PersistentLoggerNotification` subscribers therefore add audit rows before the
request/event handler returns.

This is useful for the default because the caller receives the durable result
or the write error immediately. It also means that simply putting a decorator
around `repository.append()` would be unsafe: repository instances, ZODB
objects, database connections, request state, and transaction state must not be
passed to a worker.

The async boundary should therefore be after event construction and redaction,
but before repository selection and persistence:

```text
caller/subscriber
  -> build LogEvent and canonical payload
  -> sync: append in the current execution/transaction
  -> taskqueue2: enqueue immutable payload
                    -> worker opens fresh site/database context
                    -> reconstructs event
                    -> appends idempotently
```

This scope is intentionally limited to audit-event append delivery. Search,
count, export, integrity verification, retention previews, legal holds, and
other administrative operations remain synchronous unless a separate feature
request expands the scope.

## Proposed behavior

### Synchronous mode (default)

- Preserve the current `log_event()` result and exception behavior.
- Persist through the configured repository before returning.
- Keep the existing transaction semantics for ZODB and the selected RDBMS.
- No import or runtime dependency on `collective.taskqueue2` is required in
  this mode.

### `taskqueue2` mode

- Construct the complete `LogEvent` before enqueueing it. This includes the
  event ID, timestamps, actor, object/site identity, redacted details, and any
  fields needed for validation and integrity calculation.
- Enqueue only JSON-compatible immutable data plus stable identity information;
  never enqueue live Plone objects, acquisition wrappers, ZODB connections,
  repository instances, request objects, or DB connections.
- The worker must create a fresh execution context and select the repository
  from the same configured backend settings as the originating site.
- The caller receives an accepted/enqueued result, not a claim that the audit
  row is already durable. The public result shape should clearly expose this
  status and the event ID.
- The worker appends using the event ID as an idempotency key. A retry or
  duplicate delivery must not create a second audit row or corrupt the
  integrity chain.
- Worker failures must be retryable and observable. A permanently failing task
  must reach the queue's failure/dead-letter mechanism or an equivalent
  diagnostic path rather than being silently discarded.
- Queue unavailability must have deterministic behavior. The recommended
  initial policy is fail closed in async mode (return an error and do not
  pretend the event was accepted), with no silent fallback to synchronous
  persistence. A fallback would need an explicit setting because it changes
  latency and failure semantics.

### Transaction and ordering decisions

These decisions must be implemented and tested explicitly:

1. Enqueueing is not the same as committing the originating Plone
   transaction. The plan must define whether an event is queued immediately or
   only after a successful transaction commit. For lifecycle events, queueing
   before commit can produce audit records for content changes that later abort;
   queueing only after commit avoids that but needs a transaction hook/outbox
   boundary.
2. Events need a stable event ID assigned before enqueueing so retries are
   idempotent.
3. The integrity chain is serialized by the repository worker. Concurrent
   workers must use the repository's existing conflict/transaction handling;
   the implementation must not calculate a chain digest in the request and
   assume that it remains valid until the worker runs.
4. Object-scoped lifecycle events need a stable UID and enough site identity to
   reopen the site. If the object is deleted before processing, the payload
   still needs to be appendable against the correct scope; it must not depend on
   resolving a live object merely to write the audit row.
5. Site-wide notifications must continue to be logged under the Plone root.
   The subscriber should pass the root/site identity and immutable notification
   payload into the same delivery boundary.

The implementation phase should settle the commit-hook detail against the
actual `collective.taskqueue2`/Huey behavior before code is written. The
default sync mode must not be changed while that decision is being made.

## Plone configuration design

Extend `ISettings`, the GenericSetup registry contract, the control-panel
survey, save validation, and logging diagnostics with an async write mode.

Proposed control-panel behavior:

- Label: `Audit write delivery`
- Choices: `Synchronous` and `collective.taskqueue2`
- Default: `sync`
- Description: async mode requires the add-on and a running configured
  taskqueue consumer; it changes completion semantics from persisted to
  accepted/enqueued.
- Keep the setting separate from `backend` and `transaction_mode`.
- Do not store queue URLs or credentials in this add-on's registry unless
  taskqueue2 explicitly requires it. Prefer taskqueue2's supported deployment
  configuration/environment settings so secrets are not exposed by the
  control panel or health endpoint.
- If async mode is selected without the optional package or without a usable
  queue configuration, surface a clear validation/health error. Do not silently
  change the selected mode.

Because this adds a registry setting, implementation must verify whether the
GenericSetup profile version needs a bump and must preserve existing registry
values during upgrades. Existing control-panel CSRF and POST requirements
remain unchanged.

## `collective.taskqueue2` integration shape

The dependency should be optional so synchronous installations do not gain a
mandatory worker or queue requirement. The implementation should:

1. Confirm a released taskqueue2 version compatible with Plone 6.2 and the
   project's Python target before changing packaging.
2. Add a narrowly named optional dependency extra for async support and the
   matching test dependencies, without making it part of the default install.
3. Define a small task module with a taskqueue2/Huey task that accepts only the
   serialized event envelope and stable site/storage identity.
4. Import taskqueue2 lazily or behind the async-mode path so sync mode remains
   importable and usable when the extra is absent.
5. Use taskqueue2's supported configuration and consumer lifecycle. Document
   the queue URL, consumer enablement, worker count/type, retry/backoff, and
   production backend requirements in the project documentation without
   duplicating credentials into Plone registry settings.
6. Add an application-level adapter/function for enqueueing so the public API
   and subscribers do not depend directly on Huey task invocation details.

`collective.taskqueue2` is a Huey-based Plone task queue and documents Redis,
SQLite, in-memory, and filesystem queue backends. The implementation must still
verify the exact installed API and operational guarantees instead of assuming
that queue acceptance equals durable audit persistence.

## Test strategy: full matrix in both modes

The test suite must exercise the same repository contract in `sync` and
`taskqueue2` modes. Async tests must not be reduced to a single mocked ZODB
case.

### Repository/backend matrix

Run the append, search, filtering, persistence/reopen, idempotency, integrity,
retention, and failure contract for each applicable combination:

| Repository/backend | Sync | Async |
| --- |:---:|:---:|
| `MemoryRepository` reference contract | yes | yes |
| `ZODBRepository` / Plone annotations | yes | yes |
| SQLite | yes | yes |
| DuckDB | yes | yes |
| PostgreSQL/Testcontainers | yes | yes |

The PostgreSQL path remains a separate Testcontainers verification and must not
be replaced by SQLite assertions. Existing Docker/integration skips and
environment gates should remain explicit.

### Required async coverage

- Unit-test the delivery selector: default is sync; the configured async value
  enqueues; invalid values are rejected.
- Verify the exact serialized envelope sent to the queue, including event ID,
  object/site identity, timestamps, normalized/redacted details, and no live
  objects or connections.
- Verify that async request handling does not append before the worker runs and
  reports an accepted/enqueued status.
- Run a worker/consumer integration path for every backend and verify eventual
  persistence and the final `LogEvent` contents.
- Verify duplicate delivery/retry is idempotent and preserves one event and a
  valid integrity chain.
- Verify worker exceptions, malformed payloads, missing target identity, queue
  unavailability, and retry exhaustion have deterministic observable outcomes.
- Verify Plone lifecycle subscribers and site-root notifications in both modes.
- Verify transaction commit and abort behavior, especially for lifecycle events
  and any after-commit enqueue hook.
- Verify concurrent async appends against every backend's transaction/locking
  behavior and integrity verification.
- Keep the existing 100% branch coverage requirement. Add focused tests for
  every new mode/error branch rather than excluding async integration code.

### Test harness design

Use a common delivery-mode fixture/parameter rather than duplicating unrelated
assertions. The taskqueue2 integration fixture should provide an isolated queue
and a controllable consumer so tests can assert both pre-worker and
post-worker states. Tests that need no real worker may use a small taskqueue2
adapter double, but must also include at least one real consumer path per
backend family.

The Plone control-panel tests should use small registry/request doubles where
possible and retain a functional test for profile loading and upgrade behavior.

## Documentation work

Update `README.md`, `docs/CONFIGURATION.md`, `docs/API.md`, and `docs/USAGE.md`
to cover:

- synchronous default and opt-in async semantics;
- accepted/enqueued versus persisted completion status;
- taskqueue2 installation extra and consumer startup/configuration;
- queue backend and worker/retry settings;
- transaction/commit implications and failure handling;
- event ID/idempotency and how to inspect failed tasks;
- production requirements and the fact that queue operation is separate from
  Plone control-panel selection;
- backend/mode test coverage and PostgreSQL integration requirements.

Do not describe async mode as a performance-only switch: it changes when the
audit record becomes durable and how failures reach the caller.

## Implementation sequence

1. Confirm taskqueue2 release/API compatibility and inspect its transaction,
   serialization, retry, and consumer behavior.
2. Extract/define the backend-neutral immutable event envelope and delivery
   interface while preserving the sync path.
3. Add the optional dependency and taskqueue2 adapter/task, including stable
   identity resolution and idempotent worker append.
4. Add the registry setting, control-panel UI/validation, profile upgrade
   handling, and health/diagnostic reporting.
5. Add the parameterized sync/async tests, then backend-specific consumer
   integration tests for Memory, ZODB, SQLite, DuckDB, and PostgreSQL.
6. Run `make check`, `make coverage`, `make integration`, and
   `make test-postgres` as applicable; retain 100% branch coverage.
7. Update all documentation and perform a clean-install smoke test with sync
   mode and an explicitly configured taskqueue2 worker.

## Acceptance criteria

- A fresh installation behaves exactly as synchronous today without
  taskqueue2 installed.
- A Plone administrator can select async delivery in the control panel and see
  a clear validation/health result when the queue is not configured.
- Async requests enqueue an immutable event envelope and do not block on final
  repository persistence; workers persist the event exactly once by event ID.
- ZODB, Memory, SQLite, DuckDB, and PostgreSQL pass the complete contract in
  both modes, including integrity and failure behavior.
- Lifecycle and root notification events are covered in both modes.
- No live Plone/ZODB/database/request objects cross the queue boundary.
- Documentation explains every new configuration option and operational
  consequence.
- The implementation preserves the project's 100% branch coverage gate.
