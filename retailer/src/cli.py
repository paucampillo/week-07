"""Argparse CLI for retailer operations."""

import argparse
import json

import uvicorn
from sqlmodel import Session, select

from src.db import engine, init_db, load_seed_data
from src.models import CustomerOrder, PurchaseOrder, RetailerProduct
from src.services import advance_day, create_customer_order, create_purchase_order, get_state


def _bootstrap() -> None:
    init_db()
    load_seed_data()


def _print(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retailer-cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("catalog")
    sub.add_parser("stock")

    customers = sub.add_parser("customers")
    customers_sub = customers.add_subparsers(dest="customers_command", required=True)
    co_list = customers_sub.add_parser("orders")
    co_list.add_argument("--status", default=None)
    co_get = customers_sub.add_parser("order")
    co_get.add_argument("order_id", type=int)

    sub_fulfill = sub.add_parser("fulfill")
    sub_fulfill.add_argument("order_id", type=int)

    sub_backorder = sub.add_parser("backorder")
    sub_backorder.add_argument("order_id", type=int)

    purchase = sub.add_parser("purchase")
    purchase_sub = purchase.add_subparsers(dest="purchase_command", required=True)
    purchase_sub.add_parser("list")
    pc = purchase_sub.add_parser("create")
    pc.add_argument("model")
    pc.add_argument("qty", type=int)

    price_cmd = sub.add_parser("price")
    price_sub = price_cmd.add_subparsers(dest="price_command", required=True)
    price_sub.add_parser("list")
    ps = price_sub.add_parser("set")
    ps.add_argument("model")
    ps.add_argument("price", type=float)

    day = sub.add_parser("day")
    day_sub = day.add_subparsers(dest="day_command", required=True)
    day_sub.add_parser("advance")
    day_sub.add_parser("current")

    sub.add_parser("export")
    imp = sub.add_parser("import")
    imp.add_argument("file")

    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8003)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _bootstrap()

    with Session(engine) as session:
        if args.command == "catalog":
            products = session.exec(select(RetailerProduct)).all()
            _print([p.model_dump() for p in products])
            return

        if args.command == "stock":
            products = session.exec(select(RetailerProduct)).all()
            _print([{"model": p.model, "stock": p.stock} for p in products])
            return

        if args.command == "customers":
            if args.customers_command == "orders":
                q = select(CustomerOrder)
                if args.status:
                    q = q.where(CustomerOrder.status == args.status)
                _print([o.model_dump() for o in session.exec(q.order_by(CustomerOrder.id)).all()])
            elif args.customers_command == "order":
                order = session.get(CustomerOrder, args.order_id)
                if not order:
                    raise SystemExit(f"Order {args.order_id} not found")
                _print(order.model_dump())
            return

        if args.command == "fulfill":
            order = session.get(CustomerOrder, args.order_id)
            if not order:
                raise SystemExit(f"Order {args.order_id} not found")
            product = session.get(RetailerProduct, order.model)
            if not product or product.stock < order.quantity:
                raise SystemExit(f"Not enough stock (have {product.stock if product else 0})")
            product.stock -= order.quantity
            state = get_state(session)
            order.status = "fulfilled"
            order.day_fulfilled = state.current_day
            session.commit()
            _print({"success": True, "order_id": args.order_id})
            return

        if args.command == "backorder":
            order = session.get(CustomerOrder, args.order_id)
            if not order:
                raise SystemExit(f"Order {args.order_id} not found")
            order.status = "backordered"
            session.commit()
            _print({"success": True, "order_id": args.order_id, "status": "backordered"})
            return

        if args.command == "purchase":
            if args.purchase_command == "list":
                _print([po.model_dump() for po in session.exec(select(PurchaseOrder).order_by(PurchaseOrder.id)).all()])
            elif args.purchase_command == "create":
                try:
                    po = create_purchase_order(session, args.model, args.qty)
                    session.commit()
                    session.refresh(po)
                    _print(po.model_dump())
                except ValueError as exc:
                    raise SystemExit(str(exc)) from exc
            return

        if args.command == "price":
            if args.price_command == "list":
                products = session.exec(select(RetailerProduct)).all()
                _print([{"model": p.model, "retail_price": p.retail_price, "wholesale_price": p.wholesale_price} for p in products])
            elif args.price_command == "set":
                p = session.get(RetailerProduct, args.model)
                if not p:
                    raise SystemExit(f"Product '{args.model}' not found")
                min_price = p.wholesale_price * 1.15
                if args.price < min_price:
                    raise SystemExit(f"Price {args.price} below minimum {min_price:.2f}")
                p.retail_price = args.price
                session.commit()
                _print({"success": True, "model": args.model, "new_price": args.price})
            return

        if args.command == "day":
            if args.day_command == "current":
                state = get_state(session)
                _print({"current_day": state.current_day})
            elif args.day_command == "advance":
                _print(advance_day(session))
            return

        if args.command == "export":
            from src.models import RetailerEvent
            state = get_state(session)
            data = {
                "state": state.model_dump(),
                "products": [p.model_dump() for p in session.exec(select(RetailerProduct)).all()],
                "customer_orders": [o.model_dump() for o in session.exec(select(CustomerOrder)).all()],
                "purchase_orders": [po.model_dump() for po in session.exec(select(PurchaseOrder)).all()],
                "events": [e.model_dump() for e in session.exec(select(RetailerEvent)).all()],
            }
            _print(data)
            return

        if args.command == "import":
            import sys
            with open(args.file, "r") as f:
                data = json.load(f)
            from src.db import reset_db
            reset_db()
            with Session(engine) as s2:
                from src.models import RetailerEvent
                from sqlmodel import SQLModel
                from src.db import engine as eng2
                SQLModel.metadata.drop_all(eng2)
                SQLModel.metadata.create_all(eng2)
                s2.merge(RetailerState(**data["state"]))
                for row in data.get("products", []): s2.add(RetailerProduct(**row))
                s2.commit()
                for row in data.get("customer_orders", []): s2.add(CustomerOrder(**row))
                for row in data.get("purchase_orders", []): s2.add(PurchaseOrder(**row))
                for row in data.get("events", []): s2.add(RetailerEvent(**row))
                s2.commit()
            _print({"success": True})
            return

    if args.command == "serve":
        uvicorn.run("src.main:app", host="0.0.0.0", port=args.port, reload=True)
        return


if __name__ == "__main__":
    main()
