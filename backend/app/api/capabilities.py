"""Read-only capability declarations, effective registrations and evidence layers."""
from fastapi import APIRouter, HTTPException

from app.bridge.capability_catalog_service import (
    CapabilityCatalog, CapabilityCatalogUnavailable, capability_catalog,
)

router = APIRouter(prefix="/api/capabilities", tags=["capabilities"])


@router.get("", response_model=CapabilityCatalog)
def get_capabilities() -> CapabilityCatalog:
    try:
        return capability_catalog()
    except CapabilityCatalogUnavailable as exc:
        raise HTTPException(status_code=503, detail={"code": "capability_catalog_unavailable",
            "message": "Capability declarations could not be inspected safely"}) from exc
