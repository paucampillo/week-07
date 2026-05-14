"""Database helpers for the provider app."""

import json
import os
import tempfile
from pathlib import Path
from typing import Generator, Optional

from sqlmodel import SQLModel, Session, create_engine, select

from src.models import (
    PricingTier,
    ProviderEvent,
    ProviderProduct,
    ProviderSimState,
    StockItem,
)

DEFAULT_DB_PATH = Path(tempfile.gettempdir()) / "week06-provider.db"
DATABASE_URL = os.getenv(
    "PROVIDER_DATABASE_URL",
    f"sqlite:///{DEFAULT_DB_PATH.as_posix()}",
)
DEFAULT_SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "seed-provider.json"
SEED_DATA_PATH = os.getenv("PROVIDER_SEED_PATH", str(DEFAULT_SEED_PATH))

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False},
)


def configure_database(database_url: Optional[str] = None) -> None:
    """Rebuild engine from a database URL (used by tests)."""
    global DATABASE_URL, engine
    DATABASE_URL = database_url or DATABASE_URL
    engine = create_engine(
        DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        state = session.get(ProviderSimState, 1)
        if not state:
            session.add(ProviderSimState(id=1, current_day=1))
            session.commit()


def reset_db() -> None:
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)


def load_seed_data(path: Optional[str] = None) -> dict:
    """Load provider seed data only when database is empty."""
    path = path or SEED_DATA_PATH
    if not os.path.exists(path):
        return {"status": "no_seed_file", "path": path}

    with Session(engine) as session:
        already = session.exec(select(ProviderProduct)).first()
        if already:
            return {"status": "already_loaded"}

        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)

        day = int(payload.get("current_day", 1))
        state = session.get(ProviderSimState, 1)
        if state:
            state.current_day = max(1, day)
        else:
            session.add(ProviderSimState(id=1, current_day=max(1, day)))

        product_counter = 0
        tier_counter = 0
        stock_counter = 0

        for item in payload.get("products", []):
            tiers = item.pop("tiers", [])
            stock_qty = int(item.pop("stock", 0))
            product = ProviderProduct(**item)
            session.add(product)
            session.flush()
            product_counter += 1

            session.add(StockItem(product_id=product.id, quantity=max(0, stock_qty), reserved=0))
            stock_counter += 1

            for tier in tiers:
                session.add(
                    PricingTier(
                        product_id=product.id,
                        min_qty=int(tier["min_qty"]),
                        unit_price=float(tier["unit_price"]),
                    )
                )
                tier_counter += 1

        session.add(
            ProviderEvent(
                event_type="SEED_LOADED",
                sim_day=max(1, day),
                entity_type="simulation",
                details=json.dumps(
                    {
                        "products": product_counter,
                        "tiers": tier_counter,
                        "stock_items": stock_counter,
                    }
                ),
            )
        )
        session.commit()

    return {
        "status": "loaded",
        "products": product_counter,
        "tiers": tier_counter,
        "stock_items": stock_counter,
    }
