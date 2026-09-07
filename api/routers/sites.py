from datetime import UTC, datetime, timedelta

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc
from sqlalchemy.orm import Session

from api.auth import get_active_user, get_current_user
from api.config import get_settings
from api.database import get_db
from api.models import AggregateGoldDaily, MeasurementSilver, PredictionForecast, Site, User
from api.recommendations import analyser_depassements
from api.schemas import (
    DailySummaryOut,
    MeasurementOut,
    PredictionOut,
    RecommandationOut,
    SiteCurrentReading,
    SiteOut,
)

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
    depuis = datetime.now(UTC) - timedelta(minutes=depuis_minutes)
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
    aujourdhui = datetime.now(UTC).date()
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


@router.get("/{site_id}/predictions", response_model=list[PredictionOut])
def list_site_predictions(
    site_id: str,
    horizon_heures: int = Query(24, ge=1, le=168),
    historique_heures: int = Query(0, ge=0, le=168),
    db: Session = Depends(get_db),
    _: User = Depends(get_active_user),
) -> list[PredictionForecast]:
    """Prévision de consommation (table predictions_forecast, réécrite à chaque cycle de
    ml/predict.py) : une ligne par heure, encadrée par son intervalle à 90 %.

    Renvoie une liste vide plutôt qu'un 404 quand le site n'a pas de prévision. Trois des sept
    sites ont un champion LightGBM qui réclame `solar_irradiance_wm2`, absente du gold : ils ne
    sont pas inférables aujourd'hui et le resteront tant que cette variable n'est pas collectée.
    C'est un état normal du système, pas une ressource manquante — et un site inconnu donne le
    même résultat qu'un site sans modèle, ce que le front traite pareillement (aucune courbe).

    `historique_heures` remonte dans le passé, et vaut 0 par défaut — l'appelant qui ne demande
    rien continue de ne recevoir que le futur. Les heures passées ne sont pas des prévisions
    périmées : chaque cycle réécrit uniquement le futur et laisse le passé intact, si bien
    qu'une ligne passée reste la prévision réellement émise pour cette heure-là, avec la version
    de modèle qui l'a produite. C'est ce qui permet de superposer prévu et réalisé sur le même
    graphique (voir infra/postgres/init/06_predictions.sql).

    Les deux bornes sont comptées depuis maintenant et non depuis les extrêmes de la table :
    une fenêtre ancrée sur l'heure courante est la seule qui garde le même sens d'un appel au
    suivant, quel que soit l'état d'avancement du dernier cycle d'inférence."""
    maintenant = datetime.now(UTC)
    depuis = maintenant - timedelta(hours=historique_heures)
    jusqua = maintenant + timedelta(hours=horizon_heures)
    return (
        db.query(PredictionForecast)
        .filter(
            PredictionForecast.site_id == site_id,
            PredictionForecast.target_ts >= depuis,
            PredictionForecast.target_ts < jusqua,
        )
        .order_by(asc(PredictionForecast.target_ts))
        .all()
    )


@router.get("/{site_id}/recommendations", response_model=list[RecommandationOut])
def list_site_recommendations(
    site_id: str,
    horizon_heures: int = Query(24, ge=1, le=168),
    db: Session = Depends(get_db),
    _: User = Depends(get_active_user),
) -> list:
    """Ajustements suggérés quand la prévision franchit la puissance souscrite du site.

    Recalculé à chaque appel plutôt que stocké : une recommandation est entièrement déterminée
    par predictions_forecast et sites.capacity_kw, et le futur est réécrit à chaque cycle
    d'inférence. La persister imposerait de la réécrire aussi, avec le risque qu'elle survive
    à la prévision qui l'a produite.

    Seul le futur est examiné : recommander un ajustement sur une heure écoulée n'aurait aucun
    effet. La comparaison du prévu au réalisé, elle, se lit sur le graphique (voir
    l'endpoint predictions et son paramètre historique_heures).

    Liste vide quand il n'y a rien à dire — site sans modèle inférable, sans puissance
    souscrite renseignée, ou dont la prévision tient sous le seuil. C'est un résultat, pas une
    absence de donnée, et le front l'affiche comme tel.

    Ces recommandations héritent des limites du modèle qui les produit : les modèles V1 sont
    entraînés sur CSV synthétique. `data_source` reste porté par l'endpoint predictions, que
    le dashboard affiche déjà — d'où l'avertissement à l'écran."""
    maintenant = datetime.now(UTC)
    site = db.get(Site, site_id)
    lignes = (
        db.query(PredictionForecast)
        .filter(
            PredictionForecast.site_id == site_id,
            PredictionForecast.target_ts >= maintenant,
            PredictionForecast.target_ts < maintenant + timedelta(hours=horizon_heures),
        )
        .order_by(asc(PredictionForecast.target_ts))
        .all()
    )
    return analyser_depassements(lignes, None if site is None else site.capacity_kw)
