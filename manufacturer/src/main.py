"""
3D Printer Production Simulator — Main FastAPI Application

This module initializes the FastAPI application, configures middleware,
and wires up all API routers. Database tables are created on startup
and seed data is loaded if available.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, APIRouter
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.services.database import init_db, load_seed_data
from src.api.products import router as products_router
from src.api.suppliers import router as suppliers_router
from src.api.inventory import router as inventory_router
from src.api.orders import router as orders_router
from src.api.simulation import router as simulation_router
from src.api.sales import router as sales_router


# ============================================================================
# Configuration
# ============================================================================

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")


# ============================================================================
# Pydantic Response Models
# ============================================================================

class HealthResponse(BaseModel):
    """Health check response schema."""
    status: str
    environment: str
    version: str


# ============================================================================
# Application Lifespan
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables and load seed data. Shutdown: cleanup."""
    print(f"Starting 3D Printer Production Simulator ({ENVIRONMENT})")
    os.makedirs("data", exist_ok=True)
    init_db()
    result = load_seed_data()
    print(f"Seed data: {result}")
    yield
    print("Shutting down simulator")


# ============================================================================
# Info Router (health, root)
# ============================================================================

info_router = APIRouter(prefix="/info", tags=["info"])


@info_router.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint for Docker container monitoring."""
    return HealthResponse(
        status="healthy",
        environment=ENVIRONMENT,
        version="1.0.0"
    )


@info_router.get("/")
async def info_root():
    return {"message": "3D Printer Production Simulator API"}

root_router = APIRouter(tags=["ui"])

@root_router.get("/")
async def root():
    """Serve the central dashboard UI."""
    return FileResponse("src/templates/index.html")


# ============================================================================
# FastAPI Application Factory
# ============================================================================

def create_app() -> FastAPI:
    """Factory function to create and configure the FastAPI application."""
    app = FastAPI(
        title="Week-07 Manufacturer Service",
        description=(
            "Day-stepped factory simulation for 3D printer manufacturing. "
            "Manage inventory, purchasing, and production scheduling."
        ),
        version="1.0.0",
        lifespan=lifespan
    )

    # Mount static files correctly
    app.mount("/static", StaticFiles(directory="src/static"), name="static")

    # Register all routers
    app.include_router(root_router)
    app.include_router(info_router)
    app.include_router(simulation_router)
    app.include_router(products_router)
    app.include_router(suppliers_router)
    app.include_router(inventory_router)
    app.include_router(orders_router)
    app.include_router(sales_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.main:app", host="0.0.0.0", port=8002, reload=True)
