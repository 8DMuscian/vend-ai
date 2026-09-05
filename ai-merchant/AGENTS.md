# AGENTS.md — AI Merchant Engineering Constitution

> This file is the single source of truth for all AI agents working on this codebase.
> Every agent MUST read this before writing any code.

---

## Core Principle

**The LLM proposes. Deterministic code disposes.**

The LLM is a reasoning engine. It generates intent, plans actions, and suggests outcomes.
The deterministic code is the execution engine. It validates, enforces policy, and touches money.

These two must NEVER be confused.

---

## Absolute Prohibitions

The LLM must NEVER directly:

1. **Set final transaction amounts** — Prices are looked up from the database. The LLM cannot override them.
2. **Bypass policy checks** — Every financial action goes through `PolicyEngine.evaluate()`. No exceptions.
3. **Execute payment APIs** — Only `TransactionService` calls Razorpay. The LLM has no tool for this.
4. **Modify transaction limits** — Discount caps, order maximums, and rate limits are config. The LLM cannot change them.
5. **Approve its own financial actions** — A quote proposed by the merchant agent is evaluated by the same policy engine as a buyer proposal.
6. **Store secrets** — No API keys, passwords, or tokens in source code. Environment variables only.
7. **Execute raw SQL** — All database access goes through SQLAlchemy ORM. No string interpolation in queries.

---

## Money Flow

```
Buyer/Agent intent
    │
    ▼
LLM generates proposal (quote, discount request, etc.)
    │
    ▼
Deterministic validation (price lookup, input sanitization)
    │
    ▼
Policy engine (discount cap, inventory, fraud, compliance)
    │
    ├── REJECT → Return reason to agent, log audit event
    ├── ESCALATE → Hold for admin review, log audit event
    │
    ▼ APPROVE
Quote stored with policy_decision
    │
    ▼
Buyer accepts → Order created
    │
    ▼
TransactionService.initiate() → Razorpay test-mode order
    │
    ▼
Razorpay webhook → payment.captured / payment.failed
    │
    ▼
Transaction state machine updates order status
    │
    ▼
Audit event logged with full explanation
```

---

## Financial Invariants

These are non-negotiable. Every code change must preserve them.

| # | Invariant | Enforcement |
|---|-----------|-------------|
| 1 | Transaction amount must be <= approved limit | `QuoteEngine` validates against `products.price_cents`. No client-supplied prices. |
| 2 | Discount must be <= merchant discount limit | `PolicyEngine` checks `discount_cap` rule on every quote proposal. |
| 3 | Every transaction must have a reason | `audit_events.explanation` is required for all financial events. |
| 4 | Every financial action must have an audit event | `AuditLogger.log()` called in every service method that mutates financial state. |
| 5 | Payment operations must be idempotent | `idempotency_key` UNIQUE constraint on `orders` table. Redis lock on concurrent access. |
| 6 | Failed payments must never create duplicate charges | State machine prevents re-initiation without retry. Idempotency key prevents duplicate Razorpay orders. |
| 7 | LLM output must never be trusted as financial state | Prices, totals, and discounts are recalculated server-side. LLM-proposed amounts are compared but not used directly. |

---

## Architecture Rules

### Monolith First

This is a hackathon project. One deployable unit. One database. No microservices.

Modules are separated by Python package boundaries, not by network calls.

```
backend/app/
├── agents/     # LLM orchestration + tool definitions
├── api/        # FastAPI route handlers
├── models/     # SQLAlchemy ORM models
├── services/   # Business logic (catalog, negotiation, quote, transaction)
├── policies/   # Deterministic policy engine
├── tools/      # Agent tool implementations
└── audit/      # Event logging + explanations
```

### Dependency Direction

```
api/ → services/ → policies/ + audit/
services/ → models/
agents/ → services/ + tools/ (never directly to models)
tools/ → services/ (thin wrappers)
policies/ → models/ (read-only)
audit/ → models/ (write-only append)
```

**No circular dependencies.** If you need to import across layers, you're doing it wrong.

### Module Boundaries

Each module exposes a typed service interface. Modules never import from each other's internals.

```python
# GOOD: api/negotiation.py imports NegotiationService
from app.services.negotiation_service import NegotiationService

# BAD: api/negotiation.py imports internal negotiation state machine
from app.services.negotiation_service._state import NegotiationState  # NO
```

---

## Coding Standards

### Type Safety

- All function signatures must have type hints.
- All Pydantic models must have explicit field types.
- All API request/response bodies must be Pydantic models.
- No `dict` as a catch-all. Use typed models.

