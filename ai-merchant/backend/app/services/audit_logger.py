"""Minimal append-only audit logger for financial mutations.

Every financial action (quote proposed, accepted, payment created, etc.)
must be logged here for compliance and post-hoc investigation.

The LLM proposes. Deterministic code disposes. This logger records the disposal.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


def log_audit_event(
    session: Session,
    *,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    actor_id: Optional[str] = None,
    actor_type: Optional[str] = None,
    payload: dict[str, Any] | None = None,
    explanation: str | None = None,
) -> AuditEvent:
    """Append an audit event to the event store.

    This function is INSERT-only. It never modifies or deletes existing events.

    Args:
        session: Active SQLAlchemy session (caller is responsible for commit)
        event_type: Dot-separated event type (e.g. 'quote.proposed', 'order.created')
        aggregate_type: Type of aggregate (e.g. 'negotiation', 'order', 'payment')
        aggregate_id: UUID of the aggregate
        actor_id: ID of the actor (buyer_id, merchant_id, 'system', 'policy')
        actor_type: Role of actor ('buyer', 'merchant', 'system', 'policy')
        payload: Structured event-specific data
        explanation: Human-readable explanation of why this event occurred

    Returns:
        The created AuditEvent (not yet committed)
    """
    event = AuditEvent(
        event_type=event_type,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        actor_id=actor_id,
        actor_type=actor_type,
        payload=payload or {},
        explanation=explanation,
        created_at=datetime.now(timezone.utc),
    )
    session.add(event)
    return event
