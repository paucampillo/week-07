"""Pytest fixtures for provider tests."""

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
def configured_db(tmp_path):
    db_file = tmp_path / "provider-test.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    seed_path = Path(__file__).resolve().parents[1] / "data" / "seed-provider.json"

    os.environ["PROVIDER_DATABASE_URL"] = db_url
    os.environ["PROVIDER_SEED_PATH"] = str(seed_path)

    from src import db

    db.configure_database(db_url)
    db.reset_db()
    db.init_db()
    db.load_seed_data(str(seed_path))

    return db


@pytest.fixture()
async def client(configured_db):
    from src.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test-provider",
    ) as async_client:
        yield async_client