```python
# GOOD
def create_quote(items: list[QuoteItem], discount_pct: float | None) -> Quote:
    ...

# BAD
def create_quote(items, discount_pct=None):  # NO
    ...
```

### Error Handling

- Every external call (Razorpay, LLM, database) must be wrapped in try/except.
- Errors must be logged as audit events.
- Never silently swallow exceptions.
- Use custom exception classes for business logic errors.

```python
# GOOD
class PolicyViolationError(Exception):
    def __init__(self, policy_id: str, reason: str):
        self.policy_id = policy_id
        self.reason = reason

# BAD
raise Exception("policy failed")  # NO
```

### Financial Logic

- All amounts stored as integers (paise/cents). Never floats for money.
- Price lookups always go to the database. Never from LLM output.
- Discount calculations are done server-side, validated by policy.
- Every financial mutation creates an audit event.

```python
# GOOD
price_cents = product.price_cents  # From DB
total_cents = sum(item.quantity * price_cents for item in items)

# BAD
price_cents = llm_proposed_price  # NEVER from LLM
```

### Tests

- All financial logic must have tests.
- Policy engine rules must have tests for APPROVE, REJECT, and ESCALATE cases.
- Transaction state machine must have tests for every valid transition.
- Idempotency must be tested with concurrent requests.
- Run `pytest` before every commit.

```bash
# Run all tests
pytest backend/tests/ -v

# Run with coverage
pytest backend/tests/ --cov=app --cov-report=term-missing
```

### Secrets

- No secrets in source code. Ever.
- Use environment variables for all credentials.
- `.env` files are in `.gitignore`.
- Razorpay test-mode keys only. Never production keys in this repo.

```python
# GOOD
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    razorpay_key_id: str
    razorpay_key_secret: str
    database_url: str

# BAD
RAZORPAY_KEY = "rzp_test_abc123"  # NEVER
```

### Documentation

- Every public function must have a docstring.
- API endpoints must have OpenAPI descriptions.
- Complex business logic must have inline comments explaining *why*, not *what*.
- Architecture decisions go in `docs/`.

---

## Agent Tool Rules

### Tool Permission Model

Each agent type has a fixed set of tools. Tools cannot be added at runtime.

| Agent | Allowed Tools | Cannot Do |
|-------|--------------|-----------|
| Buyer Agent | `search_catalog`, `get_product_details`, `get_recommendations`, `propose_quote`, `respond_to_merchant`, `accept_quote` | Execute payment, modify prices, access PII |
| Merchant Agent | `search_own_catalog`, `get_inventory`, `get_upsell_opportunities`, `counter_propose`, `respond_to_buyer` | Execute payment, exceed discount limits, access buyer PII |

### Tool Output Rules

- Tool outputs come from the database, not from the LLM.
- Tool outputs never include PII (emails, phone numbers, addresses).
- Tool outputs are typed Pydantic models, not raw dicts.
- Tool errors are caught and returned as structured error objects.

### Tool Call Limits

- Maximum 5 tool calls per agent message.
- Maximum 20 turns per negotiation session.
- Maximum 3 payment retry attempts per order.

---

## Policy Engine Rules

### Rule Structure

Every policy rule must have:
- `id` — unique identifier
- `type` — which action type it applies to
- `severity` — `hard` (blocks) or `soft` (escalates)
- `rule` — the actual check (field validation or custom function)
- `explanation` — template for human-readable explanation

### Rule Evaluation

- Rules are evaluated in order. First hard failure stops evaluation.
- All rules are evaluated even after a hard failure (for audit completeness).
- Results are stored with the action for audit trail.

### Adding New Rules

1. Add rule definition to `backend/app/policies/rules/`
2. Add test case in `backend/tests/test_policy.py`
3. Update this file if the rule affects financial invariants

---

## Git Rules

- Never commit `.env` files.
- Never commit Razorpay credentials.
- Commit messages: `type(scope): description` (e.g., `feat(quote): add policy validation`)
- One logical change per commit.
- Run tests before pushing.

---

## Quick Reference

| Concern | Where |
|---------|-------|
| Database models | `backend/app/models/` |
| API routes | `backend/app/api/` |
| Business logic | `backend/app/services/` |
| Agent orchestration | `backend/app/agents/` |
| Agent tool definitions | `backend/app/tools/` |
| Policy rules | `backend/app/policies/` |
| Audit logging | `backend/app/audit/` |
| Tests | `backend/tests/` |
| Architecture docs | `docs/architecture.md` |
| Security docs | `docs/security.md` |
| Demo flow | `docs/demo.md` |
| Seed data | `seed/` |
