# Security Model

> This document defines the threat model, security boundaries, and enforcement mechanisms for Vend.ai.

---

## Security Principle

**The LLM is an untrusted reasoning engine. All financial execution is deterministic and audited.**

---

## Trust Boundaries

```
┌─────────────────────────────────────────────────────────────┐
│                    TRUST BOUNDARY: External                 │
│                                                             │
│  Buyer Agent ◄──── untrusted input ──── Buyer (human)       │
│  Merchant Agent ◄── untrusted input ── Merchant (human)     │
│                                                             │
│  Risks: prompt injection, malformed input, replay attacks   │
├─────────────────────────────────────────────────────────────┤
│                    TRUST BOUNDARY: Platform                 │
│                                                             │
│  API Gateway ──▶ Auth ──▶ Rate Limit ──▶ Validation        │
│                                                             │
│  Policy Engine (deterministic, audited)                     │
│  Transaction State Machine (guarded transitions)            │
│  Razorpay Client (test-mode, signature verified)            │
│                                                             │
│  Risks: policy bypass, state machine manipulation           │
├─────────────────────────────────────────────────────────────┤
│                    TRUST BOUNDARY: Payment                  │
│                                                             │
│  Razorpay Test Mode                                         │
│  - Webhook signature verified                               │
│  - Payment amounts server-validated                         │
│  - No real money movement                                   │
│                                                             │
│  Risks: webhook spoofing, amount tampering                  │
└─────────────────────────────────────────────────────────────┘
```

---

## Threat Model (STRIDE)

### Spoofing

| Threat | Mitigation |
|--------|------------|
| Buyer impersonation | Session-based buyer_id, rate limiting per session |
| Merchant impersonation | API key authentication (hashed, stored in merchants table) |
| Webhook spoofing | HMAC-SHA256 signature verification on Razorpay webhooks |
| Agent identity confusion | Agent roles are system-assigned, not user-supplied |

### Tampering

| Threat | Mitigation |
|--------|------------|
| LLM hallucinates prices | Prices always fetched from DB. Quote engine validates against `products.price_cents`. Client-supplied prices rejected. |
| LLM modifies transaction amounts | Transaction amounts computed server-side from quote totals. LLM-proposed amounts are compared but not used directly. |
| Prompt injection in buyer message | Agent system prompt includes hard instructions. Tool parameters are typed/validated. LLM output never directly executes SQL or payments. |
| Price manipulation via API | Server-side price calculation. Client sends product_id + quantity, server looks up price. |
| Audit trail tampering | Append-only table. No UPDATE/DELETE permissions on audit_events. |

### Elevation of Privilege

| Threat | Mitigation |
|--------|------------|
| LLM approves payment directly | No tool exists for "execute payment". Only `accept_quote` exists, which goes through policy engine and requires human confirmation. |
| LLM bypasses policy engine | Policy evaluation is deterministic, outside LLM context. Every financial action calls `PolicyEngine.evaluate()`. |
| Merchant agent exceeds discount limits | Policy engine caps discounts. Merchant agent cannot override policy. |
| Admin endpoint without auth | Admin endpoints require admin role (checked via middleware). |
| Agent escalation bypass | ESCALATE verdict blocks action until admin reviews. |

### Information Disclosure

| Threat | Mitigation |
|--------|------------|
| Agent accesses buyer PII | Agent tools return only public product data. No PII in tool responses. |
| Agent accesses merchant secrets | Agent tools don't return API keys, cost floors, or internal config. |
| Excessive discount via negotiation | Policy engine caps discounts. All discount attempts logged. |

### Denial of Service

| Threat | Mitigation |
|--------|------------|
| LLM cost exhaustion | Rate limiting per session. Max turns per negotiation (20). Max tool calls per message (5). |
| Race condition on stock | `SELECT ... FOR UPDATE` on stock decrement. Pessimistic locking during payment capture. |
| Duplicate transaction flood | Idempotency key enforced at DB level. Redis lock prevents concurrent processing. |

### Repudiation

| Threat | Mitigation |
|--------|------------|
| Buyer denies placing order | Audit trail logs all actions with actor_id, timestamp, and explanation. |
| Merchant denies approving quote | Quotes include policy_decision and are logged with proposed_by. |
| Admin denies seeing escalation | ESCALATE events are logged with admin notification. |

---

## LLM Sandbox Rules

### What the LLM Can Do

| Action | Tool | How Enforced |
|--------|------|--------------|
| Search products | `search_catalog` | Returns from DB, no user input in SQL |
| Get product details | `get_product_details` | Returns typed Pydantic model |
| Get recommendations | `get_recommendations` | Recommendation engine, not LLM pricing |
| Propose a quote | `propose_quote` | Quote engine validates prices from DB, applies policy |
| Send messages | `respond_to_merchant` / `respond_to_buyer` | Content sanitized, no tool execution |
| Accept a quote | `accept_quote` | Triggers order creation, policy re-evaluated |

