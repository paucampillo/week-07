"""SQLModel entities and API schemas for the provider app."""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class ProviderProduct(SQLModel, table=True):
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True, unique=True)
    name: str
    description: Optional[str] = None
    base_price: float = Field(ge=0)
    lead_time_days: int = Field(default=1, ge=0)
    active: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class PricingTier(SQLModel, table=True):
    __tablename__ = "pricing_tiers"

    id: Optional[int] = Field(default=None, primary_key=True)
    product_id: int = Field(foreign_key="products.id", index=True)
    min_qty: int = Field(ge=1)
    unit_price: float = Field(ge=0)


class StockItem(SQLModel, table=True):
    __tablename__ = "stock"

    product_id: int = Field(foreign_key="products.id", primary_key=True)
    quantity: int = Field(default=0, ge=0)
    reserved: int = Field(default=0, ge=0)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ProviderOrder(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    buyer: str = Field(default="unknown", index=True)
    product_id: int = Field(foreign_key="products.id", index=True)
    quantity: int = Field(gt=0)
    status: str = Field(default="pending", index=True)
    unit_price: float = Field(ge=0)
    total_price: float = Field(ge=0)
    created_day: int = Field(ge=1)
    expected_delivery_day: int = Field(ge=1)
    shipped_day: Optional[int] = None
    delivered_day: Optional[int] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ProviderEvent(SQLModel, table=True):
    __tablename__ = "events"

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str = Field(index=True)
    sim_day: int = Field(index=True)
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    details: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ProviderSimState(SQLModel, table=True):
    __tablename__ = "sim_state"

    id: int = Field(default=1, primary_key=True)
    current_day: int = Field(default=1, ge=1)
    updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


class CreateOrderRequest(SQLModel):
    product: str
    quantity: int = Field(gt=0)
    buyer: str = Field(default="manufacturer")


class CatalogItem(SQLModel):
    id: int
    sku: str
    name: str
    description: Optional[str] = None
    base_price: float
    lead_time_days: int
    tiers: list[dict]


class StockItemRead(SQLModel):
    product_id: int
    sku: str
    name: str
    quantity: int
    reserved: int
    available: int


class DayRead(SQLModel):
    current_day: int
