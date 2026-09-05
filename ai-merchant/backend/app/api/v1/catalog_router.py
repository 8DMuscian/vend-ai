"""AI-readable merchant catalog API endpoints.

Endpoints:
- GET /api/v1/ai/catalog - List products with optional filtering
- GET /api/v1/ai/products/{id} - Get a single product by ID
- POST /api/v1/ai/search - Search products by natural language intent
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import or_, and_, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.product import Product
from sqlalchemy import create_engine

# Create a reusable engine; in production this would be dependency-injected
_engine = create_engine(settings.database_url.replace("postgresql+asyncpg", "postgresql"))


def _get_session() -> Session:
    """Create a new SQLAlchemy session."""
    from sqlalchemy.orm import sessionmaker
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
    return SessionLocal()


def _build_ai_schema_filter(filters: dict, ai_schema: dict) -> bool:
    """Check if a product's ai_schema matches the given filters."""
    if not filters:
        return True
    for key, value in filters.items():
        if key in ai_schema:
            if ai_schema[key] != value:
                return False
        else:
            # Key not present in ai_schema, filter misses
            return False
    return True


catalog_router = APIRouter(prefix="/api/v1/ai", tags=["ai-catalog"])


@catalog_router.get("/catalog", summary="List products in the merchant catalog")
def list_catalog(
    category: str | None = Query(default=None, description="Filter by category"),
    min_price: int | None = Query(default=None, ge=0, description="Minimum price in paise"),
    max_price: int | None = Query(default=None, ge=0, description="Maximum price in paise"),
    in_stock: bool | None = Query(default=None, description="Filter by availability"),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum number of results"),
    offset: int = Query(default=0, ge=0, description="Number of results to skip"),
):
    """Retrieve a paginated list of products from the merchant catalog.

    Parameters:
    - category: Filter products by category name
    - min_price: Only return products with price >= this value (paise)
    - max_price: Only return products with price <= this value (paise)
    - in_stock: Only return products with stock > 0
    - limit: Maximum number of products to return (default: 50)
    - offset: Number of products to skip for pagination (default: 0)

    Returns a structured JSON response with product summaries and ai_schema.
    """
    session: Session = _get_session()
    try:
        from sqlalchemy import select as sa_select

        stmt = sa_select(Product).filter(Product.is_active == True)

        # Category filtering done in Python (avoids SQL injection via JSON operator).
        # The PostgreSQL @> operator with f-string interpolation is unsafe with user input.

        # Filter by price range
        if min_price is not None:
            stmt = stmt.filter(Product.price_cents >= min_price)
        if max_price is not None:
            stmt = stmt.filter(Product.price_cents <= max_price)

        # Filter by stock
        if in_stock is True:
            stmt = stmt.filter(Product.stock_qty > 0)

        # Execute query
        products = session.execute(stmt).scalars().all()

        # Python-level fallback for SQLite compatibility
        if category:
            products = [
                p for p in products if category in p.categories
            ]

        # Build response
        result = []
        for p in products:
            result.append({
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "price_cents": p.price_cents,
                "currency": p.currency,
                "in_stock": p.stock_qty > 0,
                "stock_qty": p.stock_qty,
                "categories": p.categories,
                "use_cases": p.use_cases,
                "compatible_product_ids": p.compatible_product_ids,
                "ai_schema": p.ai_schema,
            })

        total = len(products)

        return {
            "success": True,
            "data": {
                "products": result,
                "total": total,
                "limit": limit,
                "offset": offset,
            },
            "meta": {
                "request_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    finally:
        session.close()


@catalog_router.get("/products/{product_id}", summary="Get a single product by ID")
def get_product(product_id: str):
    """Retrieve a single product by its UUID.

    Returns the full product details including the structured ai_schema
    required for LLM agent consumption.

    Parameters:
    - product_id: UUID of the product to retrieve

    Returns a 404 if the product does not exist or is not active.
    """
    session: Session = _get_session()
    try:
        statement = select(Product).filter(
            Product.id == product_id, Product.is_active == True
        )
        product = session.execute(statement).scalar_one_or_none()

        if not product:
            raise HTTPException(status_code=404, detail="Product not found")

        return {
            "success": True,
            "data": {
                "id": product.id,
                "name": product.name,
                "description": product.description,
                "price_cents": product.price_cents,
                "currency": product.currency,
                "stock_qty": product.stock_qty,
                "in_stock": product.stock_qty > 0,
                "categories": product.categories,
                "use_cases": product.use_cases,
                "compatible_product_ids": product.compatible_product_ids,
                "ai_schema": product.ai_schema,
            },
            "meta": {
                "request_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    finally:
        session.close()


@catalog_router.post("/search", summary="Search products by natural language intent")
def search_products(
    query: str = ...,
    max_price: int | None = Query(default=None, ge=0, description="Maximum price in paise"),
    category: str | None = Query(default=None, description="Filter by category"),
    limit: int = Query(default=10, ge=1, le=100, description="Maximum number of results"),
):
    """Search the merchant catalog using natural language intent.

    The LLM agent sends a natural language query describing what it's looking
    for (e.g. "wireless headphones for gym under 3000"). This endpoint
    performs a text-based search across product names, descriptions, and
    ai_schema fields (tags, use_cases, categories).

    Parameters:
    - query: Natural language buyer intent (required)
    - max_price: Maximum budget in paise (optional)
    - category: Filter by category name (optional)
    - limit: Maximum number of results to return (default: 10)

    Returns product matches ranked by relevance, with the structured
    ai_schema for each match so the agent can reason about specs and
    compatibility.

    Returns a 422 if the query is empty or whitespace-only.
    """
    if not query or not query.strip():
        raise HTTPException(
            status_code=422,
            detail="Search query cannot be empty or whitespace-only",
        )

    session: Session = _get_session()
    try:
        from sqlalchemy import select as sa_select, func

        # Base query for active products
        stmt = sa_select(Product).filter(Product.is_active == True)

        # Text search across name, description, and ai_schema fields
        search_lower = query.lower()
        search_term = f"%{search_lower}%"

        # Filter by category if specified (Python-side, safe from injection)
        if category:
            products = [p for p in products if category in p.categories]

        # Filter by max price if specified
        if max_price is not None:
            stmt = stmt.filter(Product.price_cents <= max_price)

        # Execute query
        products = session.execute(stmt.limit(limit)).scalars().all()

        # Build ranked results with relevance scoring
        results = []
        for p in products:
            relevance = 0
            # Score name matches highest
            if search_lower in (p.name or "").lower():
                relevance += 3
            # Description matches
            if search_lower in (p.description or "").lower():
                relevance += 2
            # ai_schema values matches
            if any(search_lower in str(v).lower() for v in p.ai_schema.values()):
                relevance += 1
            # use_cases matches
            if any(search_lower in str(u).lower() for u in p.use_cases):
                relevance += 2
            # categories matches
            if any(search_lower in str(c).lower() for c in p.categories):
                relevance += 2

            if relevance > 0:
                results.append({
                    "product": {
                        "id": p.id,
                        "name": p.name,
                        "description": p.description,
                        "price_cents": p.price_cents,
                        "currency": p.currency,
                        "in_stock": p.stock_qty > 0,
                    },
                    "relevance_score": relevance,
                    "ai_schema": p.ai_schema,
                })

        # Sort by relevance score (descending)
        results.sort(key=lambda x: x["relevance_score"], reverse=True)

        return {
            "success": True,
            "data": {
                "query": query,
                "results": results[:limit],
                "total": len(results),
            },
            "meta": {
                "request_id": str(uuid.uuid4()),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    finally:
        session.close()