from datetime import datetime, timedelta, timezone

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc
from sqlalchemy.orm import Session

from api.auth import get_current_user
from api.config import get_settings
from api.database import get_db
from api.models import AggregateGoldDaily, MeasurementSilver, Site, User
from api.schemas import DailySummaryOut, MeasurementOut, SiteCurrentReading, SiteOut

settings = get_settings()

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=list[SiteOut])
def list_sites(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[Site]:
    return db.query(Site).order_by(asc(Site.site_id)).all()


@router.get("/{site_id}/measurements", response_model=list[MeasurementOut])
def list_site_measurements(
    site_id: str,
    depuis_minutes: int = Query(10, ge=1, le=1440),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[MeasurementSilver]:
    """Historique réel récent (table measurements_silver, alimentée par etl/quality.py
    toutes les ~60s) : sert à préremplir le graphique de puissance sans repartir de zéro
    à chaque chargement. Résolution ~1 point/minute, pas aussi dense que le direct."""
    depuis = datetime.now(timezone.utc) - timedelta(minutes=depuis_minutes)
    return (
        db.query(MeasurementSilver)
        .filter(MeasurementSilver.site_id == site_id, MeasurementSilver.timestamp >= depuis)
        .order_by(asc(MeasurementSilver.timestamp))
        .all()
    )


@router.get("/{site_id}/daily-summary", response_model=DailySummaryOut | None)
def get_site_daily_summary(
    site_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> AggregateGoldDaily | None:
    """Résumé du jour (table aggregates_gold_daily, recalculée par etl/quality.py) :
    null si le job gold n'a pas encore tourné pour aujourd'hui sur ce site."""
    aujourdhui = datetime.now(timezone.utc).date()
    return (
        db.query(AggregateGoldDaily)
        .filter(AggregateGoldDaily.site_id == site_id, AggregateGoldDaily.record_date == aujourdhui)
        .first()
    )


@router.get("/{site_id}/current", response_model=SiteCurrentReading)
def get_site_current_reading(
    site_id: str,
    _: User = Depends(get_current_user),
) -> SiteCurrentReading:
    """Relaie la lecture instantanée de l'API Mock IoT (aucun stockage local) :
    le front n'appelle jamais le mock directement, seulement cette API."""
    try:
        reponse = requests.get(f"{settings.api_base}/api/v1/sites/{site_id}/current", timeout=5)
        reponse.raise_for_status()
    except requests.exceptions.RequestException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="API Mock IoT injoignable ou site inconnu",
        ) from exc

    return SiteCurrentReading(**reponse.json())
