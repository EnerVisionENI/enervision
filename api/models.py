import uuid
from datetime import datetime

from sqlalchemy import DateTime, Numeric, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from api.database import Base


class User(Base):
    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
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


class Site(Base):
    __tablename__ = "sites"

    # Modèle en lecture seule, alimenté par etl/sites.py (cf. commentaire sur
    # Alert.site_id : même principe, pas de logique d'écriture ici).
    site_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    site_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    site_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)
    capacity_kw: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)


class MeasurementSilver(Base):
    __tablename__ = "measurements_silver"

    # Modèle en lecture seule, alimenté par etl/quality.py via postgres_writer.py
    # (infra/postgres/init/02_silver.sql) — même principe que Alert/Site. Seules
    # les colonnes utiles à l'API (historique de puissance) sont mappées, pas les
    # 20 colonnes de la table.
    source_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    site_id: Mapped[str] = mapped_column(String(20), nullable=False)
    consumption_kw: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
