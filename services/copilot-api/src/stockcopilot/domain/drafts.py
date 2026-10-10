"""Promotion drafts and their approval state machine.

    draft -> selected -> approved
    draft -> approved            (approving straight from the chat implies selecting it)
    draft | selected -> rejected

The agent may only create drafts. `approved` / `rejected` require an explicit user action
(REST endpoint or the approval card in the chat). Repeating the current status is a no-op,
so approve/reject calls are idempotent.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, date, datetime
from typing import Protocol

from .models import DraftEvent, DraftStatus, ProductSellStrategy, PromotionDraft

ALLOWED: dict[DraftStatus, set[DraftStatus]] = {
    "draft": {"selected", "approved", "rejected"},
    "selected": {"approved", "rejected"},
    "approved": set(),
    "rejected": set(),
}


class InvalidTransitionError(ValueError):
    """Requested status change is not allowed from the current status (HTTP 409)."""


class DraftNotFoundError(LookupError):
    """Unknown draft id (HTTP 404)."""


def _now() -> datetime:
    return datetime.now(UTC)


def new_draft(strategy: ProductSellStrategy, actor: str) -> PromotionDraft:
    now = _now()
    draft_id = f"DR-{uuid.uuid4().hex[:10]}"
    strategy = strategy.model_copy(update={"draft_id": draft_id, "draft_status": "draft"})
    return PromotionDraft(
        draft_id=draft_id,
        status="draft",
        store_id=strategy.store.store_id,
        product_id=strategy.product.product_id,
        title=strategy.recommended_action,
        valid_from=strategy.valid_from,
        valid_to=strategy.valid_to,
        expected_net_impact=strategy.expected_net_impact,
        created_at=now,
        updated_at=now,
        history=[DraftEvent(status="draft", at=now, actor=actor, note="Created by the strategy engine")],
        strategy=strategy,
    )


def apply_transition(draft: PromotionDraft, to: DraftStatus, actor: str, note: str | None) -> PromotionDraft:
    if draft.status == to:
        return draft
    if to not in ALLOWED[draft.status]:
        raise InvalidTransitionError(f"Draft {draft.draft_id} is '{draft.status}' and cannot become '{to}'.")
    now = _now()
    return draft.model_copy(
        update={
            "status": to,
            "updated_at": now,
            "history": [*draft.history, DraftEvent(status=to, at=now, actor=actor, note=note)],
            "strategy": draft.strategy.model_copy(update={"draft_status": to}),
        }
    )


class DraftStore(Protocol):
    def create(self, strategy: ProductSellStrategy, actor: str = "copilot") -> PromotionDraft: ...

    def get(self, draft_id: str) -> PromotionDraft: ...

    def transition(self, draft_id: str, to: DraftStatus, actor: str, note: str | None = None) -> PromotionDraft: ...

    def list(
        self,
        store_id: str | None = None,
        statuses: set[DraftStatus] | None = None,
        overlaps: tuple[date, date] | None = None,
    ) -> list[PromotionDraft]: ...


def _matches(
    d: PromotionDraft, store_id: str | None, statuses: set[DraftStatus] | None, overlaps: tuple[date, date] | None
) -> bool:
    if store_id and d.store_id != store_id:
        return False
    if statuses and d.status not in statuses:
        return False
    return not overlaps or (d.valid_from <= overlaps[1] and overlaps[0] <= d.valid_to)


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._items: dict[str, PromotionDraft] = {}
        self._lock = threading.Lock()

    def create(self, strategy: ProductSellStrategy, actor: str = "copilot") -> PromotionDraft:
        draft = new_draft(strategy, actor)
        with self._lock:
            self._items[draft.draft_id] = draft
        return draft

    def get(self, draft_id: str) -> PromotionDraft:
        try:
            return self._items[draft_id]
        except KeyError as exc:
            raise DraftNotFoundError(f"Draft {draft_id} was not found.") from exc

    def transition(self, draft_id: str, to: DraftStatus, actor: str, note: str | None = None) -> PromotionDraft:
        with self._lock:
            updated = apply_transition(self.get(draft_id), to, actor, note)
            self._items[draft_id] = updated
            return updated

    def list(
        self,
        store_id: str | None = None,
        statuses: set[DraftStatus] | None = None,
        overlaps: tuple[date, date] | None = None,
    ) -> list[PromotionDraft]:
        items = [d for d in self._items.values() if _matches(d, store_id, statuses, overlaps)]
        return sorted(items, key=lambda d: d.created_at, reverse=True)


class PostgresDraftStore:
    """Drafts persisted as JSONB in `promotion_drafts` (see `data/schema.sql`)."""

    def __init__(self, pool: object) -> None:
        from psycopg_pool import ConnectionPool

        assert isinstance(pool, ConnectionPool)
        self._pool = pool

    def _save(self, draft: PromotionDraft, insert: bool) -> None:
        payload = draft.model_dump_json()
        with self._pool.connection() as conn:
            if insert:
                conn.execute(
                    "INSERT INTO promotion_drafts (draft_id, store_id, product_id, status, valid_from, valid_to,"
                    " created_at, updated_at, payload) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        draft.draft_id,
                        draft.store_id,
                        draft.product_id,
                        draft.status,
                        draft.valid_from,
                        draft.valid_to,
                        draft.created_at,
                        draft.updated_at,
                        payload,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE promotion_drafts SET status = %s, updated_at = %s, payload = %s WHERE draft_id = %s",
                    (draft.status, draft.updated_at, payload, draft.draft_id),
                )

    def create(self, strategy: ProductSellStrategy, actor: str = "copilot") -> PromotionDraft:
        draft = new_draft(strategy, actor)
        self._save(draft, insert=True)
        return draft

    def get(self, draft_id: str) -> PromotionDraft:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT payload FROM promotion_drafts WHERE draft_id = %s", (draft_id,)).fetchone()
        if row is None:
            raise DraftNotFoundError(f"Draft {draft_id} was not found.")
        payload = row[0]
        return PromotionDraft.model_validate(payload if isinstance(payload, dict) else json.loads(payload))

    def transition(self, draft_id: str, to: DraftStatus, actor: str, note: str | None = None) -> PromotionDraft:
        with self._pool.connection() as conn, conn.transaction():
            row = conn.execute(
                "SELECT payload FROM promotion_drafts WHERE draft_id = %s FOR UPDATE", (draft_id,)
            ).fetchone()
            if row is None:
                raise DraftNotFoundError(f"Draft {draft_id} was not found.")
            payload = row[0]
            current = PromotionDraft.model_validate(payload if isinstance(payload, dict) else json.loads(payload))
            updated = apply_transition(current, to, actor, note)
            if updated is not current:
                conn.execute(
                    "UPDATE promotion_drafts SET status = %s, updated_at = %s, payload = %s WHERE draft_id = %s",
                    (updated.status, updated.updated_at, updated.model_dump_json(), draft_id),
                )
            return updated

    def list(
        self,
        store_id: str | None = None,
        statuses: set[DraftStatus] | None = None,
        overlaps: tuple[date, date] | None = None,
    ) -> list[PromotionDraft]:
        clauses: list[str] = []
        params: list[object] = []
        if store_id:
            clauses.append("store_id = %s")
            params.append(store_id)
        if statuses:
            clauses.append("status = ANY(%s)")
            params.append(list(statuses))
        if overlaps:
            clauses.append("valid_from <= %s AND %s <= valid_to")
            params.extend([overlaps[1], overlaps[0]])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT payload FROM promotion_drafts {where} ORDER BY created_at DESC", params
            ).fetchall()
        return [PromotionDraft.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0])) for r in rows]
