"""Database initialization, session management and seeding."""

import json
import os
import tempfile
from pathlib import Path
from typing import Generator, Optional

from sqlmodel import SQLModel, Session, create_engine, select

from src.models.entities import (
    Product, BOM, Supplier, Inventory, SimState, Event,
    SalesOrder, WholesalePrice,
)

# ============================================================================
# Engine & Session
# ============================================================================

DEFAULT_DB_PATH = Path(tempfile.gettempdir()) / "week07-manufacturer.db"
DATABASE_URL = os.getenv(
    "MANUFACTURER_DATABASE_URL",
    f"sqlite:///{DEFAULT_DB_PATH.as_posix()}",
)

# WAL mode for concurrent reads; check_same_thread=False for FastAPI async
connect_args = {"check_same_thread": False}
engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args=connect_args
)

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[2] / "data" / "seed-manufacturer.json"
SEED_DATA_PATH = os.getenv("MANUFACTURER_SEED_PATH", str(DEFAULT_SEED_PATH))


def configure_database(database_url: Optional[str] = None) -> None:
    """Reconfigure the engine (used by tests)."""
    global DATABASE_URL, engine
    DATABASE_URL = database_url or DATABASE_URL
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )


def get_session() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a database session."""
    with Session(engine) as session:
        yield session


# ============================================================================
# Database Initialization
# ============================================================================

def init_db() -> None:
    """Create all tables and enable WAL mode."""
    SQLModel.metadata.create_all(engine)

    # Enable WAL mode for better concurrent performance
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")

    # Ensure SimState singleton exists
    with Session(engine) as session:
        state = session.get(SimState, 1)
        if not state:
            session.add(SimState(id=1, current_day=1, capacity_per_day=10))
            session.commit()


def reset_db() -> None:
    """Drop and recreate all tables. Used for simulation reset."""
    SQLModel.metadata.drop_all(engine)
    init_db()
    load_seed_data()


def load_seed_data(path: str = None) -> dict:
    """
    Load initial configuration from a JSON file.

    Creates products, BOM entries, suppliers, and starting inventory.
    Returns a summary dict with counts of created entities.
    """
    path = path or SEED_DATA_PATH
    if not os.path.exists(path):
        return {"status": "no_seed_file", "path": path}

    with open(path, "r", encoding="utf-8") as handle:
        seed = json.load(handle)

    counts = {"products": 0, "bom": 0, "suppliers": 0, "inventory": 0}

    with Session(engine) as session:
        # Check if data already loaded
        existing = session.exec(select(Product)).first()
        if existing:
            return {"status": "already_loaded"}

        # --- Products ---
        product_map = {}  # name -> id
        for p in seed.get("products", []):
            product = Product(**p)
            session.add(product)
            session.flush()
            product_map[product.name] = product.id
            counts["products"] += 1

        # --- BOM entries ---
        for entry in seed.get("bom", []):
            finished_id = product_map.get(entry["finished_product"])
            material_id = product_map.get(entry["material"])
            if finished_id and material_id:
                bom = BOM(
                    finished_product_id=finished_id,
                    material_id=material_id,
                    quantity=entry["quantity"]
                )
                session.add(bom)
                counts["bom"] += 1

        # --- Suppliers ---
        for supplier_data in seed.get("suppliers", []):
            supplier_data = dict(supplier_data)
            prod_id = product_map.get(supplier_data.pop("product_name", None))
            if prod_id:
                supplier = Supplier(product_id=prod_id, **supplier_data)
                session.add(supplier)
                counts["suppliers"] += 1

        # --- Initial Inventory ---
        for inv in seed.get("inventory", []):
            prod_id = product_map.get(inv["product_name"])
            if prod_id:
                inventory = Inventory(
                    product_id=prod_id,
                    quantity=inv["quantity"],
                    reserved=0.0
                )
                session.add(inventory)
                counts["inventory"] += 1

        # --- Wholesale Prices ---
        for model_name, price in seed.get("wholesale_prices", {}).items():
            prod_id = product_map.get(model_name)
            if prod_id:
                session.add(WholesalePrice(product_id=prod_id, price=float(price)))

        # Log seed event
        session.add(Event(
            event_type="SEED_LOADED",
            sim_date=1,
            category="simulation",
            details=json.dumps(counts)
        ))

        capacity = int(seed.get("capacity_per_day", 10))
        state = session.get(SimState, 1)
        if state:
            state.capacity_per_day = capacity
            state.current_day = int(seed.get("current_day", 1))

        session.commit()

    return {"status": "loaded", **counts}
