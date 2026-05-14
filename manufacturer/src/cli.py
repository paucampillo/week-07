"""Argparse CLI for manufacturer operations."""

import argparse
import json

import uvicorn
from sqlmodel import Session, select

from src.models.entities import Inventory, Product, SalesOrder, WholesalePrice

from src.services.database import engine, init_db, load_seed_data
from src.services.external_procurement import (
    create_external_purchase,
    fetch_provider_catalog,
    get_provider_configs,
    list_external_purchases,
)
from src.services.provider_client import ProviderClientError
from src.services.simulation_engine import advance_day, get_sim_state


def _bootstrap() -> None:
    init_db()
    load_seed_data()


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="manufacturer-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    # suppliers
    suppliers = sub.add_parser("suppliers")
    suppliers_sub = suppliers.add_subparsers(dest="suppliers_command", required=True)
    suppliers_sub.add_parser("list")
    suppliers_catalog = suppliers_sub.add_parser("catalog")
    suppliers_catalog.add_argument("supplier")

    # purchase
    purchase = sub.add_parser("purchase")
    purchase_sub = purchase.add_subparsers(dest="purchase_command", required=True)
    purchase_create = purchase_sub.add_parser("create")
    purchase_create.add_argument("--supplier", required=True)
    purchase_create.add_argument("--product", required=True)
    purchase_create.add_argument("--qty", required=True, type=int)
    purchase_list = purchase_sub.add_parser("list")
    purchase_list.add_argument("--status", default=None)

    # day
    day = sub.add_parser("day")
    day_sub = day.add_subparsers(dest="day_command", required=True)
    day_sub.add_parser("advance")
    day_sub.add_parser("current")

    # serve
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8002)

    # stock
    sub.add_parser("stock")

    # sales (inbound orders from retailers)
    sales = sub.add_parser("sales")
    sales_sub = sales.add_subparsers(dest="sales_command", required=True)
    so_list = sales_sub.add_parser("orders")
    so_list.add_argument("--status", default=None)
    so_get = sales_sub.add_parser("order")
    so_get.add_argument("id", type=int)

    # production
    production = sub.add_parser("production")
    production_sub = production.add_subparsers(dest="production_command", required=True)
    prod_release = production_sub.add_parser("release")
    prod_release.add_argument("order_id", type=int)
    production_sub.add_parser("status")

    # capacity
    sub.add_parser("capacity")

    # price
    price = sub.add_parser("price")
    price_sub = price.add_subparsers(dest="price_command", required=True)
    price_sub.add_parser("list")
    price_set = price_sub.add_parser("set")
    price_set.add_argument("model")
    price_set.add_argument("price", type=float)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "suppliers":
        if args.suppliers_command == "list":
            provider_rows = []
            for cfg in get_provider_configs():
                row = {"name": cfg.name, "url": cfg.url}
                try:
                    catalog = fetch_provider_catalog(cfg.name)
                    row["status"] = "ok"
                    row["catalog_items"] = len(catalog)
                except (ValueError, ProviderClientError) as exc:
                    row["status"] = "error"
                    row["error"] = str(exc)
                provider_rows.append(row)
            _print(provider_rows)
            return
        if args.suppliers_command == "catalog":
            try:
                _print(fetch_provider_catalog(args.supplier))
            except (ValueError, ProviderClientError) as exc:
                raise SystemExit(str(exc)) from exc
            return

    _bootstrap()

    if args.command == "purchase":
        with Session(engine) as session:
            if args.purchase_command == "create":
                try:
                    order = create_external_purchase(
                        session=session,
                        supplier_name=args.supplier,
                        product_ref=args.product,
                        quantity=args.qty,
                    )
                except (ValueError, ProviderClientError) as exc:
                    raise SystemExit(str(exc)) from exc
                _print(order.model_dump())
                return
            if args.purchase_command == "list":
                rows = [row.model_dump() for row in list_external_purchases(session, status=args.status)]
                _print(rows)
                return

    if args.command == "day":
        with Session(engine) as session:
            if args.day_command == "advance":
                _print(advance_day(session, spawn_demand=False))
                return
            if args.day_command == "current":
                state = get_sim_state(session)
                _print({"current_day": state.current_day})
                return

    if args.command == "stock":
        with Session(engine) as session:
            inventory_rows = session.exec(select(Inventory).order_by(Inventory.product_id.asc())).all()
            output = []
            for row in inventory_rows:
                product = session.get(Product, row.product_id)
                output.append({
                    "product_id": row.product_id,
                    "product_name": product.name if product else None,
                    "quantity": row.quantity,
                    "reserved": row.reserved,
                    "available": row.quantity - row.reserved,
                })
            _print(output)
        return

    if args.command == "sales":
        with Session(engine) as session:
            if args.sales_command == "orders":
                query = select(SalesOrder)
                if args.status:
                    query = query.where(SalesOrder.status == args.status)
                rows = [o.model_dump() for o in session.exec(query.order_by(SalesOrder.id)).all()]
                _print(rows)
                return
            if args.sales_command == "order":
                order = session.get(SalesOrder, args.id)
                if not order:
                    raise SystemExit(f"Sales order {args.id} not found")
                _print(order.model_dump())
                return

    if args.command == "production":
        with Session(engine) as session:
            if args.production_command == "release":
                order = session.get(SalesOrder, args.order_id)
                if not order:
                    raise SystemExit(f"Sales order {args.order_id} not found")
                if order.status != "pending":
                    raise SystemExit(f"Order is '{order.status}', must be 'pending'")
                state = get_sim_state(session)
                order.status = "released"
                order.day_released = state.current_day
                session.commit()
                _print({"success": True, "order_id": args.order_id, "status": "released"})
                return
            if args.production_command == "status":
                in_prod = session.exec(
                    select(SalesOrder).where(SalesOrder.status.in_(["pending", "released", "in_production"]))
                ).all()
                state = get_sim_state(session)
                _print({
                    "current_day": state.current_day,
                    "pending": sum(1 for o in in_prod if o.status == "pending"),
                    "released": sum(1 for o in in_prod if o.status == "released"),
                    "in_production": sum(1 for o in in_prod if o.status == "in_production"),
                    "orders": [o.model_dump() for o in in_prod],
                })
                return

    if args.command == "capacity":
        with Session(engine) as session:
            state = get_sim_state(session)
            in_prod = session.exec(
                select(SalesOrder).where(SalesOrder.status == "in_production")
            ).all()
            units_in_prod = sum(o.quantity for o in in_prod)
            _print({
                "capacity_per_day": state.capacity_per_day,
                "units_in_production": units_in_prod,
                "available_capacity": max(0, state.capacity_per_day - units_in_prod),
            })
        return

    if args.command == "price":
        with Session(engine) as session:
            if args.price_command == "list":
                prices = session.exec(select(WholesalePrice)).all()
                result = []
                for wp in prices:
                    p = session.get(Product, wp.product_id)
                    result.append({"model": p.name if p else None, "price": wp.price})
                _print(result)
                return
            if args.price_command == "set":
                product = session.exec(
                    select(Product).where(Product.name == args.model, Product.type == "finished")
                ).first()
                if not product:
                    raise SystemExit(f"Product '{args.model}' not found")
                wp = session.get(WholesalePrice, product.id)
                if wp:
                    wp.price = args.price
                else:
                    session.add(WholesalePrice(product_id=product.id, price=args.price))
                session.commit()
                _print({"success": True, "model": args.model, "new_price": args.price})
                return

    if args.command == "serve":
        uvicorn.run("src.main:app", host="0.0.0.0", port=args.port, reload=True)
        return


if __name__ == "__main__":
    main()
