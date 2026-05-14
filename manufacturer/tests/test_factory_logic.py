"""Tests for factory engine capacity, consumption and procurement validation."""

import asyncio

import pytest
from fastapi import HTTPException
from sqlmodel import select

from src.api.orders import create_purchase_order
from src.models.entities import (
    Inventory,
    ManufacturingOrder,
    PurchaseOrderCreate,
    Supplier,
)
from src.services.simulation_engine import step_1_complete_orders, step_4_start_production


def test_capacity_is_unit_based_not_order_count(session):
    """Daily capacity must be consumed by order quantity, not order count."""
    order_a = ManufacturingOrder(
        product_id=1,  # P3D-Classic
        quantity=3,
        priority=0,
        created_date=1,
        status="released",
    )
    order_b = ManufacturingOrder(
        product_id=1,
        quantity=2,
        priority=0,
        created_date=2,
        status="released",
    )
    session.add(order_a)
    session.add(order_b)
    session.commit()

    started = step_4_start_production(session, day=1, capacity=4)
    assert len(started) == 1
    assert started[0]["quantity"] == 3

    refreshed_a = session.get(ManufacturingOrder, order_a.id)
    refreshed_b = session.get(ManufacturingOrder, order_b.id)
    assert refreshed_a.status == "in_progress"
    assert refreshed_b.status == "released"


def test_complete_orders_consumes_inventory(session):
    """Completing production must decrement real stock and release reservations."""
    order = ManufacturingOrder(
        product_id=1,  # P3D-Classic uses pcb among other components
        quantity=2,
        priority=0,
        created_date=1,
        status="released",
    )
    session.add(order)
    session.commit()

    started = step_4_start_production(session, day=1, capacity=10)
    assert len(started) == 1

    pcb_inventory_before = session.exec(
        select(Inventory).where(Inventory.product_id == 4)  # pcb
    ).first()
    assert pcb_inventory_before.reserved == 2
    assert pcb_inventory_before.quantity == 5

    completed = step_1_complete_orders(session, day=2)
    assert len(completed) == 1

    pcb_inventory_after = session.exec(
        select(Inventory).where(Inventory.product_id == 4)
    ).first()
    assert pcb_inventory_after.quantity == 3
    assert pcb_inventory_after.reserved == 0

    refreshed_order = session.get(ManufacturingOrder, order.id)
    assert refreshed_order.status == "completed"


def test_purchase_order_requires_explicit_product(session):
    """Procurement must include product_id and match supplier's product."""
    supplier = session.exec(select(Supplier)).first()
    bad_payload = PurchaseOrderCreate(
        supplier_id=supplier.id,
        product_id=supplier.product_id + 1,
        quantity=supplier.min_order_qty,
    )
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(create_purchase_order(bad_payload, session))
    assert exc_info.value.status_code == 422

    good_payload = PurchaseOrderCreate(
        supplier_id=supplier.id,
        product_id=supplier.product_id,
        quantity=supplier.min_order_qty,
    )
    created = asyncio.run(create_purchase_order(good_payload, session))
    assert created.product_id == supplier.product_id
