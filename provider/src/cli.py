"""Argparse CLI for provider operations."""

import argparse
import json
from pathlib import Path

import uvicorn
from sqlmodel import Session

from src.db import engine, init_db, load_seed_data
from src.services import (
    advance_day,
    export_state,
    get_current_day,
    get_order,
    import_state,
    list_catalog,
    list_orders,
    list_stock,
    restock_product,
    set_price_tier,
)


def _bootstrap() -> None:
    init_db()
    load_seed_data()


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="provider-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("catalog")
    sub.add_parser("stock")

    orders = sub.add_parser("orders")
    orders_sub = orders.add_subparsers(dest="orders_command", required=True)
    orders_list = orders_sub.add_parser("list")
    orders_list.add_argument("--status", default=None)
    orders_show = orders_sub.add_parser("show")
    orders_show.add_argument("order_id", type=int)

    price = sub.add_parser("price")
    price_sub = price.add_subparsers(dest="price_command", required=True)
    price_set = price_sub.add_parser("set")
    price_set.add_argument("--product", required=True)
    price_set.add_argument("--min-qty", required=True, type=int)
    price_set.add_argument("--unit-price", required=True, type=float)

    restock = sub.add_parser("restock")
    restock.add_argument("--product", required=True)
    restock.add_argument("--qty", required=True, type=int)

    day = sub.add_parser("day")
    day_sub = day.add_subparsers(dest="day_command", required=True)
    day_sub.add_parser("advance")
    day_sub.add_parser("current")

    export_cmd = sub.add_parser("export")
    export_cmd.add_argument("--output", default="provider-export.json")

    import_cmd = sub.add_parser("import")
    import_cmd.add_argument("--input", required=True, dest="input_path")

    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8001)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _bootstrap()

    if args.command == "catalog":
        with Session(engine) as session:
            _print(list_catalog(session))
        return

    if args.command == "stock":
        with Session(engine) as session:
            _print(list_stock(session))
        return

    if args.command == "orders":
        with Session(engine) as session:
            if args.orders_command == "list":
                _print([row.model_dump() for row in list_orders(session, status=args.status)])
                return
            if args.orders_command == "show":
                row = get_order(session, args.order_id)
                if not row:
                    raise SystemExit(f"Order {args.order_id} not found")
                _print(row.model_dump())
                return

    if args.command == "price" and args.price_command == "set":
        with Session(engine) as session:
            tier = set_price_tier(
                session,
                product_ref=args.product,
                min_qty=args.min_qty,
                unit_price=args.unit_price,
            )
            _print(tier.model_dump())
        return

    if args.command == "restock":
        with Session(engine) as session:
            stock_row = restock_product(session, product_ref=args.product, quantity=args.qty)
            _print(stock_row.model_dump())
        return

    if args.command == "day":
        with Session(engine) as session:
            if args.day_command == "advance":
                _print(advance_day(session))
                return
            if args.day_command == "current":
                _print({"current_day": get_current_day(session)})
                return

    if args.command == "export":
        with Session(engine) as session:
            data = export_state(session)
        output = Path(args.output)
        output.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Exported state to {output}")
        return

    if args.command == "import":
        payload = json.loads(Path(args.input_path).read_text(encoding="utf-8"))
        with Session(engine) as session:
            _print(import_state(session, payload))
        return

    if args.command == "serve":
        uvicorn.run("src.main:app", host="0.0.0.0", port=args.port, reload=True)
        return


if __name__ == "__main__":
    main()

