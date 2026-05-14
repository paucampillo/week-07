"""
Orders API router — Manufacturing Orders and Purchase Orders management.
"""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from src.models.entities import (
    ManufacturingOrder, ManufacturingOrderCreate, ManufacturingOrderRead,
    PurchaseOrder, PurchaseOrderCreate, PurchaseOrderRead,
    Product, Supplier, Inventory, BOM, SimState, Event,
    ExternalPurchaseOrder, ExternalPurchaseOrderRead,
)
from src.services.database import get_session
from src.services.external_procurement import (
    create_external_purchase,
    list_external_purchases,
)
from src.services.provider_client import ProviderClientError

router = APIRouter(tags=["orders"])


class ExternalPurchaseCreate(BaseModel):
    supplier: str
    product: str
    qty: int


# ── Manufacturing Orders ───────────────────────────────────────────────────

@router.get("/manufacturing-orders", response_model=List[ManufacturingOrderRead])
async def list_manufacturing_orders(
    status: Optional[str] = Query(None, description="Filter by status"),
    session: Session = Depends(get_session)
):
    """List manufacturing orders with optional status filter."""
    query = select(ManufacturingOrder)
    if status:
        query = query.where(ManufacturingOrder.status == status)
    query = query.order_by(ManufacturingOrder.priority.desc(), ManufacturingOrder.created_date)

    orders = session.exec(query).all()
    result = []
    for order in orders:
        product = session.get(Product, order.product_id)
        result.append(ManufacturingOrderRead(
            id=order.id,
            product_id=order.product_id,
            quantity=order.quantity,
            priority=order.priority,
            created_date=order.created_date,
            status=order.status,
            released_date=order.released_date,
            completed_date=order.completed_date,
            product_name=product.name if product else None
        ))
    return result


