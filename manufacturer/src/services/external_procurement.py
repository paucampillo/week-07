"""External procurement logic for provider/manufacturer integration."""

from __future__ import annotations

import json
import os
from typing import Optional

from sqlmodel import Session, select

from src.models.entities import (
    Event,
    ExternalPurchaseOrder,
    Inventory,
    Product,
    SimState,
)
from src.services.provider_client import (
    ProviderApiError,
    ProviderClient,
    ProviderClientError,
    ProviderConfig,
    ProviderConnectionError,
)

DEFAULT_PROVIDERS = [{"name": "ChipSupply Co", "url": "http://localhost:8001"}]


def _current_day(session: Session) -> int:
    state = session.get(SimState, 1)
    if not state:
        state = SimState(id=1, current_day=1, capacity_per_day=10)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state.current_day


def _log(session: Session, event_type: str, day: int, details: dict, entity_id: Optional[int] = None) -> None:
    session.add(
        Event(
            event_type=event_type,
            sim_date=day,
            category="purchasing",
            entity_type="external_purchase_order",
            entity_id=entity_id,
            details=json.dumps(details),
        )
    )


def get_provider_configs() -> list[ProviderConfig]:
    raw = os.getenv("MANUFACTURER_PROVIDERS")
    data = DEFAULT_PROVIDERS
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"MANUFACTURER_PROVIDERS must be valid JSON: {exc}") from exc

    configs: list[ProviderConfig] = []
    for item in data:
        configs.append(ProviderConfig(name=item["name"], url=item["url"]))
    return configs


def get_provider_by_name(provider_name: str) -> ProviderConfig:
    for provider in get_provider_configs():
        if provider.name == provider_name:
            return provider
    raise ValueError(f"Provider '{provider_name}' is not configured")


def fetch_provider_catalog(
    provider_name: str,
    client: Optional[ProviderClient] = None,
) -> list[dict]:
    provider = get_provider_by_name(provider_name)
    client = client or ProviderClient()
    return client.get_catalog(provider)


def _resolve_local_product(session: Session, product_ref: str) -> Product:
    product_ref = str(product_ref).strip()
    product = None
    if product_ref.isdigit():
        product = session.get(Product, int(product_ref))
    if not product:
        product = session.exec(select(Product).where(Product.name == product_ref)).first()
    if not product:
        raise ValueError(f"Local product '{product_ref}' not found")
    return product


def list_external_purchases(
    session: Session,
    status: Optional[str] = None,
) -> list[ExternalPurchaseOrder]:
    query = select(ExternalPurchaseOrder)
    if status:
        query = query.where(ExternalPurchaseOrder.status == status)
    query = query.order_by(ExternalPurchaseOrder.id.asc())
    return session.exec(query).all()


def create_external_purchase(
    session: Session,
    supplier_name: str,
    product_ref: str,
    quantity: int,
    client: Optional[ProviderClient] = None,
) -> ExternalPurchaseOrder:
    if quantity <= 0:
        raise ValueError("Quantity must be > 0")

    provider = get_provider_by_name(supplier_name)
    product = _resolve_local_product(session, product_ref)
    day = _current_day(session)

    client = client or ProviderClient()
    remote_order = client.create_order(
        provider=provider,
        product=product.name,
        quantity=quantity,
        buyer="manufacturer",
    )

    local_order = ExternalPurchaseOrder(
        provider_name=provider.name,
        provider_url=provider.url,
        provider_order_id=int(remote_order["id"]),
        product_id=product.id,
        quantity=quantity,
        status=remote_order.get("status", "pending"),
        created_day=day,
        expected_delivery_day=int(remote_order["expected_delivery_day"]),
    )
    session.add(local_order)
    session.flush()

    _log(
        session,
        event_type="ORDER_PLACED",
        day=day,
        entity_id=local_order.id,
        details={
            "provider_name": provider.name,
            "provider_order_id": local_order.provider_order_id,
            "product": product.name,
            "quantity": quantity,
            "expected_delivery_day": local_order.expected_delivery_day,
        },
    )
    session.commit()
    session.refresh(local_order)
    return local_order


def poll_external_purchases(
    session: Session,
    day: Optional[int] = None,
    client: Optional[ProviderClient] = None,
) -> dict:
    day = day or _current_day(session)
    client = client or ProviderClient()

    pending_orders = session.exec(
        select(ExternalPurchaseOrder).where(
            ExternalPurchaseOrder.status.in_(["pending", "shipped", "in_transit"])
        )
    ).all()

    delivered: list[int] = []
    errors: list[str] = []
    status_updates: list[int] = []

    for order in pending_orders:
        try:
            provider = get_provider_by_name(order.provider_name)
            remote = client.get_order(provider, order.provider_order_id)
        except (ValueError, ProviderConnectionError, ProviderApiError, ProviderClientError) as exc:
            errors.append(str(exc))
            continue

        remote_status = remote.get("status", order.status)
        remote_expected = int(remote.get("expected_delivery_day", order.expected_delivery_day))
        order.expected_delivery_day = remote_expected

        if remote_status != order.status:
            previous_status = order.status
            order.status = remote_status
            status_updates.append(order.id)
            _log(
                session,
                event_type="ORDER_STATUS_UPDATED",
                day=day,
                entity_id=order.id,
                details={
                    "from": previous_status,
                    "to": remote_status,
                    "provider_order_id": order.provider_order_id,
                },
            )

        if remote_status == "delivered" and order.delivered_day is None:
            order.status = "delivered"
            order.delivered_day = day
            inventory = session.exec(
                select(Inventory).where(Inventory.product_id == order.product_id)
            ).first()
            if not inventory:
                inventory = Inventory(product_id=order.product_id, quantity=0.0, reserved=0.0)
                session.add(inventory)
            inventory.quantity += order.quantity
            delivered.append(order.id)

            _log(
                session,
                event_type="ORDER_DELIVERED",
                day=day,
                entity_id=order.id,
                details={
                    "provider_order_id": order.provider_order_id,
                    "quantity": order.quantity,
                },
            )
            _log(
                session,
                event_type="STOCK_UPDATED",
                day=day,
                entity_id=order.id,
                details={
                    "product_id": order.product_id,
                    "change": order.quantity,
                    "reason": "provider_delivery",
                },
            )

    session.commit()
    return {
        "checked": len(pending_orders),
        "delivered": delivered,
        "status_updates": status_updates,
        "errors": errors,
    }
