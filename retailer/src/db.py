"""Database initialization and session management for Retailer app."""

import json
import os
import tempfile
from pathlib import Path
from typing import Generator, Optional

from sqlmodel import SQLModel, Session, create_engine, select

from src.models import RetailerProduct, RetailerState, RetailerEvent

DEFAULT_DB_PATH = Path(tempfile.gettempdir()) / "week07-retailer.db"
DATABASE_URL = os.getenv(
    "RETAILER_DATABASE_URL",
    f"sqlite:///{DEFAULT_DB_PATH.as_posix()}",
)

connect_args = {"check_same_thread": False}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[1] / "data" / "seed-retailer.json"
SEED_DATA_PATH = os.getenv("RETAILER_SEED_PATH", str(DEFAULT_SEED_PATH))


def configure_database(database_url: Optional[str] = None) -> None:
    global DATABASE_URL, engine
    DATABASE_URL = database_url or DATABASE_URL
    engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def init_db() -> None:
    SQLModel.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    with Session(engine) as session:
        state = session.get(RetailerState, 1)
        if not state:
            session.add(RetailerState(id=1))
            session.commit()


def reset_db() -> None:
    SQLModel.metadata.drop_all(engine)
    init_db()
    load_seed_data()


def load_seed_data(path: str = None) -> dict:
    path = path or SEED_DATA_PATH
    if not os.path.exists(path):
        return {"status": "no_seed_file", "path": path}

    with open(path, "r", encoding="utf-8") as f:
        seed = json.load(f)

    with Session(engine) as session:
        existing = session.exec(select(RetailerProduct)).first()
        if existing:
            return {"status": "already_loaded"}

        for p in seed.get("products", []):
            session.add(RetailerProduct(**p))

        state = session.get(RetailerState, 1) or RetailerState(id=1)
        state.current_day = seed.get("day", 1)
        mfr = seed.get("manufacturer", {})
        state.manufacturer_name = mfr.get("name", "Factory")
        state.manufacturer_url = mfr.get("url", "http://localhost:8002")
        session.merge(state)

        session.add(RetailerEvent(
            event_type="SEED_LOADED",
            sim_day=1,
            details=json.dumps({"products": len(seed.get("products", []))}),
        ))
        session.commit()

    return {"status": "loaded", "products": len(seed.get("products", []))}
