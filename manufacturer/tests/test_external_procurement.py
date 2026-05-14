"""Tests for provider integration in manufacturer."""

import pytest
from sqlmodel import select

from src.models.entities import ExternalPurchaseOrder, Inventory, Product
from src.services.external_procurement import (
    create_external_purchase,
    fetch_provider_catalog,
    list_external_purchases,
    poll_external_purchases,
)
from src.services.provider_client import ProviderConnectionError


class CatalogClientOk:
    def get_catalog(self, provider):
        return [{"sku": "pcb", "name": "PCB Control Board"}]


class OrderClientOk:
    def create_order(self, provider, product, quantity, buyer="manufacturer"):
        return {
            "id": 77,
            "status": "pending",
            "expected_delivery_day": 4,
        }

    def get_order(self, provider, provider_order_id):
        return {
            "id": provider_order_id,
            "status": "delivered",
            "expected_delivery_day": 4,
        }


class CatalogClientDown:
    def get_catalog(self, provider):
        raise ProviderConnectionError(f"Provider '{provider.name}' is unreachable")


def test_suppliers_catalog_fetch_works():
    rows = fetch_provider_catalog("ChipSupply Co", client=CatalogClientOk())
    assert rows[0]["sku"] == "pcb"


def test_create_external_purchase_stores_local_mirror(session):
    pcb = session.exec(select(Product).where(Product.name == "pcb")).first()
    created = create_external_purchase(
        session=session,
        supplier_name="ChipSupply Co",
        product_ref=str(pcb.id),
        quantity=50,
        client=OrderClientOk(),
    )

    assert created.provider_order_id == 77
    assert created.provider_name == "ChipSupply Co"
    assert created.expected_delivery_day == 4

    db_rows = list_external_purchases(session)
    assert len(db_rows) == 1


def test_polling_updates_inventory_when_delivered(session):
    pcb = session.exec(select(Product).where(Product.name == "pcb")).first()
    inventory_before = session.exec(
        select(Inventory).where(Inventory.product_id == pcb.id)
    ).first()
    assert inventory_before.quantity == 5

    create_external_purchase(
        session=session,
        supplier_name="ChipSupply Co",
        product_ref="pcb",
        quantity=50,
        client=OrderClientOk(),
    )
    sync_result = poll_external_purchases(session=session, day=4, client=OrderClientOk())
    assert len(sync_result["delivered"]) == 1

    inventory_after = session.exec(
        select(Inventory).where(Inventory.product_id == pcb.id)
    ).first()
    assert inventory_after.quantity == 55

    order = session.exec(select(ExternalPurchaseOrder)).first()
    assert order.status == "delivered"
    assert order.delivered_day == 4


def test_provider_down_returns_clear_error():
    with pytest.raises(ProviderConnectionError) as exc_info:
        fetch_provider_catalog("ChipSupply Co", client=CatalogClientDown())
    assert "unreachable" in str(exc_info.value).lower()
