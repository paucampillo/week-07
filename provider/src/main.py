"""FastAPI application for the week-06 provider service."""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.api import router as provider_router
from src.db import init_db, load_seed_data

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


@asynccontextmanager
async def lifespan(_: FastAPI):
    os.makedirs("data", exist_ok=True)
    init_db()
    load_seed_data()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Week-06 Provider Service",
        description="Provider API for catalog, stock, purchase orders and day-stepped simulation.",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(provider_router)

    @app.get("/info/health")
    def health():
        return {"status": "healthy", "service": "provider", "environment": ENVIRONMENT}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("src.main:app", host="0.0.0.0", port=8001, reload=True)

