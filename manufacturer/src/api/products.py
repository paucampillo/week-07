"""
Products API router — CRUD operations for products and BOM management.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from src.models.entities import (
    Product, ProductCreate, ProductRead,
    BOM, BOMCreate, BOMRead,
)
from src.services.database import get_session

router = APIRouter(prefix="/products", tags=["products"])


# ── Products CRUD ──────────────────────────────────────────────────────────

@router.get("/", response_model=List[ProductRead])
async def list_products(
    type: Optional[str] = Query(None, description="Filter by 'raw' or 'finished'"),
    session: Session = Depends(get_session)
):
    """List all products, optionally filtered by type."""
    query = select(Product)
    if type:
        query = query.where(Product.type == type)
    return session.exec(query).all()


@router.post("/", response_model=ProductRead, status_code=201)
async def create_product(
    product: ProductCreate,
    session: Session = Depends(get_session)
):
    """Create a new product (raw material or finished good)."""
    # Check uniqueness
    existing = session.exec(
        select(Product).where(Product.name == product.name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Product '{product.name}' already exists")

    db_product = Product.model_validate(product)
    session.add(db_product)
    session.commit()
    session.refresh(db_product)
    return db_product


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(product_id: int, session: Session = Depends(get_session)):
    """Get a single product by ID."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    return product


@router.delete("/{product_id}")
async def delete_product(product_id: int, session: Session = Depends(get_session)):
    """Delete a product by ID."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    session.delete(product)
    session.commit()
    return {"success": True, "deleted_id": product_id}


# ── BOM Management ─────────────────────────────────────────────────────────

@router.get("/{product_id}/bom", response_model=List[BOMRead])
async def get_bom(product_id: int, session: Session = Depends(get_session)):
    """Get the Bill of Materials for a product."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")

    entries = session.exec(
        select(BOM).where(BOM.finished_product_id == product_id)
    ).all()

    result = []
    for entry in entries:
        material = session.get(Product, entry.material_id)
        result.append(BOMRead(
            id=entry.id,
            finished_product_id=entry.finished_product_id,
            material_id=entry.material_id,
            quantity=entry.quantity,
            material_name=material.name if material else None
        ))
    return result


@router.post("/{product_id}/bom", response_model=BOMRead, status_code=201)
async def add_bom_entry(
    product_id: int,
    entry: BOMCreate,
    session: Session = Depends(get_session)
):
    """Add a component to a product's BOM."""
    product = session.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    if product.type != "finished":
        raise HTTPException(status_code=422, detail="BOM entries only for finished products")

    material = session.get(Product, entry.material_id)
    if not material:
        raise HTTPException(status_code=404, detail=f"Material {entry.material_id} not found")

    # Check for duplicate
    existing = session.exec(
        select(BOM).where(
            BOM.finished_product_id == product_id,
            BOM.material_id == entry.material_id
        )
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="BOM entry already exists for this material")

    bom = BOM(
        finished_product_id=product_id,
        material_id=entry.material_id,
        quantity=entry.quantity
    )
    session.add(bom)
    session.commit()
    session.refresh(bom)
    return BOMRead(
        id=bom.id,
        finished_product_id=bom.finished_product_id,
        material_id=bom.material_id,
        quantity=bom.quantity,
        material_name=material.name
    )


@router.delete("/bom/{bom_id}")
async def delete_bom_entry(bom_id: int, session: Session = Depends(get_session)):
    """Remove a BOM entry."""
    bom = session.get(BOM, bom_id)
    if not bom:
        raise HTTPException(status_code=404, detail=f"BOM entry {bom_id} not found")
    session.delete(bom)
    session.commit()
    return {"success": True, "deleted_id": bom_id}
