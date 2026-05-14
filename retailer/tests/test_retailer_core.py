"""Core tests for retailer business logic."""

import pytest
from sqlmodel import Session, SQLModel

from src.db import configure_database, engine, init_db, load_seed_data
from src.models import CustomerOrder, RetailerProduct
from src.services import advance_day, create_customer_order


@pytest.fixture(autouse=True)
def fresh_db():
    configure_database("sqlite:///:memory:")
    from src import db as db_module
    SQLModel.metadata.drop_all(db_module.engine)
    init_db()
    # Seed minimal data
    with Session(db_module.engine) as session:
        session.add(RetailerProduct(model="P3D-Classic", retail_price=650.0, wholesale_price=500.0, stock=0))
        session.add(RetailerProduct(model="P3D-Pro", retail_price=1040.0, wholesale_price=800.0, stock=0))
        session.commit()
    yield


def test_customer_order_backordered_when_no_stock():
    from src import db as db_module
    with Session(db_module.engine) as session:
        order = create_customer_order(session, "P3D-Classic", 1)
        session.commit()
        assert order.status == "backordered"
        assert order.day_fulfilled is None


def test_customer_order_fulfilled_when_stock_available():
    from src import db as db_module
    with Session(db_module.engine) as session:
        product = session.get(RetailerProduct, "P3D-Classic")
        product.stock = 5
        session.commit()

        order = create_customer_order(session, "P3D-Classic", 3)
        session.commit()
        assert order.status == "fulfilled"
        assert order.day_fulfilled == 1

        product = session.get(RetailerProduct, "P3D-Classic")
        assert product.stock == 2


def test_backorder_auto_fulfilled_on_day_advance():
    from src import db as db_module
    with Session(db_module.engine) as session:
        order = create_customer_order(session, "P3D-Classic", 2)
        session.commit()
        assert order.status == "backordered"

        product = session.get(RetailerProduct, "P3D-Classic")
        product.stock = 5
        session.commit()

        result = advance_day(session)
        assert len(result["backorders_fulfilled"]) == 1

        order = session.get(CustomerOrder, order.id)
        assert order.status == "fulfilled"


def test_retail_price_above_minimum_markup():
    from src import db as db_module
    with Session(db_module.engine) as session:
        product = session.get(RetailerProduct, "P3D-Classic")
        assert product.retail_price >= product.wholesale_price * 1.15


def test_stock_decremented_on_fulfill():
    from src import db as db_module
    with Session(db_module.engine) as session:
        product = session.get(RetailerProduct, "P3D-Pro")
        product.stock = 10
        session.commit()

        order = create_customer_order(session, "P3D-Pro", 4)
        session.commit()
        assert order.status == "fulfilled"

        product = session.get(RetailerProduct, "P3D-Pro")
        assert product.stock == 6
