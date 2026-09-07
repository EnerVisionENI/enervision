import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Boolean, DateTime, Numeric, String, Uuid, false, func
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


class Site(Base):
    __tablename__ = "sites"

    # Modèle en lecture seule, peuplé par infra/postgres/init/04_seed_sites.sql (cf.
    # commentaire sur Alert.site_id : même principe, pas de logique d'écriture ici).
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
    # les colonnes utiles au dashboard (multi-métriques) sont mappées : pas
    # consumption_kwh (toujours identique à consumption_kw dans ce mock),
    # has_anomaly (jamais vrai sur les données collectées) ni
    # consumption_change_pct (quasi toujours NULL).
    source_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    site_id: Mapped[str] = mapped_column(String(20), nullable=False)
    consumption_kw: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    voltage_v: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    current_a: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    power_factor: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    temperature_celsius: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    humidity_percent: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    quality_score: Mapped[int | None] = mapped_column(nullable=True)
    data_quality: Mapped[str | None] = mapped_column(String(20), nullable=True)
    null_reasons: Mapped[list] = mapped_column(JSON, nullable=False, default=list)


class AggregateGoldDaily(Base):
    __tablename__ = "aggregates_gold_daily"

    # Lecture seule, alimenté par etl/quality.py via postgres_writer.py
    # (infra/postgres/init/03_gold.sql). Une ligne par (site, jour) — recalculée
    # en entier à chaque run (voir postgres_writer.write_gold_daily), pas un
    # cumul incrémental. Seules les colonnes utiles aux KPI du dashboard sont
    # mappées, pas les 19 colonnes de la table.
    record_date: Mapped[date] = mapped_column(primary_key=True)
    site_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    records_count: Mapped[int] = mapped_column(nullable=False, default=0)
    good_count: Mapped[int] = mapped_column(nullable=False, default=0)
    avg_consumption_kw: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    max_consumption_kw: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    total_consumption_kwh: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    avg_quality_score: Mapped[float | None] = mapped_column(Numeric, nullable=True)


class PredictionForecast(Base):
    __tablename__ = "predictions_forecast"

    # Lecture seule, alimenté par ml/predict.py via ml/core/postgres_store.py
    # (infra/postgres/init/06_predictions.sql) — même principe que Site/MeasurementSilver.
    # La clé composite inclut step_minutes : tous les modèles V1 sont horaires, mais un futur
    # modèle au quart d'heure cohabiterait dans la même table (voir le SQL d'init).
    #
    # Les colonnes de provenance (model_version, data_source, champion) sont mappées et non
    # écartées comme "détail interne" : ces modèles sont entraînés sur CSV synthétique et le
    # front doit pouvoir le signaler à l'écran plutôt que de présenter ces valeurs comme des
    # prévisions validées sur donnée réelle.
    site_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    target_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    step_minutes: Mapped[int] = mapped_column(primary_key=True, default=60)
    predicted_kwh: Mapped[float] = mapped_column(Numeric, nullable=False)
    lower_90: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    upper_90: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_version: Mapped[str] = mapped_column(String(10), nullable=False)
    model_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    champion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    data_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    temperature_celsius: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    temperature_source: Mapped[str | None] = mapped_column(String(30), nullable=True)
    predicted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
