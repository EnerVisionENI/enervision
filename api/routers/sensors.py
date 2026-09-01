import os
import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from api.models import User

router = APIRouter(prefix="/api/v1", tags=["Sensors"])

API_MOCK_BASE = os.environ.get("API_MOCK_BASE", "http://10.105.200.45:8000")


@router.get("/sensors/status")
async def get_sensors_status(current_user: User = Depends(get_current_user)):
    """
    Relaie GET /api/v1/sensors/status de l'API Mock.
    Ne stocke rien, retourne directement la réponse en direct.
    Nécessite un utilisateur authentifié, n'importe quel rôle.
    """
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            reponse = await client.get(f"{API_MOCK_BASE}/api/v1/sensors/status")
            reponse.raise_for_status()
        except httpx.RequestError as erreur:
            raise HTTPException(
                status_code=502,
                detail=f"Impossible de contacter l'API Mock : {erreur}",
            )
        except httpx.HTTPStatusError as erreur:
            raise HTTPException(
                status_code=erreur.response.status_code,
                detail="Erreur retournée par l'API Mock",
            )

    return reponse.json()