"""Repository contract and object-local stores.

The application services use this module's contract only.  The in-memory
implementation is intentionally complete enough for contract and failure
tests; the ZODB implementation persists the same state lazily in annotations.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID, uuid4

from .errors import DuplicateEvent, HoldConflict, IdempotentReplay, PreviewExpired
from .models import DeletionPreview, LegalHold, LogEvent, RetentionPolicy
from .serialization import canonical, digest

ANNOTATION_KEY = "zopyx.plone.persistentlogger.next"


def object_uid(context: Any) -> str:
    """Return a stable identity, preferring Plone UUID over a movable path."""
    try:
        from plone.uuid.interfaces import IUUID
        value = IUUID(context, None)
        if value:
            return str(value)
    except Exception:
        pass
    value = getattr(context, "object_uid", None)
    if value:
        return str(value)
    name = getattr(context, "__name__", None)
    return str(name or getattr(context, "id", "unknown"))


class LogRepository(Protocol):
    object_uid: str

    def append(self, event: LogEvent) -> dict[str, Any]: ...
    def search(self, **filters: Any) -> list[dict[str, Any]]: ...
    def count(self, **filters: Any) -> int: ...
    def get(self, event_id: UUID | str) -> dict[str, Any] | None: ...
    def set_policy(self, policy: RetentionPolicy, *, actor: str) -> RetentionPolicy: ...
    def get_policy(self) -> RetentionPolicy: ...
    def create_preview(self, *, actor: str, now: datetime | None = None, ttl_seconds: int = 300) -> DeletionPreview: ...
    def delete_preview(self, preview_id: UUID | str, *, actor: str, reason: str) -> dict[str, Any]: ...
    def create_hold(self, hold: LegalHold) -> LegalHold: ...
    def list_holds(self, *, active_only: bool = False) -> list[LegalHold]: ...
    def release_hold(self, hold_id: UUID | str, *, actor: str) -> LegalHold: ...
    def verify(self) -> dict[str, Any]: ...
    def health(self) -> dict[str, Any]: ...


def _row(event: LogEvent, sequence: int, previous_digest: str) -> dict[str, Any]:
    result = event.to_dict()
    result.update(sequence=sequence, previous_digest=previous_digest)
    result["integrity_digest"] = digest(result)
    return result


class MemoryRepository:
    """Reference repository with no persistence internals for tests."""

    transaction_mode = "joined"
    backend = "memory"

    def __init__(self, object_uid_value: str):
        self.object_uid = object_uid_value
        self._events: list[dict[str, Any]] = []
        self._policy = RetentionPolicy()
        self._holds: dict[UUID, LegalHold] = {}
        self._previews: dict[UUID, DeletionPreview] = {}
        self._governance: list[dict[str, Any]] = []

    def append(self, event: LogEvent) -> dict[str, Any]:
        if event.object_uid != self.object_uid:
            raise ValueError("event object_uid does not match repository scope")
        existing = self.get(event.event_id)
        if existing:
            candidate = _row(event, existing["sequence"], existing["previous_digest"])
            if canonical(candidate) == canonical(existing):
                raise IdempotentReplay(str(event.event_id))
            raise DuplicateEvent(str(event.event_id))
        row = _row(event, len(self._events) + 1, self._events[-1]["integrity_digest"] if self._events else "")
        self._events.append(row)
        return {**row, "write_status": "accepted"}

    def get(self, event_id: UUID | str) -> dict[str, Any] | None:
        value = str(event_id)
        return next((dict(row) for row in self._events if row["event_id"] == value), None)

    def search(self, **filters: Any) -> list[dict[str, Any]]:
        rows = list(self._events)
        exact = ("event_id", "actor", "target", "event_type", "severity", "schema_version")
        for key in exact:
            if filters.get(key) is not None:
                value = str(filters[key]) if key == "event_id" else filters[key]
                rows = [row for row in rows if row.get(key) == value]
        if filters.get("from"):
            start = _parse_time(filters["from"])
            rows = [row for row in rows if _parse_time(row["created_at"]) >= start]
        if filters.get("to"):
            end = _parse_time(filters["to"])
            rows = [row for row in rows if _parse_time(row["created_at"]) <= end]
        quick = filters.get("quick")
        if quick:
            needle = str(quick).casefold()
            rows = [row for row in rows if needle in (row["comment"] + " " + row["actor"] + " " + row["event_type"]).casefold()]
        rows.sort(key=lambda row: (row["created_at"], row["sequence"], row["event_id"]), reverse=filters.get("sort", "desc") != "asc")
        limit = min(max(int(filters.get("limit", 100)), 0), 1000)
        offset = max(int(filters.get("offset", 0)), 0)
        return [dict(row) for row in rows[offset:offset + limit]]

    def count(self, **filters: Any) -> int:
        filters = dict(filters)
        filters["limit"] = len(self._events)
        return len(self.search(**filters))

    def set_policy(self, policy: RetentionPolicy, *, actor: str) -> RetentionPolicy:
        self._policy = policy
        self._journal("retention.policy.changed", actor, {"enabled": policy.enabled, "max_entries": policy.max_entries})
        return policy

    def get_policy(self) -> RetentionPolicy:
        return self._policy

    def create_preview(self, *, actor: str, now: datetime | None = None, ttl_seconds: int = 300) -> DeletionPreview:
        now = _utc(now or datetime.now(UTC))
        policy = self._policy
        if not policy.enabled:
            ids: list[UUID] = []
        else:
            cutoff = now - timedelta(days=policy.older_than_days)
            candidates = [row for row in self._events if _parse_time(row["created_at"]) < cutoff]
            if policy.max_entries and len(self._events) > policy.max_entries:
                candidates += self._events[:len(self._events) - policy.max_entries]
            held = {hold.event_id for hold in self.list_holds(active_only=True) if hold.event_id}
            ids = list(dict.fromkeys(UUID(row["event_id"]) for row in candidates if UUID(row["event_id"]) not in held))
        selection = canonical([str(item) for item in ids])
        import hashlib
        preview = DeletionPreview(uuid4(), self.object_uid, tuple(ids), hashlib.sha256(selection.encode()).hexdigest(), actor, now, now + timedelta(seconds=ttl_seconds))
        self._previews[preview.preview_id] = preview
        return preview

    def delete_preview(self, preview_id: UUID | str, *, actor: str, reason: str) -> dict[str, Any]:
        preview = self._previews.get(UUID(str(preview_id)))
        if not preview or preview.is_expired() or preview.actor != actor or not reason.strip():
            raise PreviewExpired("deletion preview is missing, expired, or not owned by the actor")
        held = {hold.event_id for hold in self.list_holds(active_only=True) if hold.event_id}
        if any(event_id in held for event_id in preview.event_ids):
            raise HoldConflict("a legal hold blocks deletion")
        self._previews.pop(preview.preview_id, None)
        selected = set(preview.event_ids)
        before = len(self._events)
        self._events = [row for row in self._events if UUID(row["event_id"]) not in selected]
        # Deletion is a governed mutation of the local chain.  Re-seal the
        # surviving sequence explicitly so verification remains meaningful;
        # the deletion journal records why the chain changed.
        previous = ""
        for number, row in enumerate(self._events, 1):
            row["sequence"] = number
            row["previous_digest"] = previous
            row["integrity_digest"] = digest(row)
            previous = row["integrity_digest"]
        result = {"deleted": before - len(self._events), "selection_digest": preview.selection_digest, "reason": reason}
        self._journal("retention.events.deleted", actor, {"count": result["deleted"], "selection_digest": preview.selection_digest, "reason": reason})
        return result

    def create_hold(self, hold: LegalHold) -> LegalHold:
        if hold.object_uid != self.object_uid:
            raise ValueError("hold object_uid does not match repository scope")
        self._holds[hold.hold_id] = hold
        self._journal("governance.hold.created", hold.actor, {"hold_id": str(hold.hold_id), "event_id": str(hold.event_id) if hold.event_id else None})
        return hold

    def list_holds(self, *, active_only: bool = False) -> list[LegalHold]:
        values = list(self._holds.values())
        return [hold for hold in values if not active_only or hold.released_at is None]

    def release_hold(self, hold_id: UUID | str, *, actor: str) -> LegalHold:
        old = self._holds.get(UUID(str(hold_id)))
        if old is None or old.released_at is not None:
            raise KeyError(str(hold_id))
        released = LegalHold(old.hold_id, old.object_uid, old.event_id, old.reason, old.actor, old.created_at, datetime.now(UTC), actor)
        self._holds[released.hold_id] = released
        self._journal("governance.hold.released", actor, {"hold_id": str(released.hold_id)})
        return released

    def verify(self) -> dict[str, Any]:
        previous = ""
        for row in self._events:
            expected = digest(row)
            if row.get("previous_digest") != previous or row.get("integrity_digest") != expected:
                return {"ok": False, "count": len(self._events), "broken_event_id": row.get("event_id"), "reason": "chain mismatch"}
            previous = expected
        return {"ok": True, "count": len(self._events), "head": previous, "last_verified_at": datetime.now(UTC).isoformat()}

    def health(self) -> dict[str, Any]:
        return {"backend": self.backend, "schema_version": 1, "configuration": "valid", "connectivity": "ok", "transaction_mode": self.transaction_mode, "event_count": len(self._events), "integrity": self.verify()["ok"]}

    def governance(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._governance]

    def _journal(self, event_type: str, actor: str, details: dict[str, Any]) -> None:
        self._governance.append({"event_type": event_type, "actor": actor, "created_at": datetime.now(UTC).isoformat(), "details": details})


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _parse_time(value: str | datetime) -> datetime:
    return _utc(value if isinstance(value, datetime) else datetime.fromisoformat(value))


class ZODBRepository(MemoryRepository):
    """Persist the reference state in one lazily-created annotation mapping."""

    backend = "zodb"
    transaction_mode = "joined"

    def __init__(self, context: Any):
        self.context = context
        super().__init__(object_uid(context))
        self._load()

    def _annotations(self, create: bool = False):
        from zope.annotation.interfaces import IAnnotations
        from persistent.mapping import PersistentMapping
        annotations = IAnnotations(self.context)
        store = annotations.get(ANNOTATION_KEY)
        if store is None and create:
            store = PersistentMapping()
            annotations[ANNOTATION_KEY] = store
        return store

    def _load(self) -> None:
        store = self._annotations(False)
        if not store:
            return
        self._events = list(store.get("events", []))
        self._governance = list(store.get("governance", []))
        self._holds = {UUID(str(key)): LegalHold(**value) for key, value in store.get("holds", {}).items()}
        self._policy = RetentionPolicy(**store.get("policy", {}))
        self._previews = {
            UUID(str(key)): DeletionPreview(
                UUID(str(value["preview_id"])), value["object_uid"],
                tuple(UUID(str(item)) for item in value["event_ids"]),
                value["selection_digest"], value["actor"],
                value["created_at"], value["expires_at"])
            for key, value in store.get("previews", {}).items()
        }

    def _save(self) -> None:
        store = self._annotations(True)
        store["events"] = self._events
        store["governance"] = self._governance
        store["policy"] = {"enabled": self._policy.enabled, "older_than_days": self._policy.older_than_days, "max_entries": self._policy.max_entries, "policy_id": self._policy.policy_id, "legal_basis": self._policy.legal_basis}
        store["holds"] = {str(key): {"hold_id": value.hold_id, "object_uid": value.object_uid, "event_id": value.event_id, "reason": value.reason, "actor": value.actor, "created_at": value.created_at, "released_at": value.released_at, "released_by": value.released_by} for key, value in self._holds.items()}
        store["previews"] = {str(key): {"preview_id": value.preview_id, "object_uid": value.object_uid, "event_ids": list(value.event_ids), "selection_digest": value.selection_digest, "actor": value.actor, "created_at": value.created_at, "expires_at": value.expires_at} for key, value in self._previews.items()}

    def append(self, event: LogEvent) -> dict[str, Any]:
        result = super().append(event)
        self._save()
        return result

    def set_policy(self, policy: RetentionPolicy, *, actor: str) -> RetentionPolicy:
        result = super().set_policy(policy, actor=actor); self._save(); return result

    def create_hold(self, hold: LegalHold) -> LegalHold:
        result = super().create_hold(hold); self._save(); return result

    def create_preview(self, *, actor: str, now: datetime | None = None, ttl_seconds: int = 300) -> DeletionPreview:
        result = super().create_preview(actor=actor, now=now, ttl_seconds=ttl_seconds)
        self._save()
        return result

    def release_hold(self, hold_id: UUID | str, *, actor: str) -> LegalHold:
        result = super().release_hold(hold_id, actor=actor); self._save(); return result

    def delete_preview(self, preview_id: UUID | str, *, actor: str, reason: str) -> dict[str, Any]:
        result = super().delete_preview(preview_id, actor=actor, reason=reason); self._save(); return result
