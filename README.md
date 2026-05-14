# Week-07: Supply Chain Part 2 — Retailer, Turn Engine & Claude Agent

Extends week-06 adding a Retailer app, a Turn Engine orchestrator, and a Claude skill file for the manufacturer manager role.

## Architecture

```
Turn Engine
    │
    ├── POST /api/orders ──► Retailer (port 8003)
    │                              │
    │                              └── POST /api/orders ──► Manufacturer (port 8002)
    │                                                              │
    │                                                              └── POST /api/orders ──► Provider (port 8001)
    │
    ├── POST /api/day/advance ──► Provider
    ├── POST /api/day/advance ──► Manufacturer
    └── POST /api/day/advance ──► Retailer
```

## Setup

Install dependencies in each app:
```bash
cd provider    && pip install -r requirements.txt
cd manufacturer && pip install -r requirements.txt
cd retailer    && pip install -r requirements.txt
```

Also install `httpx` in the root environment for the turn engine:
```bash
pip install httpx
```

## Start all apps (three separate terminals)

```bash
# Terminal 1 — Provider (port 8001)
cd provider && python -m uvicorn src.main:app --port 8001

# Terminal 2 — Manufacturer (port 8002)
cd manufacturer && python -m uvicorn src.main:app --port 8002

# Terminal 3 — Retailer (port 8003)
cd retailer && python -m uvicorn src.main:app --port 8003
```

## Run Turn Engine

```bash
# Stub mode — 3 days, all deterministic
python turn_engine.py config/sim.json scenarios/smoke-test.json 3

# Agent mode — manufacturer as Claude agent (1 day)
python turn_engine.py config/sim.json scenarios/smoke-test.json 1 --agent manufacturer
```

Logs are saved to `logs/day-NNN-<role>.log`.

## CLI Quick Reference

### Retailer
```bash
cd retailer
python retailer-cli.py catalog
python retailer-cli.py stock
python retailer-cli.py customers orders
python retailer-cli.py purchase list
python retailer-cli.py day current
python retailer-cli.py day advance
```

### Manufacturer (new commands in week-07)
```bash
cd manufacturer
python manufacturer-cli.py sales orders
python manufacturer-cli.py sales orders --status pending
python manufacturer-cli.py production release <order_id>
python manufacturer-cli.py production status
python manufacturer-cli.py capacity
python manufacturer-cli.py price list
python manufacturer-cli.py price set P3D-Classic 550
```

## New in week-07 (vs week-06)

| Component | What's new |
|-----------|------------|
| `retailer/` | New app — sells printers to end customers, buys from manufacturer |
| `turn_engine.py` | Orchestrates all three apps per-day |
| `skills/manufacturer-manager.md` | Claude skill file for manufacturer agent |
| `manufacturer/src/api/sales.py` | `POST /api/orders` — accepts inbound orders from retailers |
| `manufacturer/src/models/entities.py` | `SalesOrder`, `WholesalePrice` |
| `config/sim.json` | Simulation config with app URLs |
| `scenarios/smoke-test.json` | Baseline demand scenario |
