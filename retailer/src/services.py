"""Business logic for the Retailer app."""

import json
from typing import Optional

import httpx
from sqlmodel import Session, select

from src.models import (
    CustomerOrder, PurchaseOrder, RetailerEvent, RetailerProduct, RetailerState,
)


MIN_MARGIN = 0.15  # retail must be at least 15% above wholesale


def get_state(session: Session) -> RetailerState:
    state = session.get(RetailerState, 1)
    if not state:
        state = RetailerState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def log_event(
    session: Session,
    event_type: str,
    day: int,
    entity_type: str = None,
    entity_id: int = None,
    details: dict = None,
) -> None:
    session.add(RetailerEvent(
        event_type=event_type,
        sim_day=day,
        entity_type=entity_type,
        entity_id=entity_id,
        details=json.dumps(details) if details else None,
    ))


def create_customer_order(session: Session, model: str, quantity: int, customer_name: str = "anonymous") -> CustomerOrder:
    """Place a customer order; fulfill from stock or backorder."""
    state = get_state(session)
    day = state.current_day

    product = session.get(RetailerProduct, model)
    if not product:
        raise ValueError(f"Product '{model}' not in catalog")

    if product.stock >= quantity:
        product.stock -= quantity
        status = "fulfilled"
        day_fulfilled = day
    else:
        status = "backordered"
        day_fulfilled = None

    order = CustomerOrder(
        customer_name=customer_name,
        model=model,
        quantity=quantity,
        status=status,
        day_received=day,
        day_fulfilled=day_fulfilled,
    )
    session.add(order)
    session.flush()
    log_event(session, f"CUSTOMER_ORDER_{status.upper()}", day, "customer_order", order.id,
              {"model": model, "quantity": quantity, "stock_before": product.stock + (quantity if status == "fulfilled" else 0)})
    return order


def create_purchase_order(session: Session, model: str, quantity: int) -> PurchaseOrder:
    """Place a purchase order to the manufacturer."""
    state = get_state(session)
    day = state.current_day

    product = session.get(RetailerProduct, model)
    if not product:
        raise ValueError(f"Product '{model}' not in catalog")

    # Call manufacturer API
    manufacturer_order_id = None
    try:
        resp = httpx.post(
            f"{state.manufacturer_url}/api/orders",
            json={"retailer_name": state.manufacturer_name, "model": model, "quantity": quantity},
            timeout=10,
        )
        if resp.status_code == 201:
            manufacturer_order_id = resp.json().get("id")
        else:
            raise ValueError(f"Manufacturer rejected order: {resp.status_code} {resp.text}")
    except httpx.RequestError as exc:
        raise ValueError(f"Cannot reach manufacturer: {exc}") from exc

    po = PurchaseOrder(
        manufacturer_name=state.manufacturer_name,
        model=model,
        quantity=quantity,
        status="confirmed",
        manufacturer_order_id=manufacturer_order_id,
        day_created=day,
    )
    session.add(po)
    session.flush()
    log_event(session, "PURCHASE_ORDER_CREATED", day, "purchase_order", po.id,
              {"model": model, "quantity": quantity, "manufacturer_order_id": manufacturer_order_id})
    return po


def poll_manufacturer_orders(session: Session, day: int) -> list:
    """Poll manufacturer for delivery status of open purchase orders."""
    state = get_state(session)
    delivered = []

    open_pos = session.exec(
        select(PurchaseOrder).where(PurchaseOrder.status.in_(["confirmed", "in_production", "shipped"]))
    ).all()

    for po in open_pos:
        if not po.manufacturer_order_id:
            continue
        try:
            resp = httpx.get(
                f"{state.manufacturer_url}/api/orders/{po.manufacturer_order_id}",
                timeout=5,
            )
            if resp.status_code != 200:
                continue
            mfr_order = resp.json()
            mfr_status = mfr_order.get("status", "")

            if mfr_status in ("shipped", "delivered") and po.status not in ("delivered",):
                po.status = "delivered"
                po.day_delivered = day
                # Add to stock
                product = session.get(RetailerProduct, po.model)
                if product:
                    product.stock += po.quantity
                log_event(session, "PURCHASE_DELIVERED", day, "purchase_order", po.id,
                          {"model": po.model, "quantity": po.quantity, "new_stock": product.stock if product else None})
                delivered.append({"po_id": po.id, "model": po.model, "quantity": po.quantity})
            elif mfr_status in ("in_production",):
                po.status = "in_production"
            elif mfr_status == "shipped":
                po.status = "shipped"
        except httpx.RequestError:
            pass

    return delivered


def advance_day(session: Session) -> dict:
    """Advance one simulation day."""
    state = get_state(session)
    day = state.current_day

    log_event(session, "DAY_STARTED", day, details={"day": day})

    # Poll manufacturer for deliveries
    delivered = poll_manufacturer_orders(session, day)

    # Auto-fulfill backordered orders if stock now available
    fulfilled = []
    backordered = session.exec(
        select(CustomerOrder).where(CustomerOrder.status == "backordered")
    ).all()
    for order in backordered:
        product = session.get(RetailerProduct, order.model)
        if product and product.stock >= order.quantity:
            product.stock -= order.quantity
            order.status = "fulfilled"
            order.day_fulfilled = day
            log_event(session, "BACKORDER_FULFILLED", day, "customer_order", order.id,
                      {"model": order.model, "quantity": order.quantity})
            fulfilled.append({"order_id": order.id, "model": order.model})

    state.current_day += 1
    log_event(session, "DAY_COMPLETED", day, details={
        "deliveries": len(delivered),
        "backorders_fulfilled": len(fulfilled),
    })
    session.commit()

    return {
        "success": True,
        "day": day,
        "new_day": state.current_day,
        "deliveries": delivered,
        "backorders_fulfilled": fulfilled,
    }
