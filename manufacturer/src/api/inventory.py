"""
Inventory API router — stock levels and manual adjustments.
"""

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from src.models.entities import Inventory, InventoryRead, Product
from src.services.database import get_session

router = APIRouter(prefix="/inventory", tags=["inventory"])


class InventoryAdjustment(BaseModel):
    """Schema for manual inventory correction."""
    product_id: int
    quantity_change: float
    reason: str = ""


@router.get("/", response_model=List[InventoryRead])
async def list_inventory(session: Session = Depends(get_session)):
    """Get stock levels for all products with availability calculation."""
    items = session.exec(select(Inventory)).all()
    result = []
    for item in items:
        product = session.get(Product, item.product_id)
        result.append(InventoryRead(
            product_id=item.product_id,
            quantity=item.quantity,
            reserved=item.reserved,
            available=item.quantity - item.reserved,
            product_name=product.name if product else None
        ))
    return result


@router.post("/adjust")
async def adjust_inventory(
    adjustment: InventoryAdjustment,
    session: Session = Depends(get_session)
):
    """Manual stock correction (e.g., damaged goods, counting corrections)."""
    inv = session.exec(
        select(Inventory).where(Inventory.product_id == adjustment.product_id)
    ).first()

    if not inv:
        raise HTTPException(
            status_code=404,
            detail=f"No inventory record for product {adjustment.product_id}"
        )

    new_qty = inv.quantity + adjustment.quantity_change
    if new_qty < 0:
        raise HTTPException(
            status_code=422,
            detail=f"Adjustment would result in negative stock ({new_qty})"
        )

    inv.quantity = new_qty

    # Log the adjustment as an event
    from src.models.entities import Event, SimState
    import json
    state = session.get(SimState, 1)
    day = state.current_day if state else 0
    session.add(Event(
        event_type="STOCK_ADJUSTED",
        sim_date=day,
        category="inventory",
        entity_type="inventory",
        entity_id=adjustment.product_id,
        details=json.dumps({
            "change": adjustment.quantity_change,
            "new_quantity": new_qty,
            "reason": adjustment.reason
        })
    ))

    session.commit()
    return {
        "success": True,
        "product_id": adjustment.product_id,
        "new_quantity": new_qty
    }
