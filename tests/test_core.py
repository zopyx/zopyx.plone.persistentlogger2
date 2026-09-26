from datetime import UTC, datetime, timedelta
import logging
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from zopyx.plone.persistentlogger import api, browser, delivery, subscribers
from zopyx.plone.persistentlogger.errors import (
    ConfigurationError,
    DuplicateEvent,
    HoldConflict,
    IdempotentReplay,
    PreviewExpired,
    ValidationError,
)
from zopyx.plone.persistentlogger.models import (
    LegalHold,
    LogEvent,
    RetentionPolicy,
    Severity,
    utc,
)
from zopyx.plone.persistentlogger.notifications import PersistentLoggerNotification
from zopyx.plone.persistentlogger.outbox import Envelope, Outbox
from zopyx.plone.persistentlogger.rdbms import DuckDBRepository, SQLiteRepository
from zopyx.plone.persistentlogger.async_tasks import (
    _event_from_payload,
    persist_event,
)
from zopyx.plone.persistentlogger.repository import (
    MemoryRepository,
    ZODBRepository,
    object_uid,
)
from zopyx.plone.persistentlogger.serialization import (
    REDACTED,
    bounded_details,
    canonical,
    digest,
    json_bytes,
    normalize,
)


NOW = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def event(
    uid="item", *, comment="changed", created_at=NOW, occurred_at=None, event_id=None, details=None
):
    return LogEvent(
        comment=comment,
        object_uid=uid,
        actor="alice",
        event_type="content.changed",
        created_at=created_at,
        occurred_at=occurred_at,
        event_id=event_id or uuid4(),
        details=details,
    )


def test_serialization_normalizes_and_redacts():
    value = normalize(
        {"password": "secret", "when": NOW, "items": (1, 2), "nested": {"token": "x"}}
    )
    assert value["password"] == REDACTED
    assert value["when"] == str(NOW)
    assert value["items"] == [1, 2]
    assert canonical({"b": 1, "a": 2}) == '{"a":2,"b":1}'
    assert json_bytes({"x": "y"}) == b'{"x":"y"}'
    assert digest({"event_id": "1", "integrity_digest": "ignored"})


@pytest.mark.parametrize(
    "value", [float("inf"), float("-inf"), float("nan"), {1: "bad"}, object()]
)
def test_serialization_rejects_unsafe_values(value):
    with pytest.raises(ValidationError):
        normalize(value)


def test_bounded_details_enforces_size():
    assert bounded_details({"x": "ok"}) == {"x": "ok"}
    with pytest.raises(ValidationError):
        bounded_details({"x": "0123456789"}, limit=5)


