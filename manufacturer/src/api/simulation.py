"""Simulation API router: state, day advance, events and import/export."""

import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlmodel import Session, SQLModel, select

from src.models.entities import (
    BOM,
    Event,
    EventRead,
    ExternalPurchaseOrder,
    Inventory,
    ManufacturingOrder,
    Product,
    PurchaseOrder,
    SimState,
    SimStateRead,
    Supplier,
)
from src.services.database import engine, get_session, init_db, reset_db
from src.services.simulation_engine import advance_day, get_sim_state

router = APIRouter(prefix="/simulation", tags=["simulation"])


@router.get("/state", response_model=SimStateRead)
async def get_simulation_state(session: Session = Depends(get_session)):
    state = get_sim_state(session)

    pending = session.exec(
        select(ManufacturingOrder).where(ManufacturingOrder.status == "pending")
    ).all()
    in_progress = session.exec(
        select(ManufacturingOrder).where(ManufacturingOrder.status == "in_progress")
    ).all()

    return SimStateRead(
        current_day=state.current_day,
        capacity_per_day=state.capacity_per_day,
        is_paused=state.is_paused,
        pending_orders_count=len(pending),
        in_progress_count=len(in_progress),
    )


@router.post("/advance")
async def advance_simulation(session: Session = Depends(get_session)):
    result = advance_day(session)
    if not result["success"]:
        return JSONResponse(status_code=409, content=result)
    return result


@router.post("/reset")
async def reset_simulation():
    reset_db()
    return {"success": True, "message": "Simulation reset to day 1"}


@router.post("/pause")
async def toggle_pause(session: Session = Depends(get_session)):
    state = get_sim_state(session)
    state.is_paused = not state.is_paused
    session.commit()
    return {"success": True, "is_paused": state.is_paused}


@router.get("/events", response_model=List[EventRead])
async def list_events(
    category: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    session: Session = Depends(get_session),
):
    query = select(Event)
    if category:
        query = query.where(Event.category == category)
    query = query.order_by(Event.id.desc()).limit(limit)
    return session.exec(query).all()


@router.get("/events/stats")
async def event_stats(session: Session = Depends(get_session)):
    all_events = session.exec(select(Event)).all()
    by_category: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for event in all_events:
        by_category[event.category] = by_category.get(event.category, 0) + 1
        by_type[event.event_type] = by_type.get(event.event_type, 0) + 1
    return {
        "total_events": len(all_events),
        "by_category": by_category,
        "by_type": by_type,
    }


@router.get("/export")
async def export_state(session: Session = Depends(get_session)):
    state = get_sim_state(session)

    def to_dicts(model_class):
        items = session.exec(select(model_class)).all()
        return [item.model_dump() for item in items]

    return {
        "sim_state": state.model_dump(),
        "products": to_dicts(Product),
        "bom": to_dicts(BOM),
        "suppliers": to_dicts(Supplier),
        "inventory": to_dicts(Inventory),
        "manufacturing_orders": to_dicts(ManufacturingOrder),
        "purchase_orders": to_dicts(PurchaseOrder),
        "external_purchase_orders": to_dicts(ExternalPurchaseOrder),
        "events": to_dicts(Event),
    }


@router.post("/import")
async def import_state(file: UploadFile = File(...)):
    try:
        content = await file.read()
        payload = json.loads(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

    required_keys = {
        "sim_state",
        "products",
        "bom",
        "suppliers",
        "inventory",
        "manufacturing_orders",
        "purchase_orders",
        "external_purchase_orders",
        "events",
    }
    missing = sorted(required_keys.difference(payload.keys()))
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing keys: {', '.join(missing)}")

    SQLModel.metadata.drop_all(engine)
    init_db()

    with Session(engine) as session:
        state = payload["sim_state"] or {"id": 1, "current_day": 1, "capacity_per_day": 10}
        session.merge(SimState(**state))

        for row in payload["products"]:
            session.add(Product(**row))
        session.commit()

        for row in payload["bom"]:
            session.add(BOM(**row))
        for row in payload["suppliers"]:
            session.add(Supplier(**row))
        for row in payload["inventory"]:
            session.add(Inventory(**row))
        session.commit()

        for row in payload["manufacturing_orders"]:
            session.add(ManufacturingOrder(**row))
        for row in payload["purchase_orders"]:
            session.add(PurchaseOrder(**row))
        for row in payload["external_purchase_orders"]:
            session.add(ExternalPurchaseOrder(**row))
        session.commit()

        for row in payload["events"]:
            session.add(Event(**row))

        session.commit()

    return {"success": True, "message": "State imported"}
