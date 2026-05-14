"""
SQLModel entity definitions for the 3D Printer Production Simulator.

All entities follow the schema defined in docs/PRD.md §4.3.
"""

from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field


# ============================================================================
# Product: raw material or finished printer model
# ============================================================================

class ProductBase(SQLModel):
    """Shared fields for Product creation and reading."""
    name: str = Field(index=True, unique=True)
    type: str = Field(description="Must be 'raw' or 'finished'")
    assembly_time_hours: float = Field(default=0.0)
    description: Optional[str] = None


class Product(ProductBase, table=True):
    """Persistent product entity."""
    __tablename__ = "products"
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: Optional[str] = Field(default_factory=lambda: datetime.utcnow().isoformat())


class ProductCreate(ProductBase):
    """Schema for creating a new product (no id)."""
    pass


class ProductRead(ProductBase):
    """Schema for reading a product (includes id)."""
    id: int
    created_at: Optional[str] = None


# ============================================================================
# BOM: Bill of Materials – recipe linking finished goods to components
# ============================================================================

class BOMBase(SQLModel):
    """Shared fields for BOM entries."""
    finished_product_id: int = Field(foreign_key="products.id")
    material_id: int = Field(foreign_key="products.id")
    quantity: float = Field(gt=0)


class BOM(BOMBase, table=True):
    """Persistent BOM entry."""
    __tablename__ = "bom"
    id: Optional[int] = Field(default=None, primary_key=True)


class BOMCreate(SQLModel):
    """Schema for adding a BOM component."""
    material_id: int
    quantity: float = Field(gt=0)


class BOMRead(BOMBase):
    """Schema for reading a BOM entry (includes id + material name)."""
    id: int
    material_name: Optional[str] = None


# ============================================================================
# Supplier: vendor offering a specific material
# ============================================================================

class SupplierBase(SQLModel):
    """Shared fields for Supplier."""
    name: str
    product_id: int = Field(foreign_key="products.id")
    unit_cost: float
    lead_time_days: int = Field(ge=0)
    min_order_qty: int = Field(default=1)
    active: bool = Field(default=True)


class Supplier(SupplierBase, table=True):
    """Persistent supplier entity."""
    __tablename__ = "suppliers"
    id: Optional[int] = Field(default=None, primary_key=True)


class SupplierCreate(SupplierBase):
    """Schema for creating a supplier (no id)."""
    pass


class SupplierRead(SupplierBase):
    """Schema for reading a supplier (includes id)."""
    id: int


# ============================================================================
# Inventory: current stock per product
# ============================================================================

class Inventory(SQLModel, table=True):
    """Persistent inventory row – one per product."""
    __tablename__ = "inventory"
    product_id: int = Field(foreign_key="products.id", primary_key=True)
    quantity: float = Field(default=0.0)
    reserved: float = Field(default=0.0)
    last_updated: Optional[str] = Field(default_factory=lambda: datetime.utcnow().isoformat())


class InventoryRead(SQLModel):
    """Schema for reading inventory (adds computed availability)."""
    product_id: int
    quantity: float
    reserved: float
    available: float = 0.0
    product_name: Optional[str] = None


# ============================================================================
# ManufacturingOrder: customer demand for finished printers
# ============================================================================

class ManufacturingOrderBase(SQLModel):
    """Shared fields for Manufacturing Orders."""
    product_id: int = Field(foreign_key="products.id")
    quantity: int = Field(gt=0)
    priority: int = Field(default=0)


class ManufacturingOrder(ManufacturingOrderBase, table=True):
    """Persistent manufacturing order entity."""
    __tablename__ = "manufacturing_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    created_date: int
    status: str = Field(default="pending", index=True)
    released_date: Optional[int] = None
    completed_date: Optional[int] = None


class ManufacturingOrderCreate(ManufacturingOrderBase):
    """Schema for creating a manufacturing order."""
    pass


class ManufacturingOrderRead(ManufacturingOrderBase):
    """Schema for reading a manufacturing order."""
    id: int
    created_date: int
    status: str
    released_date: Optional[int] = None
    completed_date: Optional[int] = None
    product_name: Optional[str] = None


# ============================================================================
# PurchaseOrder: replenishment request to a supplier
# ============================================================================

class PurchaseOrderBase(SQLModel):
    """Shared fields for Purchase Orders."""
    supplier_id: int = Field(foreign_key="suppliers.id")
    product_id: int = Field(foreign_key="products.id")
    quantity: int = Field(gt=0)
    notes: Optional[str] = None