@router.post("/manufacturing-orders", response_model=ManufacturingOrderRead, status_code=201)
async def create_manufacturing_order(
    order: ManufacturingOrderCreate,
    session: Session = Depends(get_session)
):
    """Create a manual manufacturing order."""
    product = session.get(Product, order.product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {order.product_id} not found")
    if product.type != "finished":
        raise HTTPException(status_code=422, detail="Can only manufacture finished products")

    state = session.get(SimState, 1)
    day = state.current_day if state else 1

    db_order = ManufacturingOrder(
        product_id=order.product_id,
        quantity=order.quantity,
        priority=order.priority,
        created_date=day,
        status="pending"
    )
    session.add(db_order)

    session.add(Event(
        event_type="ORDER_CREATED",
        sim_date=day,
        category="demand",
        entity_type="manufacturing_order",
        entity_id=db_order.id,
        details=json.dumps({"product": product.name, "quantity": order.quantity, "manual": True})
    ))

    session.commit()
    session.refresh(db_order)
    return ManufacturingOrderRead(
        id=db_order.id,
        product_id=db_order.product_id,
        quantity=db_order.quantity,
        priority=db_order.priority,
        created_date=db_order.created_date,
        status=db_order.status,
        product_name=product.name
    )


@router.post("/manufacturing-orders/{order_id}/release")
async def release_order(order_id: int, session: Session = Depends(get_session)):
    """Release a pending order to production (checks BOM availability)."""
    order = session.get(ManufacturingOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if order.status != "pending":
        raise HTTPException(status_code=422, detail=f"Order is '{order.status}', must be 'pending'")

    # Check material availability
    bom_entries = session.exec(
        select(BOM).where(BOM.finished_product_id == order.product_id)
    ).all()

    shortages = []
    for bom in bom_entries:
        inv = session.exec(
            select(Inventory).where(Inventory.product_id == bom.material_id)
        ).first()
        available = (inv.quantity - inv.reserved) if inv else 0
        needed = bom.quantity * order.quantity
        if available < needed:
            material = session.get(Product, bom.material_id)
            shortages.append({
                "material": material.name if material else str(bom.material_id),
                "needed": needed,
                "available": available,
                "shortage": needed - available
            })

    if shortages:
        raise HTTPException(
            status_code=422,
            detail={
                "title": "Insufficient stock for order release",
                "shortages": shortages,
                "order_id": order_id
            }
        )

    state = session.get(SimState, 1)
    day = state.current_day if state else 1
    order.status = "released"
    order.released_date = day

    session.add(Event(
        event_type="ORDER_RELEASED",
        sim_date=day,
        category="production",
        entity_type="manufacturing_order",
        entity_id=order_id
    ))

    session.commit()
    return {"success": True, "order_id": order_id, "new_status": "released"}


@router.post("/manufacturing-orders/{order_id}/cancel")
async def cancel_order(order_id: int, session: Session = Depends(get_session)):
    """Cancel a manufacturing order."""
    order = session.get(ManufacturingOrder, order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if order.status == "completed":
        raise HTTPException(status_code=422, detail="Cannot cancel a completed order")

    # Release reserved materials if order was in_progress
    if order.status == "in_progress":
        bom_entries = session.exec(
            select(BOM).where(BOM.finished_product_id == order.product_id)
        ).all()
        for bom in bom_entries:
            inv = session.exec(
                select(Inventory).where(Inventory.product_id == bom.material_id)
            ).first()
            if inv:
                inv.reserved = max(0, inv.reserved - bom.quantity * order.quantity)

    state = session.get(SimState, 1)
    day = state.current_day if state else 1
    order.status = "cancelled"

    session.add(Event(
        event_type="ORDER_CANCELLED",
        sim_date=day,
        category="demand",
        entity_type="manufacturing_order",
        entity_id=order_id
    ))

    session.commit()
    return {"success": True, "order_id": order_id, "new_status": "cancelled"}


# ── Purchase Orders ────────────────────────────────────────────────────────

@router.get("/purchase-orders", response_model=List[PurchaseOrderRead])
async def list_purchase_orders(
    status: Optional[str] = Query(None),
    session: Session = Depends(get_session)
):
    """List purchase orders with optional status filter."""
    query = select(PurchaseOrder)
    if status:
        query = query.where(PurchaseOrder.status == status)

    pos = session.exec(query).all()
    result = []
    for po in pos:
        supplier = session.get(Supplier, po.supplier_id)
        result.append(PurchaseOrderRead(
            id=po.id,
            supplier_id=po.supplier_id,
            product_id=po.product_id,
            quantity=po.quantity,
            notes=po.notes,
            issue_date=po.issue_date,
            expected_delivery=po.expected_delivery,
            actual_delivery=po.actual_delivery,
            status=po.status,
            total_cost=po.total_cost,
            supplier_name=supplier.name if supplier else None
        ))
    return result


@router.post("/purchase-orders", response_model=PurchaseOrderRead, status_code=201)
async def create_purchase_order(
    data: PurchaseOrderCreate,
    session: Session = Depends(get_session)
):
    """Issue a new purchase order to a supplier."""
    supplier = session.get(Supplier, data.supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail=f"Supplier {data.supplier_id} not found")
    if not supplier.active:
        raise HTTPException(status_code=422, detail="Supplier is not active")
    if data.quantity < supplier.min_order_qty:
        raise HTTPException(
            status_code=422,
            detail=f"Minimum order quantity is {supplier.min_order_qty}"
        )
    if data.product_id != supplier.product_id:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Supplier {supplier.id} only supplies product {supplier.product_id}, "
                f"but received product {data.product_id}"
            ),
        )

    state = session.get(SimState, 1)
    day = state.current_day if state else 1
    total_cost = supplier.unit_cost * data.quantity

    po = PurchaseOrder(
        supplier_id=data.supplier_id,
        product_id=data.product_id,
        quantity=data.quantity,
        notes=data.notes,
        issue_date=day,
        expected_delivery=day + supplier.lead_time_days,
        total_cost=total_cost,
        status="pending"
    )
    session.add(po)

    session.add(Event(
        event_type="PURCHASE_ORDERED",
        sim_date=day,
        category="purchasing",
        entity_type="purchase_order",
        entity_id=po.id,
        details=json.dumps({
            "supplier": supplier.name,
            "product_id": data.product_id,
            "quantity": data.quantity,
            "total_cost": total_cost,
            "expected_delivery": day + supplier.lead_time_days
        })
    ))

    session.commit()
    session.refresh(po)
    return PurchaseOrderRead(
        id=po.id,
        supplier_id=po.supplier_id,
        product_id=po.product_id,
        quantity=po.quantity,
        notes=po.notes,
        issue_date=po.issue_date,
        expected_delivery=po.expected_delivery,
        actual_delivery=po.actual_delivery,
        status=po.status,
        total_cost=po.total_cost,
        supplier_name=supplier.name
    )


@router.post("/purchase-orders/{po_id}/cancel")
async def cancel_purchase_order(po_id: int, session: Session = Depends(get_session)):
    """Cancel a pending purchase order."""
    po = session.get(PurchaseOrder, po_id)
    if not po:
        raise HTTPException(status_code=404, detail=f"Purchase order {po_id} not found")
    if po.status != "pending":
        raise HTTPException(status_code=422, detail=f"Cannot cancel order with status '{po.status}'")

    state = session.get(SimState, 1)
    day = state.current_day if state else 1
    po.status = "cancelled"

    session.add(Event(
        event_type="PURCHASE_CANCELLED",
        sim_date=day,
        category="purchasing",
        entity_type="purchase_order",
        entity_id=po_id
    ))

    session.commit()
    return {"success": True, "po_id": po_id, "new_status": "cancelled"}


@router.get("/external-purchase-orders", response_model=List[ExternalPurchaseOrderRead])
async def list_external_purchase_orders(
    status: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    return list_external_purchases(session, status=status)


@router.post("/external-purchase-orders", response_model=ExternalPurchaseOrderRead, status_code=201)
async def create_external_purchase_order(
    payload: ExternalPurchaseCreate,
    session: Session = Depends(get_session),
):
    try:
        return create_external_purchase(
            session,
            supplier_name=payload.supplier,
            product_ref=payload.product,
            quantity=payload.qty,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
