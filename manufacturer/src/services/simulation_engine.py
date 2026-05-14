"""Day-stepped simulation engine for the manufacturer app."""

import json
import random
from typing import List

import numpy as np
from sqlmodel import Session, select

from src.models.entities import (
    BOM,
    Event,
    Inventory,
    ManufacturingOrder,
    Product,
    PurchaseOrder,
    SalesOrder,
    SimState,
)
from src.services.external_procurement import poll_external_purchases


def get_sim_state(session: Session) -> SimState:
    """Get the simulation state singleton."""
    state = session.get(SimState, 1)
    if not state:
        state = SimState(id=1)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state


def log_event(
    session: Session,
    event_type: str,
    sim_date: int,
    category: str,
    entity_type: str = None,
    entity_id: int = None,
    details: dict = None,
) -> Event:
    """Append an event to the audit trail."""
    event = Event(
        event_type=event_type,
        sim_date=sim_date,
        category=category,
        entity_type=entity_type,
        entity_id=entity_id,
        details=json.dumps(details) if details else None,
    )
    session.add(event)
    return event


def step_1_complete_orders(session: Session, day: int) -> List[dict]:
    """Complete in-progress orders and consume reserved materials."""
    completed = []
    orders = session.exec(
        select(ManufacturingOrder).where(ManufacturingOrder.status == "in_progress")
    ).all()

    for order in orders:
        bom_entries = session.exec(
            select(BOM).where(BOM.finished_product_id == order.product_id)
        ).all()

        consumption_plan = []
        shortages = []
        for bom in bom_entries:
            inv = session.exec(
                select(Inventory).where(Inventory.product_id == bom.material_id)
            ).first()
            needed = bom.quantity * order.quantity
            available_qty = inv.quantity if inv else 0
            if not inv or available_qty < needed:
                shortages.append(
                    {
                        "material_id": bom.material_id,
                        "needed": needed,
                        "available": available_qty,
                    }
                )
                continue
            consumption_plan.append((inv, needed, bom.material_id))

        if shortages:
            log_event(
                session,
                "PRODUCTION_BLOCKED",
                day,
                "production",
                "manufacturing_order",
                order.id,
                {"shortages": shortages},
            )
            continue

        for inv, needed, material_id in consumption_plan:
            inv.quantity = max(0, inv.quantity - needed)
            inv.reserved = max(0, inv.reserved - needed)
            log_event(
                session,
                "STOCK_UPDATED",
                day,
                "inventory",
                "inventory",
                material_id,
                {"change": -needed, "reason": "production_consumption"},
            )

        order.status = "completed"
        order.completed_date = day
        log_event(
            session,
            "PRODUCTION_COMPLETED",
            day,
            "production",
            "manufacturing_order",
            order.id,
            {"product_id": order.product_id, "quantity": order.quantity},
        )
        completed.append({"order_id": order.id, "quantity": order.quantity})

    return completed


def step_2_deliver_goods(session: Session, day: int) -> List[dict]:
    """Process local supplier purchase order deliveries due today."""
    delivered = []
    purchase_orders = session.exec(
        select(PurchaseOrder).where(
            PurchaseOrder.status == "pending",
            PurchaseOrder.expected_delivery <= day,
        )
    ).all()

    for po in purchase_orders:
        po.status = "delivered"
        po.actual_delivery = day

        inv = session.exec(
            select(Inventory).where(Inventory.product_id == po.product_id)
        ).first()
        if inv:
            inv.quantity += po.quantity
        else:
            session.add(Inventory(product_id=po.product_id, quantity=po.quantity))

        log_event(
            session,
            "PURCHASE_DELIVERED",
            day,
            "purchasing",
            "purchase_order",
            po.id,
            {"product_id": po.product_id, "quantity": po.quantity},
        )
        delivered.append({"po_id": po.id, "product_id": po.product_id, "quantity": po.quantity})

    return delivered


def step_3_spawn_demand(
    session: Session,
    day: int,
    mean_orders: float = 2.0,
    enabled: bool = True,
) -> List[dict]:
    """Generate random demand using a Poisson distribution."""
    if not enabled:
        return []

    spawned = []
    num_orders = max(0, np.random.poisson(mean_orders))
    finished_products = session.exec(
        select(Product).where(Product.type == "finished")
    ).all()
    if not finished_products:
        return []

    for _ in range(num_orders):
        product = random.choice(finished_products)
        qty = max(1, int(np.random.normal(5, 2)))
        order = ManufacturingOrder(
            created_date=day,
            product_id=product.id,
            quantity=qty,
            status="pending",
        )
        session.add(order)
        session.flush()
        log_event(
            session,
            "ORDER_CREATED",
            day,
            "demand",
            "manufacturing_order",
            order.id,
            {"product": product.name, "quantity": qty},
        )
        spawned.append({"order_id": order.id, "product": product.name, "quantity": qty})

    return spawned