def test_models_validate_and_serialize():
    assert utc(datetime(2026, 1, 1, tzinfo=UTC)) == datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(ValidationError):
        utc(datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        LogEvent(comment="", object_uid="x")
    with pytest.raises(ValidationError):
        LogEvent(comment="x", object_uid="x", severity="invalid")
    with pytest.raises(ValidationError):
        LogEvent(comment="x", object_uid="x", info_url="http://unsafe.example")
    relative = LogEvent(comment="x", object_uid="x", info_url="/events/1")
    assert relative.info_url == "/events/1"
    item = LogEvent(comment="x", object_uid="x", severity="warn", occurred_at=NOW)
    assert item.severity is Severity.WARNING
    assert item.to_dict()["event_id"] == str(item.event_id)
    assert item.to_dict()["occurred_at"] == NOW.isoformat()
    policy = RetentionPolicy(
        enabled=True, older_than_days=10, max_entries=5, legal_basis="basis"
    )
    assert policy.enabled
    with pytest.raises(ValidationError):
        RetentionPolicy(older_than_days=0)
    with pytest.raises(ValidationError):
        RetentionPolicy(max_entries=0)
    with pytest.raises(ValidationError):
        LegalHold(object_uid="x", reason="", actor="a")


def test_object_uid_fallbacks(monkeypatch):
    import plone.uuid.interfaces as uuid_interfaces

    with monkeypatch.context() as patcher:
        patcher.setattr(
            uuid_interfaces, "IUUID", lambda _context, _default=None: "plone-uuid"
        )
        assert object_uid(SimpleNamespace(id="id")) == "plone-uuid"
    assert object_uid(SimpleNamespace(object_uid="stable", id="id")) == "stable"
    assert object_uid(SimpleNamespace(__name__="name")) == "name"
    assert object_uid(SimpleNamespace(id="id")) == "id"


def test_memory_repository_append_search_and_integrity():
    repo = MemoryRepository("item")
    first = event(details={"field": "value"})
    row = repo.append(first)
    assert row["sequence"] == 1
    assert repo.search(quick="CHANGED")[0]["event_id"] == str(first.event_id)
    assert repo.search(actor="alice", event_type="content.changed", limit=1)
    assert repo.search(filter_model={"comment": {"type": "contains", "filter": "changed"}})
    assert repo.search(sort_model=[{"colId": "actor", "sort": "asc"}])
    assert repo.count() == 1
    assert repo.verify()["ok"]
    with pytest.raises(IdempotentReplay):
        repo.append(first)
    with pytest.raises(DuplicateEvent):
        repo.append(event(event_id=first.event_id, comment="different"))
    repo._events[0]["comment"] = "tampered"
    assert repo.verify()["ok"] is False
    assert repo.health()["integrity"] is False


def test_memory_repository_server_filter_models():
    repo = MemoryRepository("item")
    repo.append(event(comment="changed invoice"))
    row = repo.search()[0]
    assert repo.search(filter_model={"missing": {"type": "equals", "filter": "x"}})
    assert repo.search(filter_model={"target": {"type": "blank"}})
    assert repo.search(filter_model={"comment": {"type": "notBlank"}})
    assert repo.search(filter_model={"actor": {"type": "set", "values": ["alice"]}})
    assert repo.search(filter_model={"comment": {"type": "equals", "filter": row["comment"]}})
    assert repo.search(filter_model={"actor": {"type": "notEqual", "filter": "bob"}})
    assert repo.search(filter_model={"comment": {"type": "startsWith", "filter": "changed"}})
    assert repo.search(filter_model={"comment": {"type": "endsWith", "filter": "invoice"}})
    assert repo.search(sort_model=[{"colId": "not-a-field", "sort": "asc"}])
    assert repo.search(sort_model=["invalid"])


def test_memory_repository_governance_retention_and_holds():
    repo = MemoryRepository("item")
    old = event(created_at=NOW - timedelta(days=20), comment="old")
    current = event(created_at=NOW, comment="new")
    repo.append(old)
    repo.append(current)
    repo.set_policy(RetentionPolicy(enabled=True, older_than_days=10), actor="admin")
    hold = LegalHold(
        object_uid="item",
        event_id=old.event_id,
        reason="legal",
        actor="admin",
        created_at=NOW,
    )
    repo.create_hold(hold)
    preview = repo.create_preview(actor="admin", now=NOW, ttl_seconds=10**9)
    assert old.event_id not in preview.event_ids
    released = repo.release_hold(hold.hold_id, actor="admin")
    assert released.released_at is not None
    preview = repo.create_preview(actor="admin", now=NOW, ttl_seconds=10**9)
    assert old.event_id in preview.event_ids
    result = repo.delete_preview(
        preview.preview_id, actor="admin", reason="approved retention"
    )
    assert result["deleted"] == 1
    assert repo.verify()["ok"]
    assert repo.governance()
    with pytest.raises(KeyError):
        repo.release_hold(hold.hold_id, actor="admin")
    with pytest.raises(PreviewExpired):
        repo.delete_preview(preview.preview_id, actor="admin", reason="again")


def test_zodb_repository_delete_preview_saves():
    repo = ZODBRepository.__new__(ZODBRepository)
    MemoryRepository.__init__(repo, "item")
    saves = []
    repo._save = lambda: saves.append(True)
    preview = MemoryRepository.create_preview(
        repo, actor="admin", now=datetime.now(UTC), ttl_seconds=60
    )
    assert (
        ZODBRepository.delete_preview(
            repo, preview.preview_id, actor="admin", reason="approved"
        )["deleted"]
        == 0
    )
    assert saves == [True]


def test_hold_conflict_blocks_deletion():
    repo = MemoryRepository("item")
    old = event(created_at=NOW - timedelta(days=20))
    repo.append(old)
    repo.set_policy(RetentionPolicy(enabled=True, older_than_days=10), actor="admin")
    preview = repo.create_preview(actor="admin", now=NOW, ttl_seconds=10**9)
    repo.create_hold(
        LegalHold(
            object_uid="item",
            event_id=old.event_id,
            reason="legal",
            actor="admin",
            created_at=NOW,
        )
    )
    with pytest.raises(HoldConflict):
        repo.delete_preview(preview.preview_id, actor="admin", reason="approved")


def test_sqlite_repository_persists_events(tmp_path):
    path = tmp_path / "audit.sqlite"
    repo = SQLiteRepository("item", str(path))
    repo.append(event())
    assert repo.health()["backend"] == "rdbms"
    repo.close()
    loaded = SQLiteRepository("item", str(path), transaction_mode="joined")
    assert len(loaded.search()) == 1
    loaded.close()
    with pytest.raises(ConfigurationError):
        SQLiteRepository("item", transaction_mode="bad")


def test_outbox_retries_and_dead_letters():
    outbox = Outbox(max_attempts=2)
    item = Envelope("item", {"secret": "value"})
    assert outbox.enqueue(item) is item
    assert outbox.enqueue(item) is item
    calls = []
    result = outbox.deliver(lambda value: calls.append(value))
    assert result["delivered"] == 1
    assert calls == [item]
    failing = Envelope("item", {"x": 1})
    outbox.enqueue(failing)
    now = datetime.now(UTC)
    assert (
        outbox.deliver(lambda _: (_ for _ in ()).throw(RuntimeError()), now=now)[
            "failed"
        ]
        == 1
    )
    assert (
        outbox.deliver(
            lambda _: (_ for _ in ()).throw(RuntimeError()),
            now=now + timedelta(seconds=3),
        )["dead_letter"]
        == 1
    )
    assert failing.failure_class == "RuntimeError"


def test_api_export_and_clear(monkeypatch):
    repo = MemoryRepository("item")
    context = SimpleNamespace(id="item")
    monkeypatch.setattr(api, "repository_for", lambda _: repo)
    api.log_event(context, "=formula", actor="alice", details={"x": 1})
    payload, content_type, filename = api.export_events(context, format="json")
    assert content_type == "application/json"
    assert filename.endswith(".json")
    assert b"formula" in payload
    payload, content_type, filename = api.export_events(context, format="csv")
    assert content_type.startswith("text/csv")
    assert b"'=formula" in payload
    with pytest.raises(ValidationError):
        api.export_events(context, format="xml")
    with pytest.raises(ValidationError):
        api.clear()


class Response:
    def __init__(self):
        self.status = None
        self.headers = {}

    def setStatus(self, value):
        self.status = value

    def setHeader(self, key, value):
        self.headers[key] = value

    def redirect(self, value):
        self.redirect_url = value


class Request:
    method = "GET"

    def __init__(self, form=None):
        self.form = form or {}
        self.response = Response()


def test_browser_adapters(monkeypatch):
    context = SimpleNamespace(absolute_url=lambda: "http://example/obj")
    request = Request({"quick": "title", "startRow": "5", "endRow": "30", "sortModel": "[]", "filterModel": "{}"})
    repo = SimpleNamespace(
        search=lambda **filters: [{"quick": filters["quick"], "offset": filters["offset"], "limit": filters["limit"]}],
        count=lambda **filters: 11,
    )
    monkeypatch.setattr("zopyx.plone.persistentlogger.api.repository_for", lambda _: repo)
    result = browser.AuditData(context, request)()
    assert '"total": 11' in result
    assert '"offset": 5' in result
    assert request.response.headers["Content-Type"].startswith("application/json")
    view = browser.AuditLog(context, request)
    assert view.data_url.endswith("@@persistent-log-data")
    assert view.export_url.endswith("@@persistent-log-export")
    monkeypatch.setattr(browser, "verify_integrity", lambda _: {"ok": True})
    assert '"ok": true' in browser.AuditIntegrity(context, Request())()
    monkeypatch.setattr(
        browser, "export_events", lambda *_args, **_kw: (b"x", "text/csv", "audit.csv")
    )
    export_request = Request({"format": "csv"})
    assert browser.AuditExport(context, export_request)() == b"x"
    assert export_request.response.headers["Content-Disposition"].endswith(
        '"audit.csv"'
    )
    with pytest.raises(ValidationError):
        browser._post(Request())


def test_browser_audit_data_validates_server_requests(monkeypatch):
    context = SimpleNamespace(id="item")
    repo = SimpleNamespace(search=lambda **_: [], count=lambda **_: 0)
    monkeypatch.setattr("zopyx.plone.persistentlogger.api.repository_for", lambda _: repo)
    with pytest.raises(ValidationError):
        browser.AuditData(context, Request({"startRow": "bad"}))()
    with pytest.raises(ValidationError):
        browser.AuditData(context, Request({"sortModel": "bad"}))()
    with pytest.raises(ValidationError):
        browser.AuditData(context, Request({"filterModel": "bad"}))()
    assert '"total": 0' in browser.AuditData(context, Request({"startRow": "2"}))()


def test_subscriber_diff_helpers():
    class Item:
        portal_type = "Document"
        id = "doc"
        values = {"title": "before", "description": "body"}

        def get(self, name):
            return self.values.get(name)

    item = Item()
    before = subscribers._metadata_snapshot(item)
    item.values["title"] = "after"
    details = subscribers._event_details(item, before=before)
    assert details["changes"]["changed"]["title"] == {
        "before": "before",
        "after": "after",
    }
    event_object = SimpleNamespace(
        descriptions=[SimpleNamespace(attributes=("title",), keys=("subject",))]
    )
    assert subscribers._changed_fields(event_object) == ["subject", "title"]


def test_site_notification_subscriber_logs_at_site_root(monkeypatch):
    site = SimpleNamespace(id="Plone")
    logged = []
    monkeypatch.setattr(subscribers, "log_event", lambda *args, **kwargs: logged.append((args, kwargs)))
    notification = PersistentLoggerNotification(
        "Invoice approved",
        event_type="business.invoice.approved",
        actor="alice",
        details={"invoice_id": "INV-42"},
        site=site,
    )
    subscribers.log_notification(notification)
    assert logged == [
        ((site, "Invoice approved"), {
            "event_type": "business.invoice.approved",
            "severity": "info",
            "actor": "alice",
            "target": None,
            "info_url": None,
            "details": {"invoice_id": "INV-42"},
            "occurred_at": None,
        })
    ]
    monkeypatch.setattr(subscribers, "getSite", lambda: site)
    subscribers.log_notification(PersistentLoggerNotification("Uses current site"))
    assert logged[-1][0][0] is site
    monkeypatch.setattr(subscribers, "getSite", lambda: None)
    subscribers.log_notification(PersistentLoggerNotification("No site"))
    assert len(logged) == 2


def test_api_services_and_export_limits(monkeypatch):
    repo = MemoryRepository("item")
    context = SimpleNamespace(id="item")
    monkeypatch.setattr(api, "repository_for", lambda _: repo)
    monkeypatch.setattr(
        "plone.api.user.get_current",
        lambda: SimpleNamespace(getUserName=lambda: "api-user"),
    )
    row = api.log_event(context, "service event")
    assert row["actor"] == "api-user"
    assert api.search_events(context, limit=1)
    assert api.verify_integrity(context)["ok"]
    policy = RetentionPolicy(legal_basis="test")
    assert api.set_retention_policy(context, policy, actor="admin") is policy
    with pytest.raises(ValidationError):
        api.export_events(context, max_rows=0)
    with pytest.raises(ValidationError):
        api.export_events(context, max_bytes=1)
    monkeypatch.setattr("plone.api.user.get_current", lambda: None)
    assert api._current_actor() == "system"


def test_async_delivery_enqueues_immutable_event_and_worker_is_idempotent(monkeypatch):
    repo = MemoryRepository("item")
    site = SimpleNamespace(getPhysicalPath=lambda: ("", "Plone"))
    context = SimpleNamespace(
        id="item", getPhysicalPath=lambda: ("", "Plone", "item")
    )
    settings = SimpleNamespace(audit_write_mode="taskqueue2")
    queued = []
    monkeypatch.setattr(
        "zopyx.plone.persistentlogger.controlpanel._registry",
        lambda: SimpleNamespace(forInterface=lambda *_a, **_kw: settings),
    )
    monkeypatch.setattr(api, "repository_for", lambda _: repo)
    monkeypatch.setattr(
        "zope.component.hooks.getSite", lambda: site
    )
    monkeypatch.setattr(delivery, "_task", lambda: lambda **payload: queued.append(payload))

    result = api.log_event(context, "queued", details={"token": "secret"})

    assert result["write_status"] == "enqueued"
    assert repo.search() == []
    assert queued[0]["event"]["details"] == {"token": "[REDACTED]"}
    assert all(isinstance(value, (str, dict)) for value in queued[0].values())

    first = persist_event(repo, _event_from_payload(queued[0]["event"]))
    second = persist_event(repo, _event_from_payload(queued[0]["event"]))
    assert first["write_status"] == "accepted"
    assert second["write_status"] == "already_persisted"
    assert len(repo.search()) == 1
    assert repo.verify()["ok"]


@pytest.mark.parametrize("mode", ["sync", "taskqueue2"])
def test_zodb_and_reference_repositories_support_both_delivery_modes(
    monkeypatch, mode
):
    stores = {}
    monkeypatch.setattr(
        "zope.annotation.interfaces.IAnnotations", lambda _context: stores
    )
    repo = ZODBRepository(SimpleNamespace(id="item"))
    context = SimpleNamespace(
        id="item", getPhysicalPath=lambda: ("", "Plone", "item")
    )
    settings = SimpleNamespace(audit_write_mode=mode)
    monkeypatch.setattr(
        "zopyx.plone.persistentlogger.controlpanel._registry",
        lambda: SimpleNamespace(forInterface=lambda *_a, **_kw: settings),
    )
    queued = []
    monkeypatch.setattr(api, "repository_for", lambda _: repo)
    monkeypatch.setattr(delivery, "_task", lambda: lambda **payload: queued.append(payload))

    result = api.log_event(context, f"{mode} event")
    if mode == "taskqueue2":
        assert result["write_status"] == "enqueued"
        result = persist_event(repo, _event_from_payload(queued[0]["event"]))
    assert result["object_uid"] == "item"
    assert repo.verify()["ok"]


def test_async_delivery_requires_paths_and_taskqueue(monkeypatch):
    context = SimpleNamespace(id="item")
    with pytest.raises(ConfigurationError):
        delivery.event_envelope(context, event())
    monkeypatch.setattr(delivery, "event_envelope", lambda *_args: {"event": event().to_dict()})
    from zopyx.plone.persistentlogger import async_tasks

    monkeypatch.setattr(
        async_tasks,
        "persist_audit_event",
        lambda **_: (_ for _ in ()).throw(ConfigurationError("not installed")),
    )
    monkeypatch.setattr(delivery, "_task", lambda: async_tasks.persist_audit_event)
    with pytest.raises(ConfigurationError, match="not installed"):
        delivery.enqueue(context, event())


def test_async_payload_and_worker_transaction_paths(monkeypatch):
    import transaction
    import Zope2

    original_manager = transaction.manager
    calls = []
    monkeypatch.setattr(
        original_manager,
        "begin",
        lambda: calls.append("begin"),
    )
    monkeypatch.setattr(original_manager, "commit", lambda: calls.append("commit"))
    monkeypatch.setattr(original_manager, "abort", lambda: calls.append("abort"))

    class Jar:
        def close(self):
            calls.append("close")

    class Site:
        def restrictedTraverse(self, path, default=None):
            return SimpleNamespace() if path == "/Plone/item" else default

    app = SimpleNamespace(
        restrictedTraverse=lambda path, default=None: Site()
        if path == "/Plone"
        else default,
        _p_jar=Jar(),
    )
    monkeypatch.setattr(Zope2, "app", lambda: app)
    monkeypatch.setattr("zope.component.hooks.setSite", lambda site: calls.append(site))
    monkeypatch.setattr(
        "zopyx.plone.persistentlogger.api.repository_for",
        lambda _: MemoryRepository("item"),
    )
    from zopyx.plone.persistentlogger.async_tasks import _persist_audit_event

    payload = {
        "site_path": "/Plone",
        "context_path": "/Plone/item",
        "event": event(occurred_at=NOW).to_dict(),
    }
    result = _persist_audit_event(payload)
    assert result["object_uid"] == "item"
    assert calls[0] == "begin"
    assert "commit" in calls
    assert calls[-1] == "close"

    app.restrictedTraverse = lambda _path, default=None: default
    with pytest.raises(ValueError, match="No Plone site"):
        _persist_audit_event(payload)
    assert calls[-2:] == ["abort", "close"]


def test_async_worker_aborts_when_context_is_missing(monkeypatch):
    import transaction
    import Zope2

    calls = []
    monkeypatch.setattr(transaction.manager, "begin", lambda: calls.append("begin"))
    monkeypatch.setattr(transaction.manager, "abort", lambda: calls.append("abort"))
    monkeypatch.setattr(transaction.manager, "commit", lambda: calls.append("commit"))

    class Jar:
        def close(self):
            calls.append("close")

    class Site:
        restrictedTraverse = lambda self, _path, default=None: default

    app = SimpleNamespace(
        restrictedTraverse=lambda _path, default=None: Site(), _p_jar=Jar()
    )
    monkeypatch.setattr(Zope2, "app", lambda: app)
    monkeypatch.setattr("zope.component.hooks.setSite", lambda _site: None)
    from zopyx.plone.persistentlogger.async_tasks import _persist_audit_event

    with pytest.raises(ValueError, match="No context"):
        _persist_audit_event(
            {"site_path": "/Plone", "context_path": "/Plone/item", "event": event().to_dict()}
        )
    assert calls == ["begin", "abort", "close"]


def test_async_persist_event_replay_without_existing_row():
    class Repository:
        def append(self, _event):
            from zopyx.plone.persistentlogger.errors import IdempotentReplay

            raise IdempotentReplay("missing")

        def get(self, _event_id):
            return None

    with pytest.raises(IdempotentReplay):
        persist_event(Repository(), event())


def test_delivery_fallbacks(monkeypatch):
    context = SimpleNamespace(
        getPhysicalPath=lambda: ("", "Plone", "item")
    )
    monkeypatch.setattr("zope.component.hooks.getSite", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert delivery.event_envelope(context, event())["site_path"] == "/Plone/item"
    real_task = delivery._task
    assert real_task()
    monkeypatch.setattr(delivery, "_task", lambda: lambda **_: (_ for _ in ()).throw(RuntimeError()))
    with pytest.raises(ConfigurationError, match="could not accept"):
        delivery.enqueue(context, event())
    from zopyx.plone.persistentlogger import async_tasks
    monkeypatch.setattr(
        async_tasks,
        "persist_audit_event",
        lambda **_: (_ for _ in ()).throw(ConfigurationError("not installed")),
    )
    monkeypatch.setattr(delivery, "_task", lambda: async_tasks.persist_audit_event)
    with pytest.raises(ConfigurationError, match="not installed"):
        delivery.enqueue(context, event())


def test_api_actor_failure_and_repository_selection(monkeypatch):
    context = SimpleNamespace(id="item")
    monkeypatch.setattr(
        "plone.api.user.get_current",
        lambda: (_ for _ in ()).throw(RuntimeError("no user")),
    )
    assert api._current_actor() == "system"
    monkeypatch.setattr(api, "ZODBRepository", lambda _: MemoryRepository("item"))
    repo = api.repository_for(context)
    assert repo.object_uid == "item"


def test_browser_rendering_exports_and_mutations(monkeypatch):
    context = SimpleNamespace(absolute_url=lambda: "http://example/obj", id="item")
    request = Request()
    monkeypatch.setattr(browser.AuditLog, "index", lambda self: "rendered")
    view = browser.AuditLog(context, request)
    assert view() == "rendered"
    assert view.timezone == "UTC"
    monkeypatch.setattr(
        "zope.component.queryUtility",
        lambda *_args, **_kwargs: SimpleNamespace(
            get=lambda key, default: "Europe/Amsterdam"
        ),
    )
    assert view.timezone == "Europe/Amsterdam"
    monkeypatch.setattr(
        browser,
        "export_events",
        lambda *_args, **_kw: (b"{}", "application/json", "audit.json"),
    )
    export = browser.AuditExport(context, Request())()
    assert export == b"{}"
    assert request.response.status is None
    post = Request()
    post.method = "POST"
    monkeypatch.setitem(
        sys.modules,
        "plone.protect.interfaces",
        SimpleNamespace(
            ICheckAuthenticator=lambda _: SimpleNamespace(validate=lambda: None)
        ),
    )
    browser._post(post)


def test_browser_retention_views(monkeypatch):
    context = SimpleNamespace(id="item")
    preview_id = uuid4()
    fake = SimpleNamespace(
        preview_id=preview_id,
        event_ids=(uuid4(),),
        selection_digest="digest",
        expires_at=NOW + timedelta(minutes=1),
    )
    repo = SimpleNamespace(
        create_preview=lambda **_: fake,
        delete_preview=lambda *args, **kwargs: {"deleted": 1},
    )
    monkeypatch.setattr(
        "zopyx.plone.persistentlogger.api.repository_for", lambda _: repo
    )
    request = Request({"actor": "admin"})
    request.method = "POST"
    assert "preview_id" in browser.AuditRetentionPreview(context, request)()
    delete_request = Request(
        {"preview_id": str(preview_id), "actor": "admin", "reason": "approved"}
    )
    delete_request.method = "POST"
    assert '"deleted": 1' in browser.AuditRetentionDelete(context, delete_request)()
    missing = Request()
    missing.method = "POST"
    with pytest.raises(ValidationError):
        browser.AuditRetentionDelete(context, missing)()
    with pytest.raises(ValidationError):
        browser.AuditRetentionDelete(context, Request()).__call__()


def test_controlpanel_settings_and_logging(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="zopyx.plone.persistentlogger.controlpanel")
    context = SimpleNamespace(portal_type="Document")
    monkeypatch.setattr("zope.component.queryUtility", lambda *_args, **_kwargs: None)
    from zopyx.plone.persistentlogger import controlpanel

    assert controlpanel.enabled_content_types(context) == frozenset()
    assert not controlpanel.logging_enabled(context)
    monkeypatch.setattr(
        "zope.component.queryUtility",
        lambda *_args, **_kwargs: SimpleNamespace(
            forInterface=lambda *_a, **_kw: SimpleNamespace(
                enabled_content_types=("Document",)
            )
        ),
    )
    assert controlpanel.enabled_content_types(context) == {"Document"}
    assert controlpanel.logging_enabled(context)
    assert controlpanel.LoggingEnabled(context, Request())()
    settings = SimpleNamespace(
        backend="zodb",
        transaction_mode="outbox",
        detail_limit=65536,
        enabled_content_types={"Document"},
        database_url="",
    )
    monkeypatch.setattr(
        "zope.component.queryUtility",
        lambda *_args, **_kwargs: SimpleNamespace(
            forInterface=lambda *_a, **_kw: settings
        ),
    )
    type_info = SimpleNamespace(Title=lambda: "Document")
    monkeypatch.setattr(
        controlpanel,
        "getToolByName",
        lambda *_args: SimpleNamespace(
            listContentTypes=lambda: ["Document"], get=lambda _: type_info
        ),
    )
    view = controlpanel.AuditLoggingControlPanel(
        SimpleNamespace(absolute_url=lambda: "http://example/Plone"),
        Request({"_authenticator": "token"}),
    )
    survey = view.survey()
    assert "Loading audit control-panel settings" in caplog.text
    assert survey["data"]["backend"] == "zodb"
    assert survey["data"]["audit_logging_enabled"] is True
    assert survey["pages"][0]["elements"][0]["defaultValue"] is True
    assert survey["pages"][0]["elements"][1]["elements"][0]["defaultValue"] == "zodb"
    assert survey["data"]["transaction_mode"] == "outbox"
    assert survey["data"]["audit_write_mode"] == "sync"
    assert survey["pages"][0]["elements"][1]["elements"][1]["defaultValue"] == "outbox"
    assert survey["data"]["enabled_content_types"] == ["Document"]
    questions = [
        item
        for panel in survey["pages"][0]["elements"]
        if "elements" in panel
        for item in panel["elements"]
    ]
    database_question = next(
        item for item in questions if item["name"] == "database_url"
    )
    assert database_question["visibleIf"] == "{backend} = 'rdbms'"
    content_types_question = next(
        item for item in questions if item["name"] == "enabled_content_types"
    )
    assert content_types_question["colCount"] == 3
    settings.audit_logging_enabled = False
    assert not controlpanel.logging_enabled(context)
    assert view.save_url.endswith("_authenticator=token")
    assert view.redirect_url.endswith("@@persistentlogger-controlpanel")
    monkeypatch.setattr("plone.protect.createToken", lambda: "generated")
    no_token_view = controlpanel.AuditLoggingControlPanel(view.context, Request())
    assert no_token_view.save_url.endswith("_authenticator=generated")
    view.index = lambda: "rendered"
    assert view() == "rendered"


def test_controlpanel_save(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="zopyx.plone.persistentlogger.controlpanel")
    from io import StringIO
    from zopyx.plone.persistentlogger import controlpanel

    settings = SimpleNamespace()
    registry = SimpleNamespace(
        forInterface=lambda *_args, **_kwargs: settings,
        registerInterface=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(controlpanel, "_registry", lambda: registry)
    monkeypatch.setattr(
        "Products.statusmessages.interfaces.IStatusMessage",
        lambda _: SimpleNamespace(addStatusMessage=lambda *_args, **_kwargs: None),
    )

    class BodyRequest(Request):
        method = "POST"

        def __init__(self, payload):
            super().__init__()
            self.stdin = StringIO(payload)

    context = SimpleNamespace(absolute_url=lambda: "http://example/Plone")
    result = controlpanel.AuditLoggingControlPanelSave(
        context,
        BodyRequest(
            '{"backend":"rdbms","transaction_mode":"joined","detail_limit":4096,'
            '"enabled_content_types":["Document"],"database_url":"postgresql://db"}'
        ),
    )()
    assert result == ""
    assert "Saving audit control-panel settings" in caplog.text
    assert settings.backend == "rdbms"
    assert settings.audit_write_mode == "sync"
    assert settings.enabled_content_types == {"Document"}
    controlpanel.AuditLoggingControlPanelSave(
        context,
        BodyRequest(
            '{"backend":"zodb","transaction_mode":"outbox","detail_limit":65536,'
            '"enabled_content_types":[]}'
        ),
    )()
    assert settings.enabled_content_types == set()
    controlpanel.AuditLoggingControlPanelSave(
        context,
        BodyRequest(
            '{"backend":"zodb","transaction_mode":"outbox","enabled_content_types":null}'
        ),
    )()
    assert settings.enabled_content_types == set()
    for payload in (
        "not-json",
        '{"audit_logging_enabled":"yes"}',
        '{"backend":"invalid"}',
        '{"detail_limit":1}',
        '{"detail_limit":100001}',
        '{"detail_limit":"bad"}',
        '{"enabled_content_types":"Document"}',
        '{"audit_write_mode":"invalid"}',
    ):
        with pytest.raises(ValidationError):
            controlpanel.AuditLoggingControlPanelSave(context, BodyRequest(payload))()
    monkeypatch.setattr(controlpanel, "_registry", lambda: None)
    with pytest.raises(ValidationError):
        controlpanel.AuditLoggingControlPanelSave(context, BodyRequest("{}"))()
    with pytest.raises(ValidationError):
        controlpanel.AuditLoggingControlPanelSave(context, Request())()


def test_subscriber_lifecycle_paths(monkeypatch):
    class Item:
        portal_type = "Document"
        id = "doc"
        values = {"title": "after"}

        def get(self, name):
            return self.values.get(name)

    item = Item()
    logged = []
    monkeypatch.setattr(subscribers, "logging_enabled", lambda _: True)
    monkeypatch.setattr(
        subscribers, "log_event", lambda *args, **kwargs: logged.append((args, kwargs))
    )
    monkeypatch.setattr(subscribers, "_actor", lambda: "editor")
    monkeypatch.setattr(subscribers, "search_events", lambda *_a, **_kw: [])
    subscribers.log_added(item, SimpleNamespace())
    subscribers.log_modified(item, SimpleNamespace(descriptions=[]))
    assert len(logged) == 2
    monkeypatch.setattr(subscribers, "logging_enabled", lambda _: False)
    subscribers.log_added(item, SimpleNamespace())
    assert len(logged) == 2
    assert (
        subscribers._metadata_diff(None, {"title": "x"})["baseline_available"] is False
    )
    assert subscribers._metadata_value(object()) is None
    monkeypatch.setattr(
        subscribers,
        "search_events",
        lambda *_a, **_kw: [{"details": {"snapshot_after": {"title": "before"}}}],
    )
    assert subscribers._previous_snapshot(item) == {"title": "before"}


def test_subscriber_historical_and_actor_fallbacks(monkeypatch):
    class Item:
        portal_type = "Document"
        id = "doc"
        title = "before"

    class Connection:
        def get(self, _oid):
            return Item()

        def close(self):
            self.closed = True

    class DB:
        def history(self, _oid, size=1):
            return [{"id": b"tx"}]

        def open(self, before=None):
            return Connection()

    item = Item()
    item._p_oid = b"oid"
    item._p_jar = SimpleNamespace(db=lambda: DB())
    assert subscribers._historical_snapshot(item)["title"] == "before"
    item._p_jar = SimpleNamespace(
        db=lambda: SimpleNamespace(history=lambda *_a, **_kw: [])
    )
    assert subscribers._historical_snapshot(item) is None
    monkeypatch.setattr(
        "plone.api.user.get_current", lambda: (_ for _ in ()).throw(RuntimeError())
    )
    assert subscribers._actor() == "system"
    monkeypatch.setattr(
        "plone.api.user.get_current",
        lambda: SimpleNamespace(getUserName=lambda: "editor"),
    )
    assert subscribers._actor() == "editor"


def test_repository_filters_retention_and_validation():
    repo = MemoryRepository("item")
    with pytest.raises(ValueError):
        repo.append(event(uid="other"))
    old = event(created_at=NOW - timedelta(days=20), comment="old", event_id=uuid4())
    new = event(created_at=NOW, comment="new", event_id=uuid4())
    repo.append(old)
    repo.append(new)
    assert repo.get_policy().enabled is False
    assert repo.create_preview(actor="admin", now=NOW).event_ids == ()
    assert repo.search(
        **{
            "from": NOW - timedelta(days=1),
            "to": NOW,
            "sort": "asc",
            "offset": 0,
            "limit": 1,
        }
    )[0]["event_id"] == str(new.event_id)
    repo.set_policy(
        RetentionPolicy(enabled=True, older_than_days=10, max_entries=1), actor="admin"
    )
    preview = repo.create_preview(actor="admin", now=NOW, ttl_seconds=1)
    assert old.event_id in preview.event_ids
    with pytest.raises(PreviewExpired):
        repo.delete_preview(preview.preview_id, actor="wrong", reason="ok")
    with pytest.raises(PreviewExpired):
        repo.delete_preview(preview.preview_id, actor="admin", reason="")
    with pytest.raises(ValueError):
        repo.create_hold(
            LegalHold(object_uid="other", reason="x", actor="a", created_at=NOW)
        )


def test_repository_zodb_annotation_roundtrip(monkeypatch):
    stores = {}
    monkeypatch.setattr(
        "zope.annotation.interfaces.IAnnotations", lambda _context: stores
    )
    context = SimpleNamespace(id="item")
    from zopyx.plone.persistentlogger.repository import ZODBRepository

    repo = ZODBRepository(context)
    repo.append(event())
    assert stores
    loaded = ZODBRepository(context)
    assert len(loaded.search()) == 1
    policy = RetentionPolicy(enabled=True, legal_basis="basis")
    loaded.set_policy(policy, actor="admin")
    hold = LegalHold(object_uid="item", reason="case", actor="admin", created_at=NOW)
    loaded.create_hold(hold)
    loaded.create_preview(actor="admin", now=NOW)
    loaded.release_hold(hold.hold_id, actor="admin")
    assert ZODBRepository(context).get_policy().enabled


def test_models_and_serialization_edge_cases():
    with pytest.raises(ValidationError):
        LogEvent(comment="x", object_uid="x", target="x" * 2049)
    with pytest.raises(ValidationError):
        LogEvent(comment="x", object_uid="x", event_id="bad")
    with pytest.raises(ValidationError):
        LogEvent(comment="x", object_uid="x", schema_version=True)
    with pytest.raises(ValidationError):
        RetentionPolicy(enabled="yes")
    with pytest.raises(ValidationError):
        LegalHold(
            object_uid="x", reason="r", actor="a", released_at=NOW, released_by=""
        )
    preview = __import__(
        "zopyx.plone.persistentlogger.models", fromlist=["DeletionPreview"]
    ).DeletionPreview(uuid4(), "x", (), "d", "a", NOW, NOW + timedelta(seconds=1))
    assert not preview.is_expired(NOW)
    assert preview.is_expired(NOW + timedelta(seconds=1))


def test_outbox_and_sqlite_edges(tmp_path):
    outbox = Outbox(max_attempts=1)
    item = Envelope("item", {})
    outbox.enqueue(item)
    assert (
        outbox.deliver(
            lambda _: (_ for _ in ()).throw(ValueError()), now=datetime.now(UTC)
        )["dead_letter"]
        == 1
    )
    delayed = Envelope("item", {})
    delayed.next_attempt_at = datetime.now(UTC) + timedelta(hours=1)
    outbox.enqueue(delayed)
    assert outbox.deliver(lambda _: None, now=datetime.now(UTC))["delivered"] == 0
    repo = SQLiteRepository(
        "item", str(tmp_path / "x.sqlite"), transaction_mode="independent"
    )
    assert repo.health()["transaction_mode"] == "independent"
    repo.close()


def test_duckdb_rejects_invalid_transaction_mode():
    with pytest.raises(ConfigurationError):
        DuckDBRepository("item", transaction_mode="invalid")


def test_remaining_defensive_branches(monkeypatch, tmp_path):
    from zopyx.plone.persistentlogger import repository, serialization

    monkeypatch.setattr(
        "plone.uuid.interfaces.IUUID",
        lambda *_args: (_ for _ in ()).throw(RuntimeError()),
    )
    assert repository.object_uid(SimpleNamespace(id="fallback")) == "fallback"
    with pytest.raises(ValidationError):
        serialization.canonical(object())
    with pytest.raises(ValidationError):
        serialization.canonical({"bad": object()})

    class Broken:
        portal_type = "Document"
        id = "doc"

        def get(self, _name):
            raise RuntimeError("broken")

    assert subscribers._metadata_snapshot(Broken())["portal_type"] == "Document"
    monkeypatch.setattr(
        subscribers, "search_events", lambda *_a, **_kw: [{"details": "not a mapping"}]
    )
    assert subscribers._previous_snapshot(Broken()) is None
    repo = SQLiteRepository("item", str(tmp_path / "delete.sqlite"))
    old = event(created_at=NOW - timedelta(days=20))
    repo.append(old)
    repo.set_policy(RetentionPolicy(enabled=True, older_than_days=10), actor="admin")
    preview = repo.create_preview(actor="admin", now=NOW, ttl_seconds=10**9)
    repo.delete_preview(preview.preview_id, actor="admin", reason="approved")
    assert repo.search() == []
    repo.close()
    assert serialization.normalize(1.5) == 1.5


def test_subscriber_reentrancy_and_changed_fields(monkeypatch):
    item = SimpleNamespace(portal_type="Document", id="doc", get=lambda name: None)
    monkeypatch.setattr(subscribers, "logging_enabled", lambda _: True)
    monkeypatch.setattr(subscribers, "log_event", lambda *_a, **_kw: None)
    token = subscribers._WRITING_AUDIT.set(True)
    try:
        subscribers.log_modified(item, SimpleNamespace(descriptions=[]))
    finally:
        subscribers._WRITING_AUDIT.reset(token)
    calls = []
    monkeypatch.setattr(subscribers, "search_events", lambda *_a, **_kw: [])
    monkeypatch.setattr(subscribers, "_historical_snapshot", lambda _: None)
    monkeypatch.setattr(
        subscribers,
        "log_event",
        lambda *args, **kwargs: calls.append(kwargs["details"]),
    )
    subscribers.log_modified(
        item,
        SimpleNamespace(descriptions=[SimpleNamespace(attributes=("title",), keys=())]),
    )
    assert calls[0]["changed_fields"] == ["title"]
