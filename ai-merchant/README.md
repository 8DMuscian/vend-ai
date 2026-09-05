# Vend.ai

**The LLM proposes. Deterministic code disposes.**

An AI-native commerce platform where buyer and merchant agents negotiate in natural language, with a deterministic policy engine ensuring every financial action is safe, explainable, and auditable.

> Built for the [Razorpay AI Growth & Agentic Commerce Hackathon](https://razorpay.com)

---

## The Problem

AI agents are powerful enough to negotiate prices — but you can't trust an LLM with money. A hallucinated price, a prompt injection attack, or a manipulated agent could drain margins or create fraudulent orders. Current solutions either remove humans entirely (risky) or keep humans in the loop (defeats the purpose).

## Our Solution

Vend.ai splits the difference: **AI agents negotiate freely on both sides, but every financial decision passes through a deterministic policy engine the LLM cannot override.**

```
Buyer Agent ←→ Merchant Agent    (natural language negotiation)
       │              │
       ▼              ▼
┌─────────────────────────────┐
│   Deterministic Policy      │   ← LLM cannot modify these
│   Engine                    │
│                             │
│  ✓ Discount caps            │
│  ✓ Inventory checks         │
│  ✓ Price floor (margin)     │
│  ✓ Order value limits       │
│  ✓ Buyer budget validation  │
│  ✓ Duplicate quote blocking │
└─────────────┬───────────────┘
              │
              ▼
     Razorpay Payment (test mode)
              │
              ▼
     Audit Event (append-only)
```

## Key Features

### 1. AI-Readable Product Catalog
Products come with structured `ai_schema` metadata — categories, tags, specs, compatibility, use cases — so agents can search and reason about products without parsing free text.

### 2. Natural Language Negotiation
Buyers describe what they want in plain English. Merchant agents find products, suggest bundles, and negotiate prices. The system enforces a bounded state machine: max 5 rounds, max 20 turns, 30-minute quote validity.

### 3. Deterministic Policy Engine
Every quote proposal is validated against 8 independent policy checks before being stored. The LLM never sees the policy rules and cannot bypass them:

| Check | What It Does |
|-------|-------------|
| `discount_cap` | Rejects discounts exceeding merchant limit (default 20%) |
| `inventory_check` | Rejects if stock < requested quantity |
| `price_floor` | Rejects if effective price falls below cost margin |
| `min_order_value` | Rejects orders below ₹100 |
| `max_order_value` | Escalates orders above ₹50,000 for manual review |
| `buyer_budget` | Rejects if total exceeds buyer's stated budget |
| `quote_expiry` | Flags expired quotes for audit completeness |
| `duplicate_quote` | Rejects if same party has an active quote in this negotiation |

### 4. Atomic Stock Management
Stock is decremented atomically at quote acceptance time using `UPDATE products SET stock_qty = stock_qty - :qty WHERE stock_qty >= :qty`. The database itself rejects oversells — no application-level race condition possible.

### 5. Idempotent Payments
Each Razorpay order gets a unique UUID4 idempotency key passed as the `receipt` field. Concurrent requests for the same buyer cannot create duplicate charges.

### 6. Append-Only Audit Trail
Every financial mutation — quote proposed, quote accepted, quote rejected, negotiation cancelled — is logged to an append-only `audit_events` table with structured payload and human-readable explanation.

### 7. Payment State Machine
Payments follow a deterministic lifecycle: `CREATED → AUTHORIZED → CAPTURED`. Transient failures trigger automatic retries with policy re-verification. Failed payments are never silently retried without re-checking limits.

## Security Hardening (Applied)

| Issue | Fix |
|-------|-----|
| SQL injection via JSON category filter | Replaced f-string interpolation with Python-side filtering |
| Hardcoded `secret_key` default | Auto-generates `secrets.token_hex(32)` if not set |
| CORS wildcard with credentials | Restricted to configured origins via `settings.cors_origins` |
| Hardcoded request IDs | Dynamic UUID4 per request |
| Deprecated `datetime.utcnow()` | Migrated to `datetime.now(timezone.utc)` across all models |
| Deterministic idempotency keys | Switched from SHA-256 hash to UUID4 per order |
| Zero audit events written | Added `audit_logger.py` with calls at every financial mutation |
| No stock decrement on acceptance | Added atomic SQL decrement with row-count verification |

## Quick Start

```bash
# Clone
git clone https://github.com/8DMuscian/vend-ai.git
cd vend-ai/ai-merchant/backend

# Install dependencies
pip install fastapi uvicorn sqlalchemy pydantic pydantic-settings razorpay

# Run tests (99 passing)
python -m pytest tests/ -v

# Start server
python -m uvicorn main:app --reload --port 8000

# Open API docs
# http://localhost:8000/docs
```

## Working Endpoints

```bash
# Health check
curl http://localhost:8000/health

# List catalog
curl http://localhost:8000/api/v1/ai/catalog

# Get product by ID
curl http://localhost:8000/api/v1/ai/products/{id}

# Search by natural language
curl -X POST "http://localhost:8000/api/v1/ai/search?query=headphones&limit=5"
```

## Project Structure

```
vend-ai/
├── ARCHITECTURE.md
├── ai-merchant/
│   ├── AGENTS.md              # Engineering constitution
│   ├── docs/
│   │   ├── architecture.md    # System design + API contracts
│   │   ├── security.md        # STRIDE threat model
│   │   ├── product.md         # Requirements + user stories
│   │   ├── demo.md            # Demo walkthrough
│   │   └── audit_timeline.md  # Audit event flow
│   └── backend/
│       ├── main.py            # FastAPI entry point
│       ├── app/
│       │   ├── api/v1/        # Route handlers (catalog)
│       │   ├── models/        # SQLAlchemy ORM (9 tables)
│       │   ├── schemas/       # Pydantic v2 validation
│       │   └── services/      # Business logic
│       │       ├── negotiation_service.py
│       │       ├── negotiation_state.py    # State machine
│       │       ├── negotiation_validator.py # Policy engine
│       │       ├── transaction_guard.py    # Payment policy
│       │       ├── razorpay_integration.py # Razorpay SDK
│       │       ├── growth_brain.py         # Recommendations
│       │       └── audit_logger.py         # Append-only audit
│       ├── tests/             # 99 tests
│       └── migrations/        # Alembic
```

## Tech Stack

| Layer | Technology |
|-------|------------|
| Language | Python 3.12+ |
| Framework | FastAPI |
| ORM | SQLAlchemy 2.0 |
| Validation | Pydantic v2 |
| Payments | Razorpay SDK (test mode) |
| Database | PostgreSQL (prod) / SQLite (tests) |
| Testing | pytest |

## Architecture Principles

1. **LLM proposes, code disposes** — Agents negotiate freely, but every financial action passes through deterministic validation.
2. **Prices from database, never from LLM** — Unit prices are fetched server-side. LLM-proposed amounts are compared but never used directly.
3. **All amounts in paise (integers)** — No float arithmetic for money. Eliminates rounding errors.
4. **Every financial mutation has an audit event** — If it moved money (or could have), it's logged with explanation.
5. **State machine enforces protocol** — Negotiations follow a bounded state diagram. Invalid transitions are rejected at the code level.

## Documentation

- [Architecture](ai-merchant/docs/architecture.md) — Full system design, database schema, API contracts
- [Security](ai-merchant/docs/security.md) — STRIDE threat model, security boundaries
- [Product](ai-merchant/docs/product.md) — Requirements, user stories, acceptance criteria
- [Demo](ai-merchant/docs/demo.md) — Step-by-step walkthrough
- [Engineering Constitution](ai-merchant/AGENTS.md) — Rules every AI agent must follow

## License

Hackathon use only. Not for production deployment.