def step_4_start_production(session: Session, day: int, capacity: int) -> List[dict]:
    """Start released orders using unit-based daily capacity."""
    started = []
    available_capacity = capacity

    in_progress = session.exec(
        select(ManufacturingOrder).where(ManufacturingOrder.status == "in_progress")
    ).all()
    available_capacity -= sum(order.quantity for order in in_progress)
    if available_capacity <= 0:
        return []

    released = session.exec(
        select(ManufacturingOrder)
        .where(ManufacturingOrder.status == "released")
        .order_by(ManufacturingOrder.priority.desc(), ManufacturingOrder.created_date)
    ).all()

    for order in released:
        if available_capacity <= 0:
            break
        if order.quantity > available_capacity:
            continue

        bom_entries = session.exec(
            select(BOM).where(BOM.finished_product_id == order.product_id)
        ).all()
        can_produce = True
        material_needs = []
        for bom in bom_entries:
            inv = session.exec(
                select(Inventory).where(Inventory.product_id == bom.material_id)
            ).first()
            available = (inv.quantity - inv.reserved) if inv else 0
            needed = bom.quantity * order.quantity
            if available < needed:
                can_produce = False
                break
            material_needs.append((inv, needed))

        if not can_produce:
            continue

        for inv, needed in material_needs:
            inv.reserved += needed
        order.status = "in_progress"
        available_capacity -= order.quantity

        log_event(
            session,
            "PRODUCTION_STARTED",
            day,
            "production",
            "manufacturing_order",
            order.id,
            {
                "product_id": order.product_id,
                "quantity": order.quantity,
                "remaining_capacity": available_capacity,
            },
        )
        started.append({"order_id": order.id, "quantity": order.quantity})

    return started


def step_sales_advance(session: Session, day: int) -> dict:
    """Advance SalesOrder states during day cycle."""
    started, shipped, delivered = [], [], []

    # Released orders → in_production (consume BOM materials)
    released = session.exec(
        select(SalesOrder).where(SalesOrder.status == "released")
    ).all()
    for order in released:
        product = session.exec(
            select(Product).where(Product.name == order.model)
        ).first()
        if not product:
            continue
        bom_entries = session.exec(
            select(BOM).where(BOM.finished_product_id == product.id)
        ).all()
        can_produce = True
        needs = []
        for bom in bom_entries:
            inv = session.exec(
                select(Inventory).where(Inventory.product_id == bom.material_id)
            ).first()
            available = (inv.quantity - inv.reserved) if inv else 0
            needed = bom.quantity * order.quantity
            if available < needed:
                can_produce = False
                break
            needs.append((inv, needed))
        if not can_produce:
            continue
        for inv, needed in needs:
            inv.reserved += needed
        order.status = "in_production"
        order.day_in_production = day
        log_event(
            session, "SALES_PRODUCTION_STARTED", day, "sales",
            "sales_order", order.id,
            {"model": order.model, "quantity": order.quantity, "retailer": order.retailer_name},
        )
        started.append({"order_id": order.id, "model": order.model, "quantity": order.quantity})

    # in_production orders done (1+ day in production) → shipped → delivered
    in_prod = session.exec(
        select(SalesOrder).where(SalesOrder.status == "in_production")
    ).all()
    for order in in_prod:
        if order.day_in_production is None or order.day_in_production >= day:
            continue
        # Consume reserved materials
        product = session.exec(
            select(Product).where(Product.name == order.model)
        ).first()
        if product:
            for bom in session.exec(
                select(BOM).where(BOM.finished_product_id == product.id)
            ).all():
                inv = session.exec(
                    select(Inventory).where(Inventory.product_id == bom.material_id)
                ).first()
                if inv:
                    needed = bom.quantity * order.quantity
                    inv.quantity = max(0, inv.quantity - needed)
                    inv.reserved = max(0, inv.reserved - needed)
        order.status = "shipped"
        order.day_shipped = day
        log_event(
            session, "SALES_ORDER_SHIPPED", day, "sales",
            "sales_order", order.id,
            {"model": order.model, "quantity": order.quantity, "retailer": order.retailer_name},
        )
        shipped.append({"order_id": order.id})

    # Shipped → delivered same day
    just_shipped = session.exec(
        select(SalesOrder).where(
            SalesOrder.status == "shipped",
            SalesOrder.day_shipped == day,
        )
    ).all()
    for order in just_shipped:
        order.status = "delivered"
        order.day_delivered = day
        log_event(
            session, "SALES_ORDER_DELIVERED", day, "sales",
            "sales_order", order.id,
            {"model": order.model, "quantity": order.quantity, "retailer": order.retailer_name},
        )
        delivered.append({"order_id": order.id, "model": order.model, "quantity": order.quantity})

    return {"started": started, "shipped": shipped, "delivered": delivered}


def advance_day(session: Session, mean_orders: float = 2.0, spawn_demand: bool = True) -> dict:
    """Run the complete daily simulation cycle."""
    state = get_sim_state(session)
    if state.is_paused:
        return {"success": False, "day": state.current_day, "reason": "paused"}

    day = state.current_day
    log_event(session, "DAY_STARTED", day, "simulation", details={"day": day})

    # Provider is advanced first by convention; poll against incoming day.
    external_sync = poll_external_purchases(session, day=day + 1)

    completed = step_1_complete_orders(session, day)
    delivered = step_2_deliver_goods(session, day)
    spawned = step_3_spawn_demand(session, day, mean_orders=mean_orders, enabled=spawn_demand)
    started = step_4_start_production(session, day, state.capacity_per_day)
    sales_result = step_sales_advance(session, day)

    state.current_day += 1
    from datetime import datetime

    state.last_saved = datetime.utcnow().isoformat()
    log_event(
        session,
        "DAY_COMPLETED",
        day,
        "simulation",
        details={
            "external_deliveries": len(external_sync["delivered"]),
            "completed_orders": len(completed),
            "deliveries": len(delivered),
            "new_orders": len(spawned),
            "production_started": len(started),
            "sales_started": len(sales_result["started"]),
            "sales_delivered": len(sales_result["delivered"]),
        },
    )
    session.commit()

    return {
        "success": True,
        "day": day,
        "new_day": state.current_day,
        "external_sync": external_sync,
        "completed_orders": completed,
        "deliveries": delivered,
        "new_orders": spawned,
        "production_started": started,
        "sales": sales_result,
    }

