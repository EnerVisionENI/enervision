from datetime import UTC, datetime, timedelta

import requests
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import asc
from sqlalchemy.orm import Session

from api.auth import get_active_user
from api.config import get_settings
from api.database import get_db
from api.models import AggregateGoldDaily, MeasurementSilver, PredictionForecast, Site, User
from api.schemas import (
    DailySummaryOut,
    MeasurementOut,
    PredictionOut,
    SiteCurrentReading,
    SiteOut,
)

settings = get_settings()

router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("", response_model=list[SiteOut])
def list_sites(
    db: Session = Depends(get_db),
    _: User = Depends(get_active_user),
) -> list[Site]:
    return db.query(Site).order_by(asc(Site.site_id)).all()


@router.get("/{site_id}/measurements", response_model=list[MeasurementOut])
def list_site_measurements(
    site_id: str,
    depuis_minutes: int = Query(10, ge=1, le=10080),
    pas_minutes: int = Query(1, ge=1, le=1440),
    db: Session = Depends(get_db),
    _: User = Depends(get_active_user),
) -> list[MeasurementSilver]:
    """Historique réel (table measurements_silver, alimentée par etl/quality.py toutes les
    ~60s) : sert à tenir le graphique de puissance à jour sans repartir de zéro à chaque
    chargement. Résolution ~1 point/minute, pas aussi dense que le direct.

    `depuis_minutes` remonte jusqu'à 7 jours, la même profondeur que `historique_heures` sur
    /predictions : les deux courbes doivent pouvoir couvrir la même période, sinon comparer
    prévu et réalisé s'arrête au bord du plus court des deux.

    `pas_minutes` éclaircit la réponse à une lecture par tranche : 7 jours à la minute font
    ~10 000 lignes par site, que le front demande pour les sept sites à chaque cycle de
    sondage. À 1 (le défaut), rien n'est éclairci et la réponse est celle d'avant."""
    depuis = datetime.now(UTC) - timedelta(minutes=depuis_minutes)
    lignes = (
        db.query(MeasurementSilver)
        .filter(MeasurementSilver.site_id == site_id, MeasurementSilver.timestamp >= depuis)
        .order_by(asc(MeasurementSilver.timestamp))
        .all()
    )
    return eclaircir(lignes, pas_minutes)


def eclaircir(lignes: list[MeasurementSilver], pas_minutes: int) -> list[MeasurementSilver]:
    """Ne garde qu'une lecture par tranche de `pas_minutes`, en partant de la plus récente.

    Éclaircir par sélection et non par moyenne : chaque point renvoyé reste une lecture
    réellement écrite par la collecte, avec son horodatage, son score de qualité et ses
    raisons de NULL. Une moyenne par tranche inventerait des valeurs que la base ne contient
    pas, et lisserait justement les pointes que le graphique sert à repérer.

    Le parcours part de la fin pour que la dernière lecture soit toujours renvoyée : c'est
    celle que le front affiche comme lecture courante et l'ancre de son « il y a Xs ». En
    partant du début, elle serait écartée dès qu'elle tombe dans la tranche de la précédente
    — jusqu'à 7 minutes de retard affichées sur une fenêtre de 7 jours.

    Les horodatages ne sont comparés qu'entre eux (jamais à `datetime.now`) : la fonction ne
    dépend donc pas de la présence d'un fuseau sur la colonne, qui diffère entre Postgres et
    le SQLite des tests."""
    if pas_minutes <= 1:
        return lignes

    pas = timedelta(minutes=pas_minutes)
    retenues: list[MeasurementSilver] = []
    for ligne in reversed(lignes):
        if not retenues or (retenues[-1].timestamp - ligne.timestamp) >= pas:
            retenues.append(ligne)
    retenues.reverse()
    return retenues


@router.get("/{site_id}/daily-summary", response_model=DailySummaryOut | None)
def get_site_daily_summary(
    site_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_active_user),
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
    _: User = Depends(get_active_user),
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
