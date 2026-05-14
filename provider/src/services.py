"""Business logic shared by provider API and CLI."""

import json
from datetime import datetime
from typing import Optional

from sqlmodel import Session, delete, select

from src.models import (
    PricingTier,
    ProviderEvent,
    ProviderOrder,
    ProviderProduct,
    ProviderSimState,
    StockItem,
)


def _log_event(
    session: Session,
    event_type: str,
    sim_day: int,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    session.add(
        ProviderEvent(
            event_type=event_type,
            sim_day=sim_day,
            entity_type=entity_type,
            entity_id=entity_id,
            details=json.dumps(details) if details else None,
        )
    )


def _ensure_state(session: Session) -> ProviderSimState:
    state = session.get(ProviderSimState, 1)
    if not state:
        state = ProviderSimState(id=1, current_day=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def get_current_day(session: Session) -> int:
    return _ensure_state(session).current_day


def _resolve_product(session: Session, product_ref: str) -> ProviderProduct:
    product_ref = str(product_ref).strip()
    if not product_ref:
        raise ValueError("product must not be empty")

    product = None
    if product_ref.isdigit():
        product = session.get(ProviderProduct, int(product_ref))
    if not product:
        product = session.exec(select(ProviderProduct).where(ProviderProduct.sku == product_ref)).first()
    if not product:
        product = session.exec(select(ProviderProduct).where(ProviderProduct.name == product_ref)).first()
    if not product:
        raise ValueError(f"product '{product_ref}' not found")
    if not product.active:
        raise ValueError(f"product '{product_ref}' is inactive")
    return product


def _get_stock_row(session: Session, product_id: int) -> StockItem:
    row = session.get(StockItem, product_id)
    if not row:
        row = StockItem(product_id=product_id, quantity=0, reserved=0)
        session.add(row)
        session.flush()
    return row


def _calculate_unit_price(session: Session, product_id: int, quantity: int) -> float:
    tiers = session.exec(
        select(PricingTier)
        .where(PricingTier.product_id == product_id, PricingTier.min_qty <= quantity)
        .order_by(PricingTier.min_qty.desc())
    ).all()
    if tiers:
        return tiers[0].unit_price
    product = session.get(ProviderProduct, product_id)
    return product.base_price


def list_catalog(session: Session) -> list[dict]:
    products = session.exec(
        select(ProviderProduct)
        .where(ProviderProduct.active.is_(True))
        .order_by(ProviderProduct.sku)
    ).all()
    output = []
    for product in products:
        tiers = session.exec(
            select(PricingTier)
            .where(PricingTier.product_id == product.id)
            .order_by(PricingTier.min_qty.asc())
        ).all()
        output.append(
            {
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "description": product.description,
                "base_price": product.base_price,
                "lead_time_days": product.lead_time_days,
                "tiers": [
                    {"min_qty": tier.min_qty, "unit_price": tier.unit_price}
                    for tier in tiers
                ],
            }
        )
    return output


def list_stock(session: Session) -> list[dict]:
    rows = session.exec(select(StockItem)).all()
    output = []
    for row in rows:
        product = session.get(ProviderProduct, row.product_id)
        if not product:
            continue
        output.append(
            {
                "product_id": row.product_id,
                "sku": product.sku,
                "name": product.name,
                "quantity": row.quantity,
                "reserved": row.reserved,
                "available": row.quantity - row.reserved,
            }
        )
    output.sort(key=lambda x: x["sku"])
    return output


def create_order(
    session: Session,
    product_ref: str,
    quantity: int,
    buyer: str = "manufacturer",
) -> ProviderOrder:
    if quantity <= 0:
        raise ValueError("quantity must be > 0")

    state = _ensure_state(session)
    product = _resolve_product(session, product_ref)
    stock = _get_stock_row(session, product.id)

    available = stock.quantity - stock.reserved
    if available < quantity:
        raise ValueError(
            f"insufficient stock for '{product.sku}': need {quantity}, available {available}"
        )

    lead_time = max(1, int(product.lead_time_days))
    expected_delivery_day = state.current_day + lead_time
    unit_price = _calculate_unit_price(session, product.id, quantity)
    total_price = unit_price * quantity

    order = ProviderOrder(
        buyer=buyer,
        product_id=product.id,
        quantity=quantity,
        status="pending",
        unit_price=unit_price,
        total_price=total_price,
        created_day=state.current_day,
        expected_delivery_day=expected_delivery_day,
    )
    stock.reserved += quantity
    stock.updated_at = datetime.utcnow().isoformat()

    session.add(order)
    session.flush()
    _log_event(
        session,
        event_type="ORDER_PLACED",
        sim_day=state.current_day,
        entity_type="order",
        entity_id=order.id,
        details={
            "product": product.sku,
            "buyer": buyer,
            "quantity": quantity,
            "expected_delivery_day": expected_delivery_day,
        },
    )
    session.commit()
    session.refresh(order)
    return order


def list_orders(session: Session, status: Optional[str] = None) -> list[ProviderOrder]:
    query = select(ProviderOrder)
    if status:
        query = query.where(ProviderOrder.status == status)
    query = query.order_by(ProviderOrder.id.asc())
    return session.exec(query).all()


def get_order(session: Session, order_id: int) -> Optional[ProviderOrder]:
    return session.get(ProviderOrder, order_id)


def advance_day(session: Session) -> dict:
    state = _ensure_state(session)
    state.current_day += 1
    state.updated_at = datetime.utcnow().isoformat()
    day = state.current_day

    shipped_orders: list[int] = []
    delivered_orders: list[int] = []

    pending_orders = session.exec(
        select(ProviderOrder).where(ProviderOrder.status == "pending")
    ).all()
    for order in pending_orders:
        order.status = "shipped"
        order.shipped_day = day
        shipped_orders.append(order.id)
        _log_event(
            session,
            event_type="ORDER_SHIPPED",
            sim_day=day,
            entity_type="order",
            entity_id=order.id,
            details={"expected_delivery_day": order.expected_delivery_day},
        )

    in_transit = session.exec(
        select(ProviderOrder).where(ProviderOrder.status.in_(["shipped", "in_transit"]))
    ).all()
    for order in in_transit:
        if order.expected_delivery_day > day:
            continue
        stock = _get_stock_row(session, order.product_id)
        if stock.quantity < order.quantity:
            shortage = order.quantity - stock.quantity
            _log_event(
                session,
                event_type="ORDER_DELAYED",
                sim_day=day,
                entity_type="order",
                entity_id=order.id,
                details={"shortage": shortage, "available": stock.quantity},
            )
            continue
        stock.reserved = max(0, stock.reserved - order.quantity)
        stock.quantity = max(0, stock.quantity - order.quantity)
        stock.updated_at = datetime.utcnow().isoformat()

        order.status = "delivered"
        order.delivered_day = day
        delivered_orders.append(order.id)

        _log_event(
            session,
            event_type="ORDER_DELIVERED",
            sim_day=day,
            entity_type="order",
            entity_id=order.id,
            details={"quantity": order.quantity},
        )
        _log_event(
            session,
            event_type="STOCK_UPDATED",
            sim_day=day,
            entity_type="stock",
            entity_id=order.product_id,
            details={"change": -order.quantity, "reason": "delivery"},
        )

    _log_event(
        session,
        event_type="DAY_ADVANCED",
        sim_day=day,
        entity_type="simulation",
        details={"shipped": len(shipped_orders), "delivered": len(delivered_orders)},
    )
    session.commit()
    return {
        "current_day": day,
        "shipped_orders": shipped_orders,
        "delivered_orders": delivered_orders,
    }


def set_price_tier(
    session: Session,
    product_ref: str,
    min_qty: int,
    unit_price: float,
) -> PricingTier:
    if min_qty <= 0:
        raise ValueError("min_qty must be > 0")
    if unit_price < 0:
        raise ValueError("unit_price must be >= 0")

    product = _resolve_product(session, product_ref)
    existing = session.exec(
        select(PricingTier).where(
            PricingTier.product_id == product.id,
            PricingTier.min_qty == min_qty,
        )
    ).first()
    if existing:
        existing.unit_price = unit_price
        tier = existing
    else:
        tier = PricingTier(product_id=product.id, min_qty=min_qty, unit_price=unit_price)
        session.add(tier)
        session.flush()

    _log_event(
        session,
        event_type="PRICE_TIER_UPDATED",
        sim_day=get_current_day(session),
        entity_type="product",
        entity_id=product.id,
        details={"min_qty": min_qty, "unit_price": unit_price},
    )
    session.commit()
    session.refresh(tier)
    return tier


def restock_product(session: Session, product_ref: str, quantity: int) -> StockItem:
    if quantity <= 0:
        raise ValueError("quantity must be > 0")
    product = _resolve_product(session, product_ref)
    stock = _get_stock_row(session, product.id)
    stock.quantity += quantity
    stock.updated_at = datetime.utcnow().isoformat()

    _log_event(
        session,
        event_type="STOCK_RESTOCKED",
        sim_day=get_current_day(session),
        entity_type="stock",
        entity_id=product.id,
        details={"change": quantity},
    )
    session.commit()
    session.refresh(stock)
    return stock


def export_state(session: Session) -> dict:
    state = _ensure_state(session)
    return {
        "sim_state": state.model_dump(),
        "products": [row.model_dump() for row in session.exec(select(ProviderProduct)).all()],
        "pricing_tiers": [row.model_dump() for row in session.exec(select(PricingTier)).all()],
        "stock": [row.model_dump() for row in session.exec(select(StockItem)).all()],
        "orders": [row.model_dump() for row in session.exec(select(ProviderOrder)).all()],
        "events": [row.model_dump() for row in session.exec(select(ProviderEvent)).all()],
    }


def import_state(session: Session, payload: dict) -> dict:
    required = {"sim_state", "products", "pricing_tiers", "stock", "orders", "events"}
    missing = sorted(required.difference(payload.keys()))
    if missing:
        raise ValueError(f"missing keys in import payload: {', '.join(missing)}")

    for model in [ProviderEvent, ProviderOrder, StockItem, PricingTier, ProviderProduct, ProviderSimState]:
        session.exec(delete(model))
    session.commit()

    state_data = payload["sim_state"] or {"id": 1, "current_day": 1}
    session.add(ProviderSimState(**state_data))

    for row in payload["products"]:
        session.add(ProviderProduct(**row))
    for row in payload["pricing_tiers"]:
        session.add(PricingTier(**row))
    for row in payload["stock"]:
        session.add(StockItem(**row))
    for row in payload["orders"]:
        session.add(ProviderOrder(**row))
    for row in payload["events"]:
        session.add(ProviderEvent(**row))

    session.commit()
    return {"status": "imported", "current_day": state_data.get("current_day", 1)}
