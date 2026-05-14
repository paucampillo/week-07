"""
Turn Engine — orchestrates one simulated day across all three apps.

Usage:
    python turn_engine.py config/sim.json scenarios/smoke-test.json <num_days> [--agent manufacturer]

Order of operations per turn (downstream → upstream):
1. Read today's market signals from scenario
2. Generate customer demand → POST to retailer
3. Run retailer stub/agent
4. Run manufacturer stub/agent
5. Run provider stub (no skill file yet)
6. POST /api/day/advance to ALL apps
"""

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

import httpx


# ── Config & Scenario Helpers ──────────────────────────────────────────────

def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def load_scenario(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def todays_signal(day: int, scenario: dict) -> dict:
    base = scenario.get("base_demand", {"mean": 3, "variance": 1})
    events = scenario.get("events", [])
    for event in events:
        if event.get("start_day", 0) <= day <= event.get("end_day", 9999):
            return {
                "day": day,
                "base_demand": base,
                "demand_modifier": event.get("demand_modifier", 1.0),
                "event_name": event.get("name", "normal"),
                "description": event.get("description", ""),
            }
    return {"day": day, "base_demand": base, "demand_modifier": 1.0, "event_name": "default"}


# ── Customer Demand ────────────────────────────────────────────────────────

def get_retailer_prices(retailer_url: str) -> dict:
    """Fetch current retail prices from retailer."""
    try:
        resp = httpx.get(f"{retailer_url}/api/catalog", timeout=5)
        resp.raise_for_status()
        catalog = resp.json()
        return {p["model"]: p["retail_price"] for p in catalog}
    except Exception:
        return {"P3D-Classic": 650.0, "P3D-Pro": 1040.0}


def generate_customer_orders(retailer_url: str, signal: dict) -> list:
    """Generate deterministic customer demand based on scenario signal."""
    base = signal["base_demand"]
    modifier = signal.get("demand_modifier", 1.0)
    base_prices = {"P3D-Classic": 650.0, "P3D-Pro": 1040.0}
    current_prices = get_retailer_prices(retailer_url)

    orders = []
    for model, base_price in base_prices.items():
        price = current_prices.get(model, base_price)
        price_factor = max(0.2, 1.0 - (price - base_price) / base_price * 0.5)
        adjusted_mean = base["mean"] * modifier * price_factor
        n = max(0, int(random.gauss(adjusted_mean, base.get("variance", 1))))
        for _ in range(n):
            orders.append({"model": model, "quantity": 1, "customer_name": f"customer_{signal['day']}"})

    return orders


def inject_customer_orders(retailer_url: str, orders: list) -> list:
    """POST each customer order to the retailer."""
    results = []
    for order in orders:
        try:
            resp = httpx.post(f"{retailer_url}/api/orders", json=order, timeout=5)
            results.append({"model": order["model"], "status": resp.status_code})
        except Exception as exc:
            results.append({"model": order["model"], "error": str(exc)})
    return results


# ── Retailer Stub ──────────────────────────────────────────────────────────

def run_retailer_stub(retailer_url: str, manufacturer_url: str, context: dict) -> str:
    """Stub: retailer buys from manufacturer if stock is low."""
    log_lines = ["[Retailer Stub] Running daily decisions..."]
    try:
        stock_resp = httpx.get(f"{retailer_url}/api/stock", timeout=5)
        stocks = {s["model"]: s["stock"] for s in stock_resp.json()}

        backordered_resp = httpx.get(f"{retailer_url}/api/orders?status=backordered", timeout=5)
        backordered = backordered_resp.json()
        model_demand = {}
        for o in backordered:
            model_demand[o["model"]] = model_demand.get(o["model"], 0) + o["quantity"]

        for model, stock in stocks.items():
            demand = model_demand.get(model, 0)
            if demand > 0 or stock < 2:
                qty = max(demand, 3)
                try:
                    resp = httpx.post(
                        f"{retailer_url}/api/purchases",
                        json={"model": model, "quantity": qty},
                        timeout=10,
                    )
                    if resp.status_code == 201:
                        log_lines.append(f"  Ordered {qty}x {model} from manufacturer")
                    else:
                        log_lines.append(f"  Failed to order {model}: {resp.status_code} {resp.text[:100]}")
                except Exception as exc:
                    log_lines.append(f"  Error ordering {model}: {exc}")
    except Exception as exc:
        log_lines.append(f"  Error: {exc}")

    output = "\n".join(log_lines)
    return output


# ── Agent Runner ───────────────────────────────────────────────────────────

def run_agent(role: str, skill_path: str, context: dict, cwd: str, day: int) -> str:
    """Run Claude as an agent using the skill file."""
    prompt = f"""Read the skill file at {skill_path}.

Today's context:
{json.dumps(context, indent=2)}

Execute your daily decisions following the skill's decision framework.
Do NOT advance the day — the turn engine does that.
"""
    print(f"  [Agent] Running claude for {role} (day {day})...")
    try:
        result = subprocess.run(
            ["claude", "--print", "--allowedTools", "Bash"],
            input=prompt,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=180,
        )
        output = result.stdout
        if result.returncode != 0:
            output += f"\n[stderr]: {result.stderr[:500]}"
    except FileNotFoundError:
        output = f"[Agent] claude CLI not found — falling back to stub mode for {role}"
    except subprocess.TimeoutExpired:
        output = f"[Agent] Timeout after 180s for {role}"
    except Exception as exc:
        output = f"[Agent] Error: {exc}"

    return output


# ── Manufacturer Stub ──────────────────────────────────────────────────────

def run_manufacturer_stub(manufacturer_url: str, context: dict) -> str:
    """Stub: release all pending sales orders if materials available."""
    log_lines = ["[Manufacturer Stub] Running daily decisions..."]
    try:
        orders_resp = httpx.get(f"{manufacturer_url}/api/orders?status=pending", timeout=5)
        pending_orders = orders_resp.json()
        log_lines.append(f"  Pending sales orders: {len(pending_orders)}")
        for order in pending_orders:
            resp = httpx.post(
                f"{manufacturer_url}/api/orders/{order['id']}/release",
                timeout=5,
            )
            if resp.status_code == 200:
                log_lines.append(f"  Released order {order['id']}: {order['model']} x{order['quantity']}")
            else:
                log_lines.append(f"  Could not release {order['id']}: {resp.status_code} {resp.text[:100]}")
    except Exception as exc:
        log_lines.append(f"  Error: {exc}")
    return "\n".join(log_lines)


# ── Provider Stub ──────────────────────────────────────────────────────────

def run_provider_stub(provider_url: str, context: dict) -> str:
    """Provider stub: no active decisions needed (day advance handles deliveries)."""
    return "[Provider Stub] No decisions needed today."


# ── Advance All ───────────────────────────────────────────────────────────

def advance_all(config: dict) -> dict:
    """POST /api/day/advance to provider, manufacturer, retailer."""
    results = {}
    # Provider must advance first (manufacturer polls it)
    for role in ("provider", "manufacturer", "retailer"):
        url = config[role]["url"]
        try:
            resp = httpx.post(f"{url}/api/day/advance", timeout=15)
            body = resp.json()
            day_val = body.get("new_day") or body.get("current_day", "?")
            results[role] = {"status": resp.status_code, "day": day_val}
        except Exception as exc:
            results[role] = {"error": str(exc)}
    return results


# ── Single Turn ────────────────────────────────────────────────────────────

def run_day(day: int, config: dict, scenario: dict, agent_roles: set) -> None:
    signal = todays_signal(day, scenario)
    retailer_url = config["retailer"]["url"]
    manufacturer_url = config["manufacturer"]["url"]
    provider_url = config["provider"]["url"]

    print(f"\n{'='*60}")
    print(f"DAY {day} — {signal['event_name']} (modifier={signal['demand_modifier']})")
    print(f"{'='*60}")

    # Step 1: Generate and inject customer demand
    orders = generate_customer_orders(retailer_url, signal)
    print(f"[Demand] Generating {len(orders)} customer orders...")
    results = inject_customer_orders(retailer_url, orders)
    fulfilled = sum(1 for r in results if r.get("status") == 201)
    print(f"[Demand] Injected: {fulfilled}/{len(orders)} accepted")

    # Build context for agents
    context = {
        "day": day,
        "signal": signal,
        "manufacturer_url": manufacturer_url,
        "provider_url": provider_url,
    }

    # Step 2: Retailer turn
    print("\n[Retailer] Running...")
    if "retailer" in agent_roles:
        skill = str(Path(__file__).parent / "skills" / "retailer-manager.md")
        output = run_agent("retailer", skill, context, str(Path(__file__).parent / "retailer"), day)
    else:
        output = run_retailer_stub(retailer_url, manufacturer_url, context)
    print(output)
    _save_log(day, "retailer", output)

    # Step 3: Manufacturer turn
    print("\n[Manufacturer] Running...")
    if "manufacturer" in agent_roles:
        skill = str(Path(__file__).parent / "skills" / "manufacturer-manager.md")
        output = run_agent("manufacturer", skill, context, str(Path(__file__).parent / "manufacturer"), day)
    else:
        output = run_manufacturer_stub(manufacturer_url, context)
    print(output)
    _save_log(day, "manufacturer", output)

    # Step 4: Provider turn (stub only for now)
    print("\n[Provider] Running...")
    output = run_provider_stub(provider_url, context)
    print(output)
    _save_log(day, "provider", output)

    # Step 5: Advance all days
    print("\n[Advance] Advancing all apps...")
    advance_results = advance_all(config)
    for role, result in advance_results.items():
        if "error" in result:
            print(f"  {role}: ERROR — {result['error']}")
        else:
            print(f"  {role}: day {result.get('day', '?')} OK")

    print(f"\n[Done] Day {day} complete.")


def _save_log(day: int, role: str, content: str) -> None:
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)
    log_file = logs_dir / f"day-{day:03d}-{role}.log"
    log_file.write_text(content, encoding="utf-8")


