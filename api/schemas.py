import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Role = Literal["viewer", "operator", "admin"]


def _en_utc(valeur: datetime | None) -> datetime | None:
    """La base (colonnes TIMESTAMP sans fuseau) et l'API Mock IoT renvoient des
    datetimes naïves mais réellement en UTC. Leur associer explicitement UTC
    avant sérialisation JSON, sinon `new Date(...)` côté front les interprète
    comme heure locale (décalage silencieux de l'écart UTC de l'utilisateur)."""
    if valeur is not None and valeur.tzinfo is None:
        return valeur.replace(tzinfo=timezone.utc)
    return valeur


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    email: EmailStr
    role: Role
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _valider_created_at(cls, valeur: datetime) -> datetime:
        return _en_utc(valeur)


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    role: Role = "viewer"


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
    data_quality: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _valider_timestamp(cls, valeur: datetime) -> datetime:
        return _en_utc(valeur)