### What the LLM Cannot Do

| Action | Why | Enforcement |
|--------|-----|-------------|
| Execute payment | Only humans confirm transactions | No tool exists |
| Set final prices | Prices come from DB only | Quote engine overrides LLM amounts |
| Apply unlimited discounts | Policy engine caps discounts | Discount checked before quote storage |
| Modify transaction limits | Limits are config | Config is read-only, loaded at startup |
| Access PII | Privacy | Agent tools don't return PII |
| Bypass policy | Deterministic enforcement | Policy evaluation is outside LLM context |
| Direct database access | SQL injection risk | All DB access through SQLAlchemy ORM |
| Call external APIs | Unbounded side effects | Only whitelisted tools available |

---

## Financial Security Rules

### Rule 1: Prices Are Database-Sourced

```python
# CORRECT
price_cents = product.price_cents  # From DB

# WRONG (never do this)
price_cents = llm_proposed_price  # NEVER trust LLM for prices
```

### Rule 2: Discounts Are Policy-Capped

```python
# Every discount goes through policy engine
discount = min(proposed_discount, merchant_max_discount)
# Policy engine also checks:
# - Is discount within merchant config?
# - Is total order value within bounds?
# - Is this a rapid-fire attempt?
```

### Rule 3: Payments Are Idempotent

```python
# Every payment initiation requires idempotency_key
# UNIQUE constraint in DB prevents duplicates
# Redis lock prevents concurrent processing
```

### Rule 4: Webhooks Are Verified

```python
# Razorpay webhook signature verification
signature = hmac_sha256(webhook_secret, request_body)
if signature != request.headers["X-Razorpay-Signature"]:
    reject(401)
```

### Rule 5: Amounts Are Server-Computed

```python
# Quote total is computed, not accepted from client
total = sum(item.quantity * item.unit_price_cents for item in items)
total -= discount_cents
total += tax_cents
# Client-proposed amounts are compared but overridden
```

---

## Audit Security

### Append-Only Events

- `audit_events` table has no UPDATE or DELETE triggers
- Application code never modifies existing audit events
- Only INSERT operations are allowed

### Event Integrity

- Each event includes: event_type, aggregate_type/id, actor, payload, timestamp
- Financial events include explanation and policy_decision
- Events are indexed for fast query but never modified

### Event Immutability

```python
# Audit events are write-once
def log_event(event: AuditEvent):
    db.add(event)
    db.commit()
    # No update or delete methods exist on AuditLogger
```

---

## Rate Limiting

| Action | Limit | Window | Enforcement |
|--------|-------|--------|-------------|
| Buyer messages | 10 | per minute | Redis counter per session |
| Quote proposals | 5 | per minute | Redis counter per negotiation |
| Payment attempts | 3 | per 5 minutes | Redis counter per buyer |
| API requests | 100 | per minute | Redis counter per IP |
| Negotiation turns | 20 | per session | Counter in negotiations table |

---

## Secrets Management

- No secrets in source code (`.env` in `.gitignore`)
- Razorpay test-mode keys only (never production)
- Database credentials via environment variables
- API keys hashed before storage (bcrypt)
- Webhook secrets loaded from environment

---

## Data Classification

| Data | Classification | Storage | Access |
|------|---------------|---------|--------|
| Product catalog | Public | PostgreSQL | Anyone |
| AI schemas | Public | PostgreSQL (JSONB) | Agents, API |
| Negotiation messages | Internal | PostgreSQL | Session participants only |
| Quotes | Internal | PostgreSQL | Session participants + admin |
| Orders | Confidential | PostgreSQL | Buyer, merchant, admin |
| Audit events | Confidential | PostgreSQL | Admin only |
| API keys | Secret | Environment vars | Server only |
| Razorpay credentials | Secret | Environment vars | Server only |
| PII (emails, phones) | Restricted | Not stored | N/A |

---

## Incident Response

If a security issue is discovered:

1. **Stop.** Do not attempt to fix it silently.
2. **Document.** Create an audit event describing the issue.
3. **Escalate.** Notify admin immediately.
4. **Patch.** Fix the issue with a test covering the vulnerability.
5. **Review.** Check audit trail for any exploitation.

---

## Security Checklist

Before any deployment:

- [ ] No secrets in source code
- [ ] `.env` in `.gitignore`
- [ ] Razorpay test-mode keys only
- [ ] Webhook signature verification enabled
- [ ] Rate limiting configured
- [ ] Input validation on all endpoints
- [ ] Policy engine rules reviewed
- [ ] Audit trail completeness verified
- [ ] Agent tool permissions reviewed
- [ ] No direct SQL queries (ORM only)
