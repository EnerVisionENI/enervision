import os

import httpx
from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_active_user
from api.models import User

router = APIRouter(prefix="/sensors", tags=["Sensors"])

API_MOCK_BASE = os.environ.get("API_MOCK_BASE", "http://10.105.200.45:8000")


@router.get("/failing")
async def get_all_sites_failing_sensors(current_user: User = Depends(get_active_user)):
    """
    Récupère l'état de tous les capteurs de tous les sites depuis l'API Mock,
    retourne pour chaque site uniquement ses capteurs en panne.
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

    donnees = reponse.json()

    resultat = {}
    for site_id, site in donnees.items():
        capteurs = site["sensors"]
        capteurs_en_panne = [
            {"capteur": nom, "failing_until": info["failing_until"]}
            for nom, info in capteurs.items()
            if info["status"] == "failing"
        ]
        resultat[site_id] = {
            "site_name": site["site_name"],
            "capteurs_en_panne": capteurs_en_panne,
        }

    return resultat