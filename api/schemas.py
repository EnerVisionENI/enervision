import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Role = Literal["viewer", "operator", "admin"]

# Aligné sur la contrainte appliquée par api/create_admin.py.
MIN_PASSWORD_LENGTH = 8


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
