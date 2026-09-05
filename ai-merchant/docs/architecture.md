# System Architecture

> See also: `AGENTS.md` for engineering rules that this architecture enforces.

---

## Design Principles

1. **LLM generates intent, never executes money.** Every financial action passes through a deterministic policy gate.
2. **Monolith first.** One deployable unit. Modules separated by Python package boundaries, not network calls.
3. **Event sourcing for audit.** Every state mutation is an immutable audit event.
4. **Bounded autonomy.** Agents get narrow tool permissions. Escalation on ambiguity.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Buyer Agent  │  │ Merchant Agent│  │  Admin Dashboard (React) │  │
│  │  (LLM + Tool) │  │ (LLM + Tool) │  │                          │  │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘  │
└─────────┼─────────────────┼───────────────────────┼─────────────────┘
          │                 │                       │
          ▼                 ▼                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        API GATEWAY                                  │
│            (FastAPI / rate limiting / auth / request ID)            │
└────────────────────────────┬────────────────────────────────────────┘
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
┌─────────────────┐ ┌───────────────┐ ┌─────────────────┐
│  CATALOG MODULE │ │ NEGOTIATION   │ │ TRANSACTION     │
│                 │ │ MODULE        │ │ MODULE          │
│ - Product CRUD  │ │ - Sessions    │ │ - Quote engine  │
│ - AI-readable   │ │ - Agent conv  │ │ - State machine │
│   schema        │ │ - Proposals   │ │ - Razorpay ops  │
│ - Upsell graph  │ │ - Policy gate │ │ - Idempotency   │
└────────┬────────┘ └───────┬───────┘ └────────┬────────┘
         │                  │                   │
         ▼                  ▼                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     SHARED SERVICES                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │ Policy Engine │  │ Audit Logger │  │ Razorpay Client          │  │
│  │ (deterministic│  │ (event store)│  │ (test-mode SDK)          │  │
│  │  rule engine) │  │              │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      DATA LAYER                                    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  PostgreSQL   │  │  Redis       │  │  File Storage (S3/local) │  │
│  │  (primary DB) │  │  (cache +    │  │  (product images, docs)  │  │
│  │               │  │   locks)     │  │                          │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Module Map

```
backend/app/
├── __init__.py
├── main.py                  # FastAPI app, lifespan, middleware
├── config.py                # Settings, env vars (Pydantic BaseSettings)
├── models/                  # SQLAlchemy ORM models
│   ├── __init__.py
│   ├── product.py
│   ├── quote.py
│   ├── order.py
│   ├── negotiation.py
│   └── audit.py
├── schemas/                 # Pydantic request/response schemas
│   ├── __init__.py
│   ├── catalog.py
│   ├── negotiation.py
│   ├── quote.py
│   └── transaction.py
├── api/                     # Route handlers
│   ├── __init__.py
│   ├── catalog.py
│   ├── negotiation.py
│   ├── quote.py
│   ├── transaction.py
│   └── admin.py
├── services/                # Business logic
│   ├── __init__.py
│   ├── catalog_service.py
│   ├── recommendation.py
│   ├── quote_engine.py
│   ├── negotiation_service.py
│   └── upsell.py
├── agents/                  # LLM agent orchestration
│   ├── __init__.py
│   ├── buyer_agent.py
│   ├── merchant_agent.py
│   └── session.py
├── tools/                   # Agent tool implementations
│   ├── __init__.py
│   ├── catalog_tools.py
│   ├── negotiation_tools.py
│   └── definitions.py       # JSON Schema tool definitions
├── policies/                # Deterministic policy engine
│   ├── __init__.py
│   ├── engine.py
│   ├── approvals.py
│   └── rules/
│       ├── __init__.py
│       ├── pricing.py
│       ├── discount.py
│       ├── fraud.py
│       └── compliance.py
├── audit/                   # Event sourcing
│   ├── __init__.py
│   ├── logger.py
│   ├── events.py
│   └── explanations.py
└── transaction/             # State machine + Razorpay
    ├── __init__.py
    ├── state_machine.py
    ├── razorpay_client.py
    └── idempotency.py
```

---

## Dependency Direction

```
api/ → services/ → policies/ + audit/
services/ → models/ (via repository pattern)
agents/ → services/ + tools/ (never directly to models)
tools/ → services/ (thin wrappers)
policies/ → models/ (read-only)
audit/ → models/ (write-only append)
```

**Rule:** No circular dependencies. `policies/` and `audit/` are leaf nodes.

---

## Database Schema

### Products

