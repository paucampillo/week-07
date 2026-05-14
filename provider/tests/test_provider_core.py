"""Provider tests for pricing, lifecycle and import/export."""

import pytest
from sqlmodel import Session, select

from src.models import ProviderEvent, StockItem
from src.services import advance_day, create_order, export_state, import_state


@pytest.mark.asyncio
async def test_pricing_tiers_boundary(configured_db):
    with Session(configured_db.engine) as session:
        order_19 = create_order(session, "pcb", 19)
        order_20 = create_order(session, "pcb", 20)
        order_50 = create_order(session, "pcb", 50)

        assert order_19.unit_price == 30.0
        assert order_20.unit_price == 28.5
        assert order_50.unit_price == 27.0


@pytest.mark.asyncio
async def test_order_expected_delivery_is_not_same_day(client):
    response = await client.post(
        "/api/orders",
        json={"product": "pcb", "quantity": 5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["expected_delivery_day"] > data["created_day"]


@pytest.mark.asyncio
async def test_day_advance_transitions_and_events(client, configured_db):
    response = await client.post(
        "/api/orders",
        json={"product": "pcb", "quantity": 10},
    )
    order_id = response.json()["id"]

    for _ in range(3):
        advance = await client.post("/api/day/advance")
        assert advance.status_code == 200

    order_response = await client.get(f"/api/orders/{order_id}")
    assert order_response.status_code == 200
    assert order_response.json()["status"] == "delivered"

    with Session(configured_db.engine) as session:
        delivered_events = session.exec(
            select(ProviderEvent).where(ProviderEvent.event_type == "ORDER_DELIVERED")
        ).all()
        assert len(delivered_events) >= 1


@pytest.mark.asyncio
async def test_export_import_round_trip(configured_db):
    with Session(configured_db.engine) as session:
        before = export_state(session)
        result = import_state(session, before)
        after = export_state(session)

    assert result["status"] == "imported"
    assert before["sim_state"]["current_day"] == after["sim_state"]["current_day"]
    assert len(before["products"]) == len(after["products"])
    assert len(before["stock"]) == len(after["stock"])


@pytest.mark.asyncio
async def test_provider_never_delivers_more_than_real_stock(configured_db):
    with Session(configured_db.engine) as session:
        order = create_order(session, "pcb", 10)
        # Ship the order first.
        advance_day(session)
        # Simulate unexpected stock shrinkage.
        stock = session.get(StockItem, order.product_id)
        stock.quantity = 4
        session.commit()

        # Move time until expected delivery day.
        advance_day(session)
        advance_day(session)

        refreshed = session.get(type(order), order.id)
        assert refreshed.status == "shipped"
        delayed_events = session.exec(
            select(ProviderEvent).where(ProviderEvent.event_type == "ORDER_DELAYED")
        ).all()
        assert len(delayed_events) >= 1
