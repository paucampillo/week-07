"""Suppliers API router for local and remote suppliers."""

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from src.models.entities import Product, Supplier, SupplierCreate, SupplierRead
from src.services.database import get_session
from src.services.external_procurement import fetch_provider_catalog, get_provider_configs
from src.services.provider_client import ProviderClientError

router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("/", response_model=List[SupplierRead])
async def list_suppliers(
    active_only: bool = True,
    session: Session = Depends(get_session),
):
    query = select(Supplier)
    if active_only:
        query = query.where(Supplier.active.is_(True))
    return session.exec(query).all()


@router.get("/providers")
async def list_provider_configs():
    """Configured remote providers (name + URL)."""
    return [{"name": cfg.name, "url": cfg.url} for cfg in get_provider_configs()]


@router.get("/providers/{provider_name}/catalog")
async def provider_catalog(provider_name: str):
    """Read catalog from remote provider."""
    try:
        return fetch_provider_catalog(provider_name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ProviderClientError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/", response_model=SupplierRead, status_code=201)
async def create_supplier(
    supplier: SupplierCreate,
    session: Session = Depends(get_session),
):
    product = session.get(Product, supplier.product_id)
    if not product:
        raise HTTPException(status_code=404, detail=f"Product {supplier.product_id} not found")

    db_supplier = Supplier.model_validate(supplier)
    session.add(db_supplier)
    session.commit()
    session.refresh(db_supplier)
    return db_supplier


@router.get("/{supplier_id}", response_model=SupplierRead)
async def get_supplier(supplier_id: int, session: Session = Depends(get_session)):
    supplier = session.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    return supplier


@router.put("/{supplier_id}", response_model=SupplierRead)
async def update_supplier(
    supplier_id: int,
    data: SupplierCreate,
    session: Session = Depends(get_session),
):
    supplier = session.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")

    for key, value in data.model_dump().items():
        setattr(supplier, key, value)

    session.commit()
    session.refresh(supplier)
    return supplier


@router.delete("/{supplier_id}")
async def delete_supplier(supplier_id: int, session: Session = Depends(get_session)):
    supplier = session.get(Supplier, supplier_id)
    if not supplier:
        raise HTTPException(status_code=404, detail=f"Supplier {supplier_id} not found")
    supplier.active = False
    session.commit()
    return {"success": True, "deactivated_id": supplier_id}