```sql
CREATE TABLE products (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    name            VARCHAR(255) NOT NULL,
    description     TEXT,
    ai_schema       JSONB NOT NULL,
    price_cents     INTEGER NOT NULL CHECK (price_cents > 0),
    currency        VARCHAR(3) DEFAULT 'INR',
    stock_qty       INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    metadata        JSONB DEFAULT '{}',
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

**AI Schema Example:**
```json
{
  "category": "electronics",
  "tags": ["wireless", "bluetooth", "headphones"],
  "specs": { "battery_life": "40h", "driver": "40mm" },
  "target_audience": "music_lovers",
  "use_cases": ["commute", "gym", "work"],
  "comparison_notes": "Mid-range, good bass response"
}
```

### Product Relationships

```sql
CREATE TABLE product_upsell_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id       UUID NOT NULL REFERENCES products(id),
    target_id       UUID NOT NULL REFERENCES products(id),
    relationship    VARCHAR(20) NOT NULL CHECK (relationship IN ('upsell', 'cross_sell', 'accessory')),
    priority        INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, target_id, relationship)
);
```

### Bundles

```sql
CREATE TABLE bundles (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id         UUID NOT NULL REFERENCES merchants(id),
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    bundle_price_cents  INTEGER NOT NULL,
    savings_pct         DECIMAL(5,2),
    is_active           BOOLEAN DEFAULT TRUE,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE bundle_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bundle_id       UUID NOT NULL REFERENCES bundles(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    discount_pct    DECIMAL(5,2) DEFAULT 0
);
```

### Negotiations

```sql
CREATE TABLE negotiations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    buyer_id        VARCHAR(255) NOT NULL,
    merchant_id     UUID NOT NULL REFERENCES merchants(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'quote_proposed', 'accepted', 'rejected', 'expired', 'cancelled')),
    started_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id  UUID NOT NULL REFERENCES negotiations(id),
    role            VARCHAR(20) NOT NULL CHECK (role IN ('buyer', 'merchant', 'system')),
    content         TEXT NOT NULL,
    tool_calls      JSONB DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Quotes

```sql
CREATE TABLE quotes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    negotiation_id  UUID REFERENCES negotiations(id),
    proposed_by     VARCHAR(20) NOT NULL CHECK (proposed_by IN ('buyer', 'merchant', 'system')),
    status          VARCHAR(20) NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft', 'pending_review', 'approved', 'rejected', 'expired', 'accepted')),
    subtotal_cents  INTEGER NOT NULL,
    discount_cents  INTEGER DEFAULT 0,
    tax_cents       INTEGER NOT NULL DEFAULT 0,
    total_cents     INTEGER NOT NULL,
    currency        VARCHAR(3) DEFAULT 'INR',
    valid_until     TIMESTAMPTZ NOT NULL,
    policy_decision VARCHAR(20),
    policy_reason   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE quote_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id        UUID NOT NULL REFERENCES quotes(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL
);
```

### Orders

```sql
CREATE TABLE orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quote_id            UUID REFERENCES quotes(id),
    buyer_id            VARCHAR(255) NOT NULL,
    merchant_id         UUID NOT NULL REFERENCES merchants(id),
    status              VARCHAR(20) NOT NULL DEFAULT 'created'
                        CHECK (status IN (
                            'created', 'pending_payment', 'payment_initiated',
                            'payment_captured', 'payment_failed', 'refunded',
                            'cancelled', 'completed'
                        )),
    total_cents         INTEGER NOT NULL,
    currency            VARCHAR(3) DEFAULT 'INR',
    idempotency_key     VARCHAR(64) UNIQUE NOT NULL,
    razorpay_order_id   VARCHAR(64),
    razorpay_payment_id VARCHAR(64),
    payment_method      VARCHAR(32),
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    paid_at             TIMESTAMPTZ,
    failed_at           TIMESTAMPTZ,
    failure_reason      TEXT
);

CREATE TABLE order_items (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id        UUID NOT NULL REFERENCES orders(id),
    product_id      UUID NOT NULL REFERENCES products(id),
    quantity        INTEGER NOT NULL CHECK (quantity > 0),
    unit_price_cents INTEGER NOT NULL
);
```

### Merchants

```sql
CREATE TABLE merchants (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    api_key_hash    VARCHAR(128),
    config          JSONB DEFAULT '{}',
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### Audit Events

```sql
CREATE TABLE audit_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(100) NOT NULL,
    aggregate_type  VARCHAR(50) NOT NULL,
    aggregate_id    UUID NOT NULL,
    actor_id        VARCHAR(255),
    actor_type      VARCHAR(20) CHECK (actor_type IN ('buyer', 'merchant', 'system', 'policy', 'agent')),
    payload         JSONB NOT NULL,
    explanation     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_audit_aggregate ON audit_events(aggregate_type, aggregate_id);
CREATE INDEX idx_audit_created ON audit_events(created_at);
```

---

## API Contracts

### Catalog

```
GET  /api/v1/products?q=&category=&min_price=&max_price=&limit=&offset=
GET  /api/v1/products/{id}
GET  /api/v1/products/{id}/ai-schema
GET  /api/v1/products/{id}/recommendations?limit=
GET  /api/v1/products/{id}/upsell?type=
GET  /api/v1/bundles?merchant_id=
```

### Negotiation

```
POST /api/v1/negotiations              { buyer_id, merchant_id }
GET  /api/v1/negotiations/{id}
POST /api/v1/negotiations/{id}/messages   { role, content }
POST /api/v1/negotiations/{id}/propose    { proposed_by, items, discount_pct? }
POST /api/v1/negotiations/{id}/accept-quote  { quote_id }
POST /api/v1/negotiations/{id}/reject-quote  { quote_id, reason? }
POST /api/v1/negotiations/{id}/cancel
```

### Transaction

```
POST /api/v1/orders                    { quote_id, idempotency_key }
POST /api/v1/orders/{id}/pay           { payment_method }
POST /api/v1/orders/{id}/verify        { razorpay_payment_id, razorpay_order_id, razorpay_signature }
GET  /api/v1/orders/{id}
GET  /api/v1/orders/{id}/explanation
```

### Webhook

```
POST /api/v1/webhooks/razorpay
Headers: X-Razorpay-Signature: {hmac}
Body: RazorpayEvent
```

### Admin

```
GET  /api/v1/admin/audit?aggregate_type=&aggregate_id=&event_type=&from=&to=&limit=
GET  /api/v1/admin/orders?status=&merchant_id=&from=&to=
GET  /api/v1/admin/policies
POST /api/v1/admin/policies/{id}/toggle
```

### Response Envelope

```json
{
  "success": true,
  "data": {},
  "meta": { "request_id": "req_abc", "timestamp": "2026-09-05T10:30:00Z" }
}
```

### Error Response

```json
{
  "success": false,
  "error": {
    "code": "POLICY_REJECTED",
    "message": "Discount exceeds maximum allowed (15%)",
    "details": { "requested_discount": 25, "max_allowed": 15, "policy_id": "discount_cap" }
  }
}
```

---

## Agent/Tool Contracts

### Buyer Agent Tools

```yaml
tools:
  - search_catalog:
      params: { query: str, max_price: int?, category: str?, limit: int }
      returns: list[ProductSummary]

  - get_product_details:
      params: { product_id: str }
      returns: Product

  - get_recommendations:
      params: { product_id: str, relationship: str, limit: int }
      returns: list[ProductSummary]

  - propose_quote:
      params: { negotiation_id: str, items: list[QuoteItem], discount_request_pct: float? }
      returns: Quote

  - respond_to_merchant:
      params: { negotiation_id: str, content: str }
      returns: Message

  - accept_quote:
      params: { negotiation_id: str, quote_id: str }
      returns: Order
```

### Merchant Agent Tools

```yaml
tools:
  - search_own_catalog:
      params: { query: str, in_stock_only: bool, limit: int }
      returns: list[ProductSummary]

  - get_inventory:
      params: { product_id: str }
      returns: InventoryStatus

  - get_upsell_opportunities:
      params: { product_ids: list[str], type: str }
      returns: list[UpsellOpportunity]

  - counter_propose:
      params: { negotiation_id: str, items: list[QuoteItem], discount_pct: float?, message: str? }
      returns: Quote

  - respond_to_buyer:
      params: { negotiation_id: str, content: str }
      returns: Message
```

---

## Policy Engine Design

### Architecture

```
Input: Action { type, actor, payload }
    │
    ▼
┌────────────────────────────────────────────┐
│  Rule Pipeline (ordered)                   │
│  1. Fraud Detection Rules                  │
│  2. Discount Bounds Rules                  │
│  3. Inventory Rules                        │
│  4. Pricing Rules                          │
│  5. Compliance Rules                       │
│  6. Rate Limiting Rules                    │
└────────────────────────────────────────────┘
    │
    ▼
