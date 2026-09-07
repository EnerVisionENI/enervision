"""
Accès Postgres du module ML : lecture des entrées d'inférence dans le gold, écriture des
prévisions dans predictions_forecast (voir infra/postgres/init/06_predictions.sql).

Pourquoi Postgres et pas MinIO comme core/data.py : MinIO reste la source de vérité du gold,
mais l'inférence a de toute façon besoin d'une connexion Postgres pour écrire ses résultats,
et les deux lectures dont elle a besoin (la climatologie de température, les valeurs réelles
d'une fenêtre pour le backtest) sont des agrégats — un GROUP BY contre la réplique SQL, plutôt
que le téléchargement de N Parquet journaliers à regrouper en pandas.

Environment (mêmes noms que etl/postgres_writer.py) :
    POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras

# Ordre des colonnes de l'INSERT — doit suivre 06_predictions.sql. predicted_at est laissé
# à son DEFAULT now() : c'est l'heure d'écriture réelle, pas une valeur calculée en Python.
PREDICTION_COLUMNS = [
    "site_id",
    "target_ts",
    "step_minutes",
    "predicted_kwh",
    "lower_90",
    "upper_90",
    "model_name",
    "model_version",
    "model_stage",
    "champion",
    "run_id",
    "data_source",
    "temperature_celsius",
    "temperature_source",
    "horizon_h",
]


def make_pg_connection() -> Any:
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "ev_monitoring"),
        user=os.environ.get("POSTGRES_USER", "ev_admin"),
        password=os.environ["POSTGRES_PASSWORD"],
    )


def _sql_value(value: Any) -> Any:
    """psycopg2 n'adapte pas les scalaires numpy : les prédictions sortent de pandas en
    float64/int64, on les ramène en natifs Python (même problème que etl/postgres_writer)."""
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    if isinstance(value, np.generic):
        value = value.item()
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return value


def load_weather_climatology(
    conn: Any, site_id: str, lookback_days: int, tz: str
) -> tuple[pd.DataFrame, dict[str, float | None]]:
    """
    Météo moyenne observée par créneau (jour de semaine, heure) sur les `lookback_days`
    derniers jours de gold, plus la moyenne toutes heures confondues de chaque variable.

    Ce sont les seules entrées non calendaires des modèles, et elles portent sur le futur :
    faute de prévision météo dans le projet, on rejoue le climat récent du site. Le créneau est
    calculé dans `tz` — pas en UTC — pour rester aligné sur le fuseau dans lequel le modèle a
    appris ses 168 coefficients horaires.

    ISODOW - 1 donne lundi = 0, la convention de pandas .dt.dayofweek utilisée à
    l'entraînement (scripts/train_csv_experiment.prepare_time_of_week).
    """
    query = """
        SELECT
            EXTRACT(ISODOW FROM record_hour AT TIME ZONE %(tz)s)::int - 1 AS day_of_week,
            EXTRACT(HOUR   FROM record_hour AT TIME ZONE %(tz)s)::int     AS hour,
            AVG(avg_temperature_celsius)::float                          AS temperature_celsius,
            AVG(avg_humidity_percent)::float                             AS humidity_percent
        FROM aggregates_gold_hourly
        WHERE site_id = %(site_id)s
          AND record_hour >= now() - make_interval(days => %(lookback_days)s)
        GROUP BY 1, 2
    """
    params = {"tz": tz, "site_id": site_id, "lookback_days": lookback_days}
    columns = ["day_of_week", "hour", "temperature_celsius", "humidity_percent"]
    with conn.cursor() as cur:
        cur.execute(query, params)
        slots = pd.DataFrame(cur.fetchall(), columns=columns)
    if slots.empty:
        return slots.set_index(["day_of_week", "hour"]), {"temperature_celsius": None, "humidity_percent": None}

    slots = slots.set_index(["day_of_week", "hour"]).sort_index()
    # Moyenne de repli calculée sur les créneaux et non en SQL : un site dont le gold ne couvre
    # que les heures ouvrées ne doit pas voir ses nuits comblées par une moyenne pondérée par
    # le nombre de relevés, qui serait biaisée vers les heures les mieux couvertes.
    means = {column: (float(slots[column].mean()) if slots[column].notna().any() else None) for column in slots.columns}
    return slots, means


def load_gold_actuals(conn: Any, site_id: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """
    Heures réellement observées d'un site sur [start, end[ : sert au backtest de contrôle
    (predict.py --check), qui rejoue le modèle sur du passé connu avec la vraie température.

    consumption_kwh := avg_consumption_kw, la même équivalence que core/data.load_gold_hourly.
    Elle est discutable en unités, mais c'est celle sur laquelle les modèles ont été comparés :
    en changer ici rendrait le MASE des runs et le MAPE du backtest incomparables.
    """
    query = """
        SELECT record_hour, avg_consumption_kw::float, avg_temperature_celsius::float,
               avg_humidity_percent::float
        FROM aggregates_gold_hourly
        WHERE site_id = %s AND record_hour >= %s AND record_hour < %s
          AND avg_consumption_kw IS NOT NULL
        ORDER BY record_hour
    """
    with conn.cursor() as cur:
        cur.execute(query, (site_id, start.to_pydatetime(), end.to_pydatetime()))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["timestamp", "consumption_kwh", "temperature_celsius", "humidity_percent"])
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


def write_predictions(
    conn: Any,
    df: pd.DataFrame,
    site_id: str,
    step_minutes: int,
    start: pd.Timestamp,
) -> int:
    """
    Remplace tout ce que le site avait de prévu à partir de `start` par les lignes de `df`.

    DELETE + INSERT et non upsert ligne à ligne, et le DELETE porte sur tout target_ts >= start
    plutôt que sur la seule fenêtre réécrite : si l'horizon raccourcit d'un run à l'autre (168 h
    puis 24 h), les heures au-delà doivent disparaître au lieu de rester en place comme une
    prévision à jour. C'est la même logique que le loader gold
    (etl/postgres_writer.write_gold_hourly), appliquée à une fenêtre glissante.

    Les lignes déjà passées (target_ts < start) sont conservées : elles ne coûtent presque rien
    (7 sites x 24 h/j) et ce sont les seules qui permettront un jour de comparer prévu et
    réalisé. Elles ne sont jamais réécrites, donc chacune reste la prévision réellement émise
    pour cette heure-là, avec la version de modèle qui l'a produite.

    Le DELETE est borné par step_minutes : sinon une future inférence au quart d'heure
    effacerait les lignes horaires du même site.
    """
    columns_sql = ", ".join(PREDICTION_COLUMNS)
    rows = [tuple(_sql_value(row.get(column)) for column in PREDICTION_COLUMNS) for row in df.to_dict("records")]
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM predictions_forecast WHERE site_id = %s AND step_minutes = %s AND target_ts >= %s",
            (site_id, step_minutes, start.to_pydatetime()),
        )
        if rows:
            psycopg2.extras.execute_values(
                cur,
                f"INSERT INTO predictions_forecast ({columns_sql}) VALUES %s",
                rows,
            )
    conn.commit()
    return len(rows)
