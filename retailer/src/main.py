"""Retailer FastAPI application — port 8003."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.db import init_db, load_seed_data
from src.api import router as api_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("data", exist_ok=True)
    init_db()
    result = load_seed_data()
    print(f"Retailer seed: {result}")
    yield
    print("Retailer shutting down")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Week-07 Retailer Service",
        description="Retailer app: sells 3D printers to end customers, buys from manufacturer.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8003, reload=True)
