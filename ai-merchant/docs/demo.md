# Demo Flow

> Step-by-step walkthrough for the Razorpay AI Growth & Agentic Commerce hackathon demo.

---

## Pre-Demo Setup

```bash
# 1. Start the server
uvicorn backend.app.main:app --reload --port 8000

# 2. Seed sample data
python -m seed.seed_products

# 3. Verify catalog
curl http://localhost:8000/api/v1/products | python -m json.tool

# 4. Open browser
# Admin dashboard: http://localhost:3000
# Demo UI: http://localhost:3000/demo
```

---

## Demo Scenario: Gym Audio Kit

**Narrative:** A buyer wants wireless headphones for the gym. Through agent negotiation, they discover a bundle that saves them money. The entire flow is policy-governed and audited.

---

### Step 1: Seed Catalog (Pre-loaded)

**Merchant "TechZone" has these products:**

| Product | Price | AI Schema Highlights |
|---------|-------|---------------------|
| Wireless Headphones Pro | ₹2,499 | wireless, gym, IPX7, 40h battery |
| Wireless Earbuds Lite | ₹1,499 | wireless, budget, gym |
| Headphone Carrying Case | ₹299 | accessory, fits headphones |
| Premium Replacement Tips | ₹149 | accessory, fits earbuds |
| Bluetooth Audio Cable | ₹499 | accessory, wired backup |

**Upsell links:**
- Headphones Pro → Carrying Case (accessory)
- Headphones Pro → Audio Cable (cross_sell)
- Headphones Pro → Earbuds Lite (upsell)

**Bundle:**
- "Gym Audio Kit": Headphones Pro + Carrying Case + Audio Cable = ₹2,799 (saves ₹498)

---

### Step 2: Buyer Initiates Search

**Buyer says:** "I need good wireless headphones for gym, budget around 3000"

**Buyer Agent executes:**

```
Tool: search_catalog
  query: "wireless headphones gym"
  max_price: 300000  (₹3,000 in paise)
  category: "headphones"
  limit: 5

Returns:
  1. Wireless Headphones Pro (₹2,499) — relevance: 0.95
  2. Wireless Earbuds Lite (₹1,499) — relevance: 0.78
```

**Buyer Agent responds:** "I found two great options for gym headphones:

1. **Wireless Headphones Pro** (₹2,499) — IPX7 waterproof, 40h battery, perfect for intense workouts
2. **Wireless Earbuds Lite** (₹1,499) — Lightweight, budget-friendly, also gym-rated"

---

### Step 3: Agent Discovers Recommendations

**Buyer Agent executes:**

```
Tool: get_recommendations
  product_id: "prod_headphones_pro"
  relationship: "accessory"
  limit: 3

Returns:
  1. Headphone Carrying Case (₹299) — protects during commute
  2. Bluetooth Audio Cable (₹499) — wired backup for gym
```

```
Tool: get_recommendations
  product_id: "prod_headphones_pro"
  relationship: "bundle"
  limit: 1

Returns:
  1. Gym Audio Kit Bundle (₹2,799) — saves ₹498 vs buying separately
```

**Buyer Agent responds:** "Great news! There's a **Gym Audio Kit bundle** at ₹2,799 that includes:
- Wireless Headphones Pro
- Carrying Case (₹299 value)
- Audio Cable (₹499 value)

That saves you ₹498 compared to buying them separately!"

---

### Step 4: Negotiation Session Created

```
POST /api/v1/negotiations
Body: { "buyer_id": "buyer_001", "merchant_id": "merchant_techzone" }

Response:
{
  "id": "neg_abc123",
  "status": "active",
  "started_at": "2026-09-05T10:30:00Z"
}
```

---

### Step 5: Buyer Negotiates

**Buyer says:** "The bundle looks good. Can you do 2700 for everything?"

**Buyer Agent sends message:**

```
POST /api/v1/negotiations/neg_abc123/messages
Body: {
  "role": "buyer",
  "content": "The Gym Audio Kit bundle looks good. Can you do ₹2,700 for everything?"
}
```

**Merchant Agent receives and responds:**

