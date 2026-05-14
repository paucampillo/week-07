# Week-07: Supply Chain Part 2

Three-app supply chain simulation with Turn Engine and Claude agent.

## Apps
- **Provider** (port 8001): supplies raw materials to manufacturer
- **Manufacturer** (port 8002): assembles printers, sells to retailer
- **Retailer** (port 8003): sells to end customers

## Start all apps (separate terminals)
```
cd provider && python -m uvicorn src.main:app --port 8001
cd manufacturer && python -m uvicorn src.main:app --port 8002
cd retailer && python -m uvicorn src.main:app --port 8003
```

## Run turn engine
```
# Stub mode (3 days)
python turn_engine.py config/sim.json scenarios/smoke-test.json 3

# Agent mode (manufacturer as Claude)
python turn_engine.py config/sim.json scenarios/smoke-test.json 1 --agent manufacturer
```

## Key new files (vs week-06)
- `retailer/` — new app
- `turn_engine.py` — orchestrator
- `skills/manufacturer-manager.md` — Claude skill file
- `manufacturer/src/api/sales.py` — inbound orders from retailer
- `manufacturer/src/models/entities.py` — SalesOrder, WholesalePrice

## New manufacturer endpoints
- `POST /api/orders` — retailer places order
- `GET /api/orders` — list sales orders
- `GET /api/orders/{id}` — order detail
- `POST /api/orders/{id}/release` — release to production
- `GET /api/price-list` — wholesale prices
- `PUT /api/price-list/{model}` — update price
- `GET /api/day/current` — current day
- `POST /api/day/advance` — advance day (turn engine uses this)
