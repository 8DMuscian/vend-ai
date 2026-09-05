"""Deterministic Negotiation State Machine.

The state machine enforces the negotiation protocol:
- Maximum 5 rounds
- Maximum 20 turns
- Valid state transitions only
- Deterministic termination

State transitions:
    ACTIVE → QUOTE_PROPOSED (buyer proposes)
    QUOTE_PROPOSED → ACCEPTED (merchant accepts)
    QUOTE_PROPOSED → QUOTE_PROPOSED (merchant counter-proposes)
    QUOTE_PROPOSED → REJECTED (merchant rejects)
    QUOTE_PROPOSED → EXPIRED (timeout)
    ACCEPTED → (terminal)
    REJECTED → (terminal)
    EXPIRED → (terminal)
    CANCELLED → (terminal)

The LLM proposes actions, deterministic code validates and executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Optional

from app.schemas.negotiation import (
    NegotiationStatus,
    MAX_ROUNDS,
    MAX_TURNS,
    QUOTE_VALIDITY_MINUTES,
)


class StateMachineError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_state: NegotiationStatus, action: str, reason: str):
        self.current_state = current_state
        self.action = action
        self.reason = reason
        super().__init__(f"Invalid transition from {current_state.value} via {action}: {reason}")


class NegotiationTerminated(Exception):
    """Raised when negotiation has reached a terminal state."""

    def __init__(self, status: NegotiationStatus, reason: str):
        self.status = status
        self.reason = reason
        super().__init__(f"Negotiation {status.value}: {reason}")


@dataclass(frozen=True)
class StateTransition:
    """Represents a valid state transition."""

    from_state: NegotiationStatus
    action: str
    to_state: NegotiationStatus
    actor: str  # 'buyer', 'merchant', 'system'


# Valid transitions
VALID_TRANSITIONS: list[StateTransition] = [
    # Buyer starts negotiation
    StateTransition(NegotiationStatus.ACTIVE, "start", NegotiationStatus.ACTIVE, "buyer"),
    # Buyer proposes quote
    StateTransition(NegotiationStatus.ACTIVE, "propose_quote", NegotiationStatus.QUOTE_PROPOSED, "buyer"),
    # Merchant counter-proposes (same state, increments round)
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "counter_propose", NegotiationStatus.QUOTE_PROPOSED, "merchant"),
    # Merchant accepts quote
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "accept_quote", NegotiationStatus.ACCEPTED, "merchant"),
    # Merchant rejects quote
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "reject_quote", NegotiationStatus.REJECTED, "merchant"),
    # Buyer accepts merchant's counter
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "accept_quote", NegotiationStatus.ACCEPTED, "buyer"),
    # Buyer rejects merchant's counter
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "reject_quote", NegotiationStatus.REJECTED, "buyer"),
    # System expires negotiation
    StateTransition(NegotiationStatus.ACTIVE, "expire", NegotiationStatus.EXPIRED, "system"),
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "expire", NegotiationStatus.EXPIRED, "system"),
    # Cancel
    StateTransition(NegotiationStatus.ACTIVE, "cancel", NegotiationStatus.CANCELLED, "buyer"),
    StateTransition(NegotiationStatus.ACTIVE, "cancel", NegotiationStatus.CANCELLED, "merchant"),
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "cancel", NegotiationStatus.CANCELLED, "buyer"),
    StateTransition(NegotiationStatus.QUOTE_PROPOSED, "cancel", NegotiationStatus.CANCELLED, "merchant"),
]


TERMINAL_STATES = {
    NegotiationStatus.ACCEPTED,
    NegotiationStatus.REJECTED,
    NegotiationStatus.EXPIRED,
    NegotiationStatus.CANCELLED,
}


def is_terminal(state: NegotiationStatus) -> bool:
    """Check if a state is terminal."""
    return state in TERMINAL_STATES


def get_valid_transitions(state: NegotiationStatus, actor: str) -> list[StateTransition]:
    """Get valid transitions from a state for a given actor."""
    return [t for t in VALID_TRANSITIONS if t.from_state == state and t.actor == actor]


def transition(
    current_state: NegotiationStatus,
    action: str,
    actor: str,
    round_number: int,
    turn_count: int,
) -> NegotiationStatus:
    """
    Execute a state transition with validation.

    Args:
        current_state: Current negotiation state
        action: Action being attempted
        actor: Actor performing the action ('buyer', 'merchant', 'system')
        round_number: Current round (0-5)
        turn_count: Total turns so far (0-20)

    Returns:
        New state after transition

    Raises:
        StateMachineError: If transition is invalid
        NegotiationTerminated: If negotiation has terminated
    """
    # Check if already terminated
    if is_terminal(current_state):
        raise NegotiationTerminated(current_state, "Negotiation already terminated")

    # Check turn limit
    if turn_count >= MAX_TURNS:
        raise NegotiationTerminated(
            NegotiationStatus.EXPIRED, f"Maximum turns ({MAX_TURNS}) exceeded"
        )

    # Check round limit for quote proposals
    if action in ("propose_quote", "counter_propose") and round_number >= MAX_ROUNDS:
        raise NegotiationTerminated(
            NegotiationStatus.EXPIRED, f"Maximum rounds ({MAX_ROUNDS}) exceeded"
        )

    # Find valid transition
    for t in VALID_TRANSITIONS:
        if t.from_state == current_state and t.action == action and t.actor == actor:
            # Additional validation for counter-propose
            if action == "counter_propose" and round_number >= MAX_ROUNDS:
                raise StateMachineError(
                    current_state, action, f"Round {round_number} exceeds maximum {MAX_ROUNDS}"
                )
            return t.to_state

    raise StateMachineError(
        current_state,
        action,
        f"No valid transition for {actor} action '{action}' in state {current_state.value}",
    )


def calculate_valid_until() -> datetime:
    """Calculate quote expiry timestamp."""
    return datetime.now(timezone.utc) + timedelta(minutes=QUOTE_VALIDITY_MINUTES)


def is_quote_expired(valid_until: datetime) -> bool:
    """Check if a quote has expired.

    Handles both naive and aware datetimes by normalizing to UTC-aware.
    """
    now = datetime.now(timezone.utc)
    if valid_until.tzinfo is None:
        valid_until = valid_until.replace(tzinfo=timezone.utc)
    return now > valid_until


def next_round(round_number: int) -> int:
    """Increment round number."""
    return min(round_number + 1, MAX_ROUNDS)


def next_turn(turn_count: int) -> int:
    """Increment turn count."""
    return min(turn_count + 1, MAX_TURNS)