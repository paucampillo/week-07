"""Provider REST API router."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from src.db import get_session
from src.models import CreateOrderRequest, DayRead
from src.services import (
    advance_day,
    create_order,
    get_current_day,
    get_order,
    list_catalog,
    list_orders,
    list_stock,
)

router = APIRouter(prefix="/api", tags=["provider"])


@router.get("/catalog")
def get_catalog(session: Session = Depends(get_session)):
    return list_catalog(session)


@router.get("/stock")
def get_stock(session: Session = Depends(get_session)):
    return list_stock(session)


@router.post("/orders")
def post_order(payload: CreateOrderRequest, session: Session = Depends(get_session)):
    try:
        order = create_order(session, payload.product, payload.quantity, payload.buyer)
    except ValueError as exc:
        message = str(exc)
        status_code = 404 if "not found" in message else 422
        raise HTTPException(status_code=status_code, detail=message) from exc
    return order.model_dump()


@router.get("/orders")
def get_orders(
    status: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    return [row.model_dump() for row in list_orders(session, status=status)]


@router.get("/orders/{order_id}")
def get_order_by_id(order_id: int, session: Session = Depends(get_session)):
    order = get_order(session, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"order {order_id} not found")
    return order.model_dump()


@router.post("/day/advance")
def post_day_advance(session: Session = Depends(get_session)):
    return advance_day(session)


@router.get("/day/current", response_model=DayRead)
def get_current_day_route(session: Session = Depends(get_session)):
    return DayRead(current_day=get_current_day(session))