```
Merchant Agent calls: get_inventory(product_id="prod_headphones_pro")
Returns: { stock_qty: 15, is_available: true }

Merchant Agent calls: get_upsell_opportunities(
  product_ids: ["prod_headphones_pro"],
  type: "all"
)
Returns: [
  { product: "Premium Replacement Tips", type: "cross_sell", relevance: 0.6 }
]

Merchant Agent responds: "Great choice! The Pro has 40h battery and IPX7 — perfect for gym.
I can't do ₹2,700, but I can offer **₹2,750** for the bundle — that's my best price.
I'd also suggest adding Premium Replacement Tips (₹149) for when the originals wear out."
```

---

### Step 6: Buyer Accepts

**Buyer says:** "Deal! Let's go with the bundle at ₹2,750."

**Buyer Agent calls:**

```
Tool: propose_quote
  negotiation_id: "neg_abc123"
  items: [
    { product_id: "prod_headphones_pro", quantity: 1 },
    { product_id: "prod_case", quantity: 1 },
    { product_id: "prod_audio_cable", quantity: 1 }
  ]
  discount_request_pct: 0  (buyer accepted merchant's price)

Returns: Quote object
```

---

### Step 7: Policy Engine Evaluates

**Quote Engine generates quote:**

```
subtotal_cents: 3297  (2499 + 299 + 499)
discount_cents: 547   (bundle discount)
tax_cents: 0          (demo mode)
total_cents: 2750
valid_until: 2026-09-05T11:00:00Z  (30 min)
```

**Policy Engine evaluates:**

```
Action: QuoteProposalAction {
  type: "quote_proposal",
  actor: { id: "buyer_001", type: "buyer" },
  payload: { total_cents: 2750, discount_pct: 16.6, items: [...] }
}

Rule Pipeline:
  1. discount_cap: PASS (16.6% < 20% max)
  2. bundle_discount_cap: PASS (16.6% < 30% max)
  3. min_order_value: PASS (₹2,750 > ₹100)
  4. inventory_check: PASS (all items in stock)
  5. price_floor: PASS (above cost)
  6. quote_expiry: PASS (30min validity)

Verdict: APPROVE
```

**Quote stored with:**
```json
{
  "policy_decision": "APPROVE",
  "policy_reason": null
}
```

**Audit event logged:**
```json
{
  "event_type": "quote.proposed",
  "aggregate_type": "quote",
  "aggregate_id": "quote_xyz",
  "actor_id": "buyer_001",
  "actor_type": "buyer",
  "payload": { "total_cents": 2750, "discount_pct": 16.6 },
  "explanation": "Buyer proposed ₹2,750 for Gym Audio Kit bundle (Headphones Pro + Case + Audio Cable). Discount of 16.6% within bounds. All policy checks passed."
}
```

---

### Step 8: Order Creation

**Buyer Agent calls:**

```
Tool: accept_quote
  negotiation_id: "neg_abc123"
  quote_id: "quote_xyz"
```

**System creates order:**

```
POST /api/v1/orders
Body: { "quote_id": "quote_xyz", "idempotency_key": "idk_abc123" }

Response:
{
  "id": "order_def456",
  "status": "created",
  "total_cents": 2750,
  "idempotency_key": "idk_abc123"
}
```

**Audit event logged:**
```json
{
  "event_type": "order.created",
  "explanation": "Order created from accepted quote. Total: ₹2,750. Awaiting payment."
}
```

---

### Step 9: Payment Initiation

**Frontend calls:**

```
POST /api/v1/orders/order_def456/pay
Body: { "payment_method": "upi" }

Response:
{
  "razorpay_order_id": "order_rp_xyz789",
  "razorpay_key_id": "rzp_test_...",
  "amount": 275000,
  "currency": "INR"
}
```

**System flow:**
1. Idempotency key checked (not duplicate)
2. Redis lock acquired on order
3. Order status: `CREATED` → `PENDING_PAYMENT` → `PAYMENT_INITIATED`
4. Razorpay test-mode order created
5. Audit event logged

---

### Step 10: Razorpay Checkout (Test Mode)

**Frontend opens Razorpay Checkout:**
- Buyer enters test UPI ID or test card
- Completes test payment
- Razorpay processes (test mode, no real money)

**Razorpay sends webhook:**

```
POST /api/v1/webhooks/razorpay
Headers: X-Razorpay-Signature: {hmac_signature}
Body: {
  "event": "payment.captured",
  "payload": {
    "payment": { "id": "pay_abc123", "amount": 275000 }
  }
}
```

