"""Fixtures for manufacturer tests."""

import os
from pathlib import Path

import pytest
from sqlmodel import Session


@pytest.fixture()
def configured_db(tmp_path):
    db_file = tmp_path / "manufacturer-test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seed-manufacturer.json"

    os.environ["MANUFACTURER_DATABASE_URL"] = db_url
    os.environ["MANUFACTURER_SEED_PATH"] = str(seed_path)
    os.environ["MANUFACTURER_PROVIDERS"] = '[{"name":"ChipSupply Co","url":"http://provider:8001"}]'

    from src.services import database

    database.configure_database(db_url)
    database.reset_db()
    database.load_seed_data(str(seed_path))
    return database


@pytest.fixture()
def session(configured_db):
    with Session(configured_db.engine) as db_session:
        yield db_session