Output: Decision {
    verdict: APPROVE | REJECT | ESCALATE,
    reason: string,
    applied_policies: list[PolicyResult],
    explanation: string
}
```

### Rules

```yaml
policies:
  - id: discount_cap
    type: quote_proposal
    severity: hard
    rule: { field: "items[*].discount_pct", max: 20.0 }

  - id: bundle_discount_cap
    type: quote_proposal
    severity: hard
    rule: { field: total_discount_pct, max: 30.0 }

  - id: min_order_value
    type: quote_proposal
    severity: hard
    rule: { field: total_cents, min: 10000 }

  - id: max_order_value
    type: quote_proposal
    severity: soft
    rule: { field: total_cents, max: 5000000 }

  - id: inventory_check
    type: quote_proposal
    severity: hard
    rule: "for_each(items, product.stock_qty >= item.quantity)"

  - id: price_floor
    type: quote_proposal
    severity: hard
    rule: "for_each(items, item.unit_price >= product.cost_floor_cents)"

  - id: rapid_fire
    type: transaction
    severity: hard
    rule: "count(orders WHERE buyer_id = actor.id AND created_at > now - 5min) < 3"

  - id: quote_expiry
    type: quote_proposal
    severity: hard
    rule: { field: valid_until, max_duration_from_now: "30m" }
```

---

## Transaction State Machine

```
CREATED → PENDING_PAYMENT → PAYMENT_INITIATED → PAYMENT_CAPTURED → COMPLETED
                                  │                    │
                                  │                    └──→ REFUNDED
                                  │
                                  └──→ PAYMENT_FAILED → CANCELLED
                                                    └──→ PENDING_PAYMENT (retry)

PENDING_PAYMENT → CANCELLED (timeout 15min)
PAYMENT_INITIATED → CANCELLED (timeout 5min)
```

### Transitions

| From | To | Trigger | Guard |
|------|----|---------|-------|
| CREATED | PENDING_PAYMENT | Auto | Quote approved |
| PENDING_PAYMENT | PAYMENT_INITIATED | POST /pay | Order not expired |
| PAYMENT_INITIATED | PAYMENT_CAPTURED | Webhook payment.captured | Signature valid |
| PAYMENT_INITIATED | PAYMENT_FAILED | Webhook payment.failed | Signature valid |
| PAYMENT_FAILED | PENDING_PAYMENT | Buyer retries | Retry < 3 |
| PAYMENT_FAILED | CANCELLED | Buyer cancels | — |
| PAYMENT_CAPTURED | COMPLETED | Auto | Stock confirmed |
| PENDING_PAYMENT | CANCELLED | Timeout/buyer | — |

---

## Audit Event Schema

```json
{
  "id": "evt_a1b2c3d4",
  "event_type": "quote.proposed",
  "aggregate_type": "quote",
  "aggregate_id": "quote_xyz",
  "actor_id": "buyer_session_abc",
  "actor_type": "buyer",
  "timestamp": "2026-09-05T10:30:00Z",
  "payload": { "items": [...], "total_cents": 2499 },
  "policy_decision": { "verdict": "APPROVE", "applied_policies": ["discount_cap:PASS"] },
  "explanation": "Buyer proposed ₹2499 for Wireless Headphones Pro. All policy checks passed."
}
```

### Event Types

```
catalog.product.created/updated/deactivated
negotiation.started/message_sent/quote_proposed/quote_accepted/quote_rejected/resolved/cancelled
quote.proposed/counter_proposed/policy_evaluated/approved/rejected/expired/accepted
order.created/payment_initiated/payment_captured/payment_failed/cancelled/completed/refunded
policy.violation_detected/escalation_required
agent.tool_called/tool_result/error
```

---

## Technology Choices

| Layer | Choice | Rationale |
|-------|--------|-----------|
| Language | Python 3.12+ | FastAPI ecosystem, LLM SDK support |
| Framework | FastAPI | Async, auto-docs, Pydantic validation |
| ORM | SQLAlchemy 2.0 | Mature, async, Alembic migrations |
| Database | PostgreSQL | JSONB, ACID, reliable |
| Cache/Lock | Redis | Distributed locks for idempotency |
| LLM | OpenAI GPT-4o | Tool calling, reasoning |
| Payment | Razorpay SDK | Hackathon requirement |
| Validation | Pydantic v2 | Request/response schemas |
| Testing | pytest + httpx | FastAPI test client |
| Migrations | Alembic | Database versioning |

---

## Environment Variables

```env
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/ai_merchant
REDIS_URL=redis://localhost:6379/0
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
APP_ENV=development
APP_PORT=8000
APP_SECRET_KEY=...
```
