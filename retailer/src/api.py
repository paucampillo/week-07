"""FastAPI router with all retailer endpoints."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from src.models import CustomerOrder, PurchaseOrder, RetailerProduct, RetailerState
from src.db import get_session
from src.services import advance_day, create_customer_order, create_purchase_order, get_state

router = APIRouter(prefix="/api", tags=["retailer"])


# ── Schemas ────────────────────────────────────────────────────────────────

class CustomerOrderCreate(BaseModel):
    model: str
    quantity: int
    customer_name: str = "anonymous"


class PurchaseOrderCreate(BaseModel):
    model: str
    quantity: int


class PriceUpdate(BaseModel):
    price: float


# ── Catalog & Stock ────────────────────────────────────────────────────────

@router.get("/catalog")
async def get_catalog(session: Session = Depends(get_session)):
    products = session.exec(select(RetailerProduct)).all()
    return [p.model_dump() for p in products]


@router.get("/stock")
async def get_stock(session: Session = Depends(get_session)):
    products = session.exec(select(RetailerProduct)).all()
    return [{"model": p.model, "stock": p.stock} for p in products]


# ── Customer Orders ────────────────────────────────────────────────────────

@router.post("/orders", status_code=201)
async def create_order(
    payload: CustomerOrderCreate,
    session: Session = Depends(get_session),
):
    try:
        order = create_customer_order(session, payload.model, payload.quantity, payload.customer_name)
        session.commit()
        session.refresh(order)
        return order.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/orders")
async def list_orders(
    status: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    query = select(CustomerOrder)
    if status:
        query = query.where(CustomerOrder.status == status)
    return [o.model_dump() for o in session.exec(query.order_by(CustomerOrder.id)).all()]


@router.get("/orders/{order_id}")
async def get_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(CustomerOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    return order.model_dump()


@router.post("/orders/{order_id}/fulfill")
async def fulfill_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(CustomerOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if order.status == "fulfilled":
        return {"success": True, "order_id": order_id, "status": "already fulfilled"}
    product = session.get(RetailerProduct, order.model)
    if not product or product.stock < order.quantity:
        raise HTTPException(
            status_code=422,
            detail=f"Insufficient stock: have {product.stock if product else 0}, need {order.quantity}",
        )
    product.stock -= order.quantity
    state = get_state(session)
    order.status = "fulfilled"
    order.day_fulfilled = state.current_day
    session.commit()
    return {"success": True, "order_id": order_id, "new_status": "fulfilled"}


@router.post("/orders/{order_id}/backorder")
async def backorder_order(order_id: int, session: Session = Depends(get_session)):
    order = session.get(CustomerOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if order.status not in ("pending",):
        raise HTTPException(status_code=422, detail=f"Cannot backorder order with status '{order.status}'")
    order.status = "backordered"
    session.commit()
    return {"success": True, "order_id": order_id, "new_status": "backordered"}


# ── Purchase Orders (to manufacturer) ─────────────────────────────────────

@router.post("/purchases", status_code=201)
async def create_purchase(
    payload: PurchaseOrderCreate,
    session: Session = Depends(get_session),
):
    try:
        po = create_purchase_order(session, payload.model, payload.quantity)
        session.commit()
        session.refresh(po)
        return po.model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/purchases")
async def list_purchases(
    status: Optional[str] = Query(None),
    session: Session = Depends(get_session),
):
    query = select(PurchaseOrder)
    if status:
        query = query.where(PurchaseOrder.status == status)
    return [po.model_dump() for po in session.exec(query.order_by(PurchaseOrder.id)).all()]


# ── Pricing ────────────────────────────────────────────────────────────────

@router.put("/price/{model}")
async def set_price(
    model: str,
    payload: PriceUpdate,
    session: Session = Depends(get_session),
):
    product = session.get(RetailerProduct, model)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product '{model}' not found")
    min_price = product.wholesale_price * 1.15
    if payload.price < min_price:
        raise HTTPException(
            status_code=422,
            detail=f"Price {payload.price} is below minimum {min_price:.2f} (wholesale + 15%)",
        )
    product.retail_price = payload.price
    session.commit()
    return {"success": True, "model": model, "new_price": payload.price}


# ── Day ────────────────────────────────────────────────────────────────────

@router.get("/day/current")
async def get_day(session: Session = Depends(get_session)):
    state = get_state(session)
    return {"current_day": state.current_day}


@router.post("/day/advance")
async def advance(session: Session = Depends(get_session)):
    return advance_day(session)
