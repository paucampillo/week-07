"""SQLModel entities for the Retailer app."""

from typing import Optional
from datetime import datetime
from sqlmodel import SQLModel, Field


class RetailerProduct(SQLModel, table=True):
    """Printer model available for retail sale."""
    __tablename__ = "retailer_products"
    model: str = Field(primary_key=True)
    retail_price: float
    wholesale_price: float
    stock: int = Field(default=0)


class CustomerOrder(SQLModel, table=True):
    """Order placed by an end customer."""
    __tablename__ = "customer_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    customer_name: str = Field(default="anonymous")
    model: str
    quantity: int = Field(gt=0)
    status: str = Field(default="pending")  # pending|fulfilled|backordered|cancelled
    day_received: int
    day_fulfilled: Optional[int] = None


class PurchaseOrder(SQLModel, table=True):
    """Order placed by the retailer to the manufacturer."""
    __tablename__ = "purchase_orders"
    id: Optional[int] = Field(default=None, primary_key=True)
    manufacturer_name: str
    model: str
    quantity: int = Field(gt=0)
    status: str = Field(default="pending")  # pending|confirmed|in_production|shipped|delivered
    manufacturer_order_id: Optional[int] = None
    day_created: int
    day_delivered: Optional[int] = None


class RetailerEvent(SQLModel, table=True):
    """Append-only audit log."""
    __tablename__ = "retailer_events"
    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str
    sim_day: int
    entity_type: Optional[str] = None
    entity_id: Optional[int] = None
    details: Optional[str] = None
    created_at: Optional[str] = Field(default_factory=lambda: datetime.utcnow().isoformat())


class RetailerState(SQLModel, table=True):
    """Singleton tracking current simulation day."""
    __tablename__ = "retailer_state"
    id: int = Field(default=1, primary_key=True)
    current_day: int = Field(default=1)
    manufacturer_name: str = Field(default="Factory")
    manufacturer_url: str = Field(default="http://localhost:8002")
