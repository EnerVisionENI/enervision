import uuid
from datetime import datetime

from sqlalchemy import Boolean, Numeric, String, Uuid, false, func
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    # Vrai tant que le mot de passe temporaire choisi par l'admin n'a pas été
    # remplacé par l'utilisateur : voir get_active_user dans api/auth.py.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Alert(Base):
    __tablename__ = "alerts"

    # site_id référence sites(site_id) dans infra/postgres/init.sql, non redéclaré ici
    # en clé étrangère : ce modèle est en lecture seule, alimenté par etl/alerts.py.
    alert_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    timestamp: Mapped[datetime | None] = mapped_column(nullable=True)
    site_id: Mapped[str | None] = mapped_column(String(20), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    message: Mapped[str | None] = mapped_column(nullable=True)
    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    threshold: Mapped[float | None] = mapped_column(Numeric, nullable=True)
