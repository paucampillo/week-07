"""Sales API — inbound orders from retailers + wholesale prices."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from src.models.entities import (
    Event, Product, SalesOrder, SalesOrderRead, SimState, WholesalePrice,
)
from src.services.database import get_session

router = APIRouter(prefix="/api", tags=["sales"])


# ── Schemas ────────────────────────────────────────────────────────────────

class RetailerOrderCreate(BaseModel):
    retailer_name: str
    model: str
    quantity: int


class PriceUpdate(BaseModel):
    price: float


# ── Day endpoints (for turn engine) ───────────────────────────────────────

@router.get("/day/current")
async def get_current_day(session: Session = Depends(get_session)):
    state = session.get(SimState, 1)
    return {"current_day": state.current_day if state else 1}


@router.post("/day/advance")
async def advance_day_api(session: Session = Depends(get_session)):
    from src.services.simulation_engine import advance_day
    result = advance_day(session, spawn_demand=False)
    if not result["success"]:
        raise HTTPException(status_code=409, detail=result)
    return result


# ── Sales Orders ───────────────────────────────────────────────────────────

@router.post("/orders", response_model=SalesOrderRead, status_code=201)
async def receive_retailer_order(
    payload: RetailerOrderCreate,
    session: Session = Depends(get_session),
):
    """Accept an inbound order from a retailer."""
    product = session.exec(
        select(Product).where(Product.name == payload.model, Product.type == "finished")
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{payload.model}' not found")

    wp = session.get(WholesalePrice, product.id)
    price = wp.price if wp else None
    total = price * payload.quantity if price else None

    state = session.get(SimState, 1)
    day = state.current_day if state else 1

    order = SalesOrder(
        retailer_name=payload.retailer_name,
        model=payload.model,
        quantity=payload.quantity,
        status="pending",
        wholesale_price=price,
        total_value=total,
        day_received=day,
    )
    session.add(order)
    session.flush()

    session.add(Event(
        event_type="SALES_ORDER_RECEIVED",
        sim_date=day,
        category="sales",
        entity_type="sales_order",
        entity_id=order.id,
        details=json.dumps({
            "retailer": payload.retailer_name,
            "model": payload.model,
            "quantity": payload.quantity,
            "total_value": total,
        }),
    ))
    session.commit()
    session.refresh(order)
    return SalesOrderRead(**order.model_dump())


@router.get("/orders", response_model=List[SalesOrderRead])
async def list_sales_orders(
    status: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    query = select(SalesOrder)
    if status:
        query = query.where(SalesOrder.status == status)
    query = query.order_by(SalesOrder.id.asc())
    return [SalesOrderRead(**o.model_dump()) for o in session.exec(query).all()]


@router.get("/orders/{order_id}", response_model=SalesOrderRead)
async def get_sales_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(SalesOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Sales order {order_id} not found")
    return SalesOrderRead(**order.model_dump())


@router.post("/orders/{order_id}/release")
async def release_sales_order(order_id: int, session: Session = Depends(get_session)):
    """Mark a sales order as released for production."""
    order = session.get(SalesOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Sales order {order_id} not found")
    if order.status != "pending":
        raise HTTPException(
            status_code=422, detail=f"Order is '{order.status}', must be 'pending'"
        )

    state = session.get(SimState, 1)
    day = state.current_day if state else 1
    order.status = "released"
    order.day_released = day

    session.add(Event(
        event_type="SALES_ORDER_RELEASED",
        sim_date=day,
        category="sales",
        entity_type="sales_order",
        entity_id=order_id,
    ))
    session.commit()
    return {"success": True, "order_id": order_id, "new_status": "released"}


# ── Wholesale Prices ───────────────────────────────────────────────────────

@router.get("/price-list")
async def get_price_list(session: Session = Depends(get_session)):
    prices = session.exec(select(WholesalePrice)).all()
    result = []
    for wp in prices:
        product = session.get(Product, wp.product_id)
        result.append({
            "product_id": wp.product_id,
            "model": product.name if product else None,
            "price": wp.price,
        })
    return result


@router.put("/price-list/{model}")
async def set_price(
    model: str,
    payload: PriceUpdate,
    session: Session = Depends(get_session),
):
    product = session.exec(
        select(Product).where(Product.name == model, Product.type == "finished")
    ).first()
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{model}' not found")

    wp = session.get(WholesalePrice, product.id)
    if wp:
        wp.price = payload.price
    else:
        session.add(WholesalePrice(product_id=product.id, price=payload.price))

    state = session.get(SimState, 1)
    day = state.current_day if state else 1
    session.add(Event(
        event_type="PRICE_UPDATED",
        sim_date=day,
        category="sales",
        entity_type="product",
        entity_id=product.id,
        details=json.dumps({"model": model, "new_price": payload.price}),
    ))
    session.commit()
    return {"success": True, "model": model, "new_price": payload.price}
