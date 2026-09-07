import uuid
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["viewer", "operator", "admin"]

# Aligné sur la contrainte appliquée par api/create_admin.py.
MIN_PASSWORD_LENGTH = 8


def _en_utc(valeur: datetime | None) -> datetime | None:
    """La base (colonnes TIMESTAMP sans fuseau) et l'API Mock IoT renvoient des
    datetimes naïves mais réellement en UTC. Leur associer explicitement UTC
    avant sérialisation JSON, sinon `new Date(...)` côté front les interprète
    comme heure locale (décalage silencieux de l'écart UTC de l'utilisateur)."""
    if valeur is not None and valeur.tzinfo is None:
        return valeur.replace(tzinfo=UTC)
    return valeur


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: EmailStr
    role: Role
    must_change_password: bool
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _valider_created_at(cls, valeur: datetime) -> datetime:
        return _en_utc(valeur)


class UserCreate(BaseModel):
    """Création par un admin : le mot de passe fourni est temporaire, l'utilisateur
    devra le remplacer à sa première connexion."""

    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    role: Role = "viewer"


class UserUpdate(BaseModel):
    role: Role


class PasswordReset(BaseModel):
    """Réinitialisation par un admin : redonne un mot de passe temporaire."""

    password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class PasswordChange(BaseModel):
    """Changement par l'utilisateur lui-même, mot de passe actuel exigé."""

    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_id: str
    timestamp: datetime | None = None
    site_id: str | None = None
    severity: str | None = None
    type: str | None = None
    message: str | None = None
    value: float | None = None
    threshold: float | None = None

    @field_validator("timestamp")
    @classmethod
    def _valider_timestamp(cls, valeur: datetime | None) -> datetime | None:
        return _en_utc(valeur)


class SiteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    site_id: str
    site_type: str | None = None
    site_name: str | None = None
    location: str | None = None
    capacity_kw: float | None = None
    status: str | None = None


class SiteCurrentReading(BaseModel):
    site_id: str
    timestamp: datetime
    site_type: str | None = None
    consumption_kw: float | None = None
    consumption_kwh: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_factor: float | None = None
    temperature_celsius: float | None = None
    humidity_percent: float | None = None
    null_reasons: list[str] = []
    data_quality: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _valider_timestamp(cls, valeur: datetime) -> datetime:
        return _en_utc(valeur)


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    timestamp: datetime
    consumption_kw: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_factor: float | None = None
    temperature_celsius: float | None = None
    humidity_percent: float | None = None
    quality_score: int | None = None
    data_quality: str | None = None
    null_reasons: list[str] = []

    @field_validator("timestamp")
    @classmethod
    def _valider_timestamp(cls, valeur: datetime) -> datetime:
        return _en_utc(valeur)


class DailySummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    record_date: date
    records_count: int
    good_count: int
    avg_consumption_kw: float | None = None
    max_consumption_kw: float | None = None
    total_consumption_kwh: float | None = None
    avg_quality_score: float | None = None


class PredictionOut(BaseModel):
    """Une heure prédite pour un site. `predicted_kwh` est encadré par [lower_90, upper_90],
    intervalle conforme à 90 % calibré sur un jeu jamais vu à l'entraînement — les bornes sont
    NULL quand le run MLflow ne porte pas la métrique, jamais égales à la valeur centrale (ce
    qui se lirait comme un intervalle de largeur nulle).

    `data_source` remonte jusqu'au front à dessein : les modèles V1 sont entraînés sur CSV
    synthétique ('csv_synthetic') et l'écran doit le dire, sans quoi ces courbes passent pour
    des prévisions validées sur donnée réelle."""

    # protected_namespaces=() : Pydantic v2 réserve le préfixe `model_` et avertit sur
    # model_version / model_stage. Ces deux noms sont ceux des colonnes SQL et du vocabulaire
    # MLflow — les renommer côté API pour contourner un avertissement ferait diverger le
    # contrat du schéma qu'il expose.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    target_ts: datetime
    step_minutes: int
    predicted_kwh: float
    lower_90: float | None = None
    upper_90: float | None = None
    temperature_celsius: float | None = None
    model_version: str
    model_stage: str | None = None
    champion: str | None = None
    data_source: str | None = None
    predicted_at: datetime

    @field_validator("target_ts", "predicted_at")
    @classmethod
    def _valider_horodatages(cls, valeur: datetime) -> datetime:
        return _en_utc(valeur)