# ── Health Check ───────────────────────────────────────────────────────────

def check_health(config: dict) -> bool:
    """Verify all three apps are reachable before starting."""
    all_ok = True
    for role in ("provider", "manufacturer", "retailer"):
        url = config[role]["url"]
        try:
            resp = httpx.get(f"{url}/api/day/current", timeout=5)
            day = resp.json().get("current_day", "?")
            print(f"  {role} ({url}): OK - day {day}")
        except Exception as exc:
            print(f"  {role} ({url}): UNREACHABLE — {exc}")
            all_ok = False
    return all_ok


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Turn Engine — orchestrates supply chain simulation")
    parser.add_argument("config", help="Path to config/sim.json")
    parser.add_argument("scenario", help="Path to scenarios/smoke-test.json")
    parser.add_argument("days", type=int, help="Number of days to simulate")
    parser.add_argument("--agent", nargs="*", default=[], help="Roles to run as Claude agents (e.g. --agent manufacturer)")
    args = parser.parse_args()

    config = load_config(args.config)
    scenario = load_scenario(args.scenario)
    agent_roles = set(args.agent or [])

    print(f"\nTurn Engine — {scenario['scenario_name']}")
    print(f"Simulating {args.days} day(s)")
    if agent_roles:
        print(f"Agent roles: {', '.join(agent_roles)}")
    else:
        print("Mode: stub (deterministic)")

    print("\nChecking app health...")
    if not check_health(config):
        print("\nERROR: Some apps are not reachable. Start all apps first.")
        sys.exit(1)

    # Determine starting day from provider
    try:
        resp = httpx.get(f"{config['provider']['url']}/api/day/current", timeout=5)
        start_day = resp.json().get("current_day", 1)
    except Exception:
        start_day = 1

    print(f"\nStarting from day {start_day}")

    for i in range(args.days):
        day = start_day + i
        run_day(day, config, scenario, agent_roles)

    print(f"\n{'='*60}")
    print(f"Simulation complete: {args.days} day(s) simulated.")
    print(f"Logs saved to: {Path(__file__).parent / 'logs'}")


if __name__ == "__main__":
    main()