---

### Step 11: Payment Confirmation

**System flow:**
1. Webhook signature verified (HMAC-SHA256)
2. Payment ID matches order's razorpay_order_id
3. Order status: `PAYMENT_INITIATED` → `PAYMENT_CAPTURED` → `COMPLETED`
4. Stock decremented: Headphones Pro (1), Case (1), Audio Cable (1)
5. Audit event logged

**Audit event:**
```json
{
  "event_type": "order.payment_captured",
  "explanation": "Razorpay confirmed payment of ₹2,750 via UPI. Order completed. Stock decremented for 3 items."
}
```

---

### Step 12: Explanation Endpoint

```
GET /api/v1/orders/order_def456/explanation

Response:
{
  "steps": [
    {
      "action": "buyer_search",
      "reason": "Buyer searched for wireless headphones for gym under ₹3,000",
      "timestamp": "2026-09-05T10:30:00Z"
    },
    {
      "action": "recommendation",
      "reason": "System recommended Gym Audio Kit bundle (₹2,799) with ₹498 savings",
      "timestamp": "2026-09-05T10:30:05Z"
    },
    {
      "action": "negotiation",
      "reason": "Buyer negotiated bundle price from ₹2,799 to ₹2,750",
      "timestamp": "2026-09-05T10:31:00Z"
    },
    {
      "action": "policy_evaluation",
      "reason": "Discount of 16.6% approved (within 20% cap). All inventory in stock.",
      "timestamp": "2026-09-05T10:31:01Z"
    },
    {
      "action": "payment",
      "reason": "Payment of ₹2,750 initiated via UPI test mode",
      "timestamp": "2026-09-05T10:31:05Z"
    },
    {
      "action": "completion",
      "reason": "Razorpay confirmed payment. Order completed. Stock decremented.",
      "timestamp": "2026-09-05T10:31:10Z"
    }
  ]
}
```

---

## Failure Scenario: Payment Failure

**What happens if Razorpay reports payment failed:**

1. Webhook received: `payment.failed`
2. Signature verified
3. Order status: `PAYMENT_INITIATED` → `PAYMENT_FAILED`
4. Audit event logged with failure reason
5. Buyer sees "Payment failed" message
6. Buyer can retry (up to 3 attempts)
7. Each retry uses same idempotency_key → no duplicate charges

**Audit event:**
```json
{
  "event_type": "order.payment_failed",
  "explanation": "Razorpay reported payment failure. Reason: Insufficient funds. Order available for retry (attempt 1/3)."
}
```

---

## Failure Scenario: Policy Rejection

**What happens if buyer requests 25% discount:**

1. Buyer agent proposes quote with 25% discount
2. Policy engine evaluates: `discount_cap: FAIL (25% > 20% max)`
3. Verdict: REJECT
4. Quote stored with `policy_decision: "REJECT"`, `policy_reason: "Discount exceeds cap"`
5. Agent told: "Sorry, the maximum discount available is 20%."
6. Audit event logged

**Audit event:**
```json
{
  "event_type": "quote.rejected",
  "explanation": "Quote rejected by policy: discount of 25% exceeds merchant cap of 20%.",
  "policy_decision": {
    "verdict": "REJECT",
    "reason": "Discount of 25% exceeds cap of 20%",
    "applied_policies": ["discount_cap:FAIL"]
  }
}
```

---

## Demo Talking Points

1. **LLM proposes, code disposes** — The agent suggested products and negotiated, but prices came from the database.
2. **Policy engine is deterministic** — Every quote was checked by rules, not by the LLM.
3. **Full audit trail** — Every action logged with explanation. No black boxes.
4. **Idempotency** — Duplicate payment attempts are blocked at the database level.
5. **Razorpay test mode** — Real integration, real webhooks, but no real money.
6. **Agent scoping** — Buyer agent can search and propose, but cannot execute payments.

---

## Demo Checklist

- [ ] Server running on localhost:8000
- [ ] Database seeded with sample products
- [ ] Razorpay test credentials configured
- [ ] Browser open on demo UI
- [ ] Admin dashboard accessible
- [ ] Audit log viewer working
- [ ] Test UPI/card available for Razorpay checkout