class PurchaseOrder(PurchaseOrderBase, table=True):
    """Persistent purchase order entity."""
    __tablename__ = "purchase_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    issue_date: int
    expected_delivery: int
    actual_delivery: Optional[int] = None
    status: str = Field(default="pending", index=True)
    total_cost: Optional[float] = None


class PurchaseOrderCreate(SQLModel):
    """Schema for creating a purchase order."""
    supplier_id: int
    product_id: int
    quantity: int = Field(gt=0)
    notes: Optional[str] = None


class PurchaseOrderRead(PurchaseOrderBase):
    """Schema for reading a purchase order."""
    id: int
    issue_date: int
    expected_delivery: int
    actual_delivery: Optional[int] = None
    status: str
    total_cost: Optional[float] = None
    supplier_name: Optional[str] = None


# ============================================================================
# ExternalPurchaseOrder: remote order placed to provider app
# ============================================================================

class ExternalPurchaseOrderBase(SQLModel):
    """Shared fields for provider-backed purchase orders."""
    provider_name: str = Field(index=True)
    provider_order_id: int = Field(index=True)
    product_id: int = Field(foreign_key="products.id")
    quantity: int = Field(gt=0)


class ExternalPurchaseOrder(ExternalPurchaseOrderBase, table=True):
    """Persistent mirror of provider purchase orders."""
    __tablename__ = "external_purchase_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    status: str = Field(default="pending", index=True)
    created_day: int
    expected_delivery_day: int
    delivered_day: Optional[int] = None
    provider_url: str
    notes: Optional[str] = None


class ExternalPurchaseOrderRead(ExternalPurchaseOrderBase):
    """Read schema for external purchase orders."""
    id: int
    status: str
    created_day: int
    expected_delivery_day: int
    delivered_day: Optional[int] = None
    provider_url: str
    notes: Optional[str] = None


# ============================================================================
# Event: append-only audit trail
# ============================================================================

class Event(SQLModel, table=True):
    """Immutable event log entry."""
    __tablename__ = "events"
    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str
    sim_date: int = Field(index=True)
    category: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    details: Optional[str] = None  # JSON string
    created_at: Optional[str] = Field(default_factory=lambda: datetime.utcnow().isoformat())


class EventRead(SQLModel):
    """Schema for reading events."""
    id: int
    event_type: str
    sim_date: int
    category: Optional[str] = None
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    details: Optional[str] = None
    created_at: Optional[str] = None


# ============================================================================
# SalesOrder: inbound orders from retailers (B2B)
# ============================================================================

class SalesOrder(SQLModel, table=True):
    """Inbound order placed by a retailer to buy finished printers."""
    __tablename__ = "sales_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    retailer_name: str = Field(index=True)
    model: str = Field(index=True)
    quantity: int = Field(gt=0)
    status: str = Field(default="pending", index=True)
    wholesale_price: Optional[float] = None
    total_value: Optional[float] = None
    day_received: int
    day_released: Optional[int] = None
    day_in_production: Optional[int] = None
    day_shipped: Optional[int] = None
    day_delivered: Optional[int] = None


class SalesOrderRead(SQLModel):
    id: int
    retailer_name: str
    model: str
    quantity: int
    status: str
    wholesale_price: Optional[float] = None
    total_value: Optional[float] = None
    day_received: int
    day_released: Optional[int] = None
    day_in_production: Optional[int] = None
    day_shipped: Optional[int] = None
    day_delivered: Optional[int] = None


# ============================================================================
# WholesalePrice: B2B prices per finished product
# ============================================================================

class WholesalePrice(SQLModel, table=True):
    """Wholesale price for a finished product sold to retailers."""
    __tablename__ = "wholesale_prices"
    product_id: int = Field(primary_key=True, foreign_key="products.id")
    price: float


# ============================================================================
# SimState: simulation singleton – tracks current day and config
# ============================================================================

class SimState(SQLModel, table=True):
    """Single-row singleton tracking simulation progress."""
    __tablename__ = "sim_state"
    id: int = Field(default=1, primary_key=True)
    current_day: int = Field(default=1)
    capacity_per_day: int = Field(default=10)
    is_paused: bool = Field(default=False)
    last_saved: Optional[str] = Field(default_factory=lambda: datetime.utcnow().isoformat())


class SimStateRead(SQLModel):
    """Schema for reading simulation state with computed fields."""
    current_day: int
    capacity_per_day: int
    is_paused: bool
    pending_orders_count: int = 0
    in_progress_count: int = 0
