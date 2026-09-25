"""Retry-safe transactional outbox primitives for RDBMS outbox mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import uuid4

from .serialization import bounded_details


@dataclass(slots=True)
class Envelope:
    object_uid: str
    payload: dict[str, Any]
    schema_version: int = 1
    idempotency_key: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempt_count: int = 0
    next_attempt_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    failure_class: str | None = None
    dead_letter: bool = False

    def __post_init__(self) -> None:
        self.payload = bounded_details(self.payload)


class Outbox:
    def __init__(self, max_attempts: int = 8):
        self.items: dict[str, Envelope] = {}
        self.max_attempts = max_attempts

    def enqueue(self, item: Envelope) -> Envelope:
        return self.items.setdefault(item.idempotency_key, item)

    def deliver(
        self, send: Callable[[Envelope], None], now: datetime | None = None
    ) -> dict[str, int]:
        now = now or datetime.now(UTC)
        result = {"delivered": 0, "failed": 0, "dead_letter": 0}
        for item in list(self.items.values()):
            if item.dead_letter or item.next_attempt_at > now:
                continue
            try:
                send(item)
            except Exception as exc:  # exception text must never enter the envelope
                item.attempt_count += 1
                item.failure_class = type(exc).__name__[:100]
                result["failed"] += 1
                if item.attempt_count >= self.max_attempts:
                    item.dead_letter = True
                    result["dead_letter"] += 1
                else:
                    item.next_attempt_at = now + timedelta(
                        seconds=min(3600, 2**item.attempt_count)
                    )
            else:
                result["delivered"] += 1
                del self.items[item.idempotency_key]
        return result
