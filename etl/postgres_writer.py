"""EV-035 : réplique dans PostgreSQL des données silver/gold calculées par quality.py.

MinIO/Parquet reste la source de vérité (voir quality.py) ; Postgres n'est qu'une copie
interrogeable en SQL pour l'API/dashboard. Schéma : infra/postgres/init/02_silver.sql,
03_gold.sql et 05_quarantine.sql. Une panne Postgres ne doit pas interrompre l'ETL MinIO :
chaque fonction est appelée depuis quality.py dans un bloc try/except qui log et continue.

Environment (mêmes noms que alerts.py) :
	POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
"""

from __future__ import annotations

import json
import os
from typing import Any

import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras


def make_pg_connection() -> Any:
	return psycopg2.connect(
		host=os.environ.get("POSTGRES_HOST", "localhost"),
		port=os.environ.get("POSTGRES_PORT", "5432"),
		dbname=os.environ.get("POSTGRES_DB", "ev_monitoring"),
		user=os.environ.get("POSTGRES_USER", "ev_admin"),
		password=os.environ["POSTGRES_PASSWORD"],
	)


SILVER_COLUMNS = [
	"source_key", "timestamp", "site_id", "site_type",
	"consumption_kw", "consumption_kwh", "voltage_v", "current_a",
	"power_factor", "temperature_celsius", "humidity_percent",
	"null_reasons", "data_quality", "ingested_at", "record_date",
	"record_hour", "missing_fields", "usable_metrics_count",
	"quality_score", "is_valid", "has_anomaly", "consumption_change_pct",
]

# Compteurs de couverture puis métriques : même liste aux deux grains, l'horaire n'est plus
# un sous-ensemble appauvri du journalier (voir quality.GOLD_COUNTERS / GOLD_METRICS).
_GOLD_MEASURES = [
	"records_count", "usable_count", "empty_count", "good_count", "partial_count",
	"degraded_count", "critical_count", "unknown_count", "missing_consumption_count",
	"anomaly_count",
	"avg_consumption_kw", "min_consumption_kw", "max_consumption_kw",
	"total_consumption_kwh", "avg_voltage_v", "avg_current_a", "avg_power_factor",
	"avg_temperature_celsius", "avg_humidity_percent", "avg_quality_score",
]

GOLD_DAILY_COLUMNS = ["record_date", "site_id", "site_type", *_GOLD_MEASURES]

GOLD_HOURLY_COLUMNS = ["record_date", "record_hour", "site_id", "site_type", *_GOLD_MEASURES]

QUARANTINE_COLUMNS = [
	"source_key", "error_type", "error_message", "site_id", "record_date",
	"raw_timestamp", "raw_record", "captured_at",
]

_JSON_COLUMNS = {"null_reasons", "missing_fields"}


def _sql_value(row: dict[str, Any], column: str) -> Any:
	value = row.get(column)
	if column in _JSON_COLUMNS:
		# Relu depuis un Parquet, une colonne de liste revient en ndarray et non en list :
		# le test `isinstance(value, list)` seul écrasait null_reasons / missing_fields par
		# [], c'est-à-dire précisément la colonne qui explique pourquoi la mesure manque.
		if value is None or isinstance(value, (str, bytes)):
			return json.dumps([])
		if isinstance(value, (list, tuple, np.ndarray, pd.Series)):
			return json.dumps([str(item) for item in value])
		return json.dumps([])
	if isinstance(value, pd.Timestamp):
		return None if pd.isna(value) else value.to_pydatetime()
	# psycopg2 n'adapte pas les scalaires numpy (int64, bool_, float64) : les agrégats gold
	# sortent du groupby dans ces types-là, on les ramène en natifs Python.
	if isinstance(value, np.generic):
		value = value.item()
	if value is None or (isinstance(value, float) and pd.isna(value)):
		return None
	return value


def _rows_for(df: pd.DataFrame, columns: list[str]) -> list[tuple[Any, ...]]:
	return [tuple(_sql_value(row, column) for column in columns) for row in df.to_dict("records")]


def write_silver(conn: Any, df: pd.DataFrame) -> None:
	"""Insère les lignes silver d'un batch. Idempotent sur source_key (rejouer un batch
	déjà inséré met simplement à jour les mêmes lignes, sans les dupliquer)."""
	if df.empty:
		return
	columns_sql = ", ".join(SILVER_COLUMNS)
	update_sql = ", ".join(f'{c} = EXCLUDED.{c}' for c in SILVER_COLUMNS if c != "source_key")
	with conn.cursor() as cur:
		psycopg2.extras.execute_values(
			cur,
			f"INSERT INTO measurements_silver ({columns_sql}) VALUES %s "
			f"ON CONFLICT (source_key) DO UPDATE SET {update_sql}",
			_rows_for(df, SILVER_COLUMNS),
		)
	conn.commit()


def write_quarantine(conn: Any, df: pd.DataFrame) -> None:
	"""Insère les rejets d'un lot. Idempotent sur source_key, comme le silver : rejouer un
	lot déjà traité met à jour les mêmes lignes au lieu d'empiler des doublons."""
	if df.empty:
		return
	columns_sql = ", ".join(QUARANTINE_COLUMNS)
	update_sql = ", ".join(f"{c} = EXCLUDED.{c}" for c in QUARANTINE_COLUMNS if c != "source_key")
	with conn.cursor() as cur:
		psycopg2.extras.execute_values(
			cur,
			f"INSERT INTO measurements_quarantine ({columns_sql}) VALUES %s "
			f"ON CONFLICT (source_key) DO UPDATE SET {update_sql}",
			_rows_for(df, QUARANTINE_COLUMNS),
		)
	conn.commit()


def write_gold_daily(conn: Any, df: pd.DataFrame, record_date: Any, site_id: Any) -> None:
	"""Remplace l'intégralité de la partition (record_date, site_id) : comme
	rebuild_gold_partition recalcule le gold en entier à chaque run, Postgres doit refléter
	exactement le même état plutôt que s'accumuler (DELETE + INSERT, pas d'upsert ligne à ligne)."""
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM aggregates_gold_daily WHERE record_date = %s AND site_id = %s",
			(record_date, site_id),
		)
		if not df.empty:
			columns_sql = ", ".join(GOLD_DAILY_COLUMNS)
			psycopg2.extras.execute_values(
				cur,
				f"INSERT INTO aggregates_gold_daily ({columns_sql}) VALUES %s",
				_rows_for(df, GOLD_DAILY_COLUMNS),
			)
	conn.commit()


def write_gold_hourly(conn: Any, df: pd.DataFrame, record_date: Any, site_id: Any) -> None:
	with conn.cursor() as cur:
		cur.execute(
			"DELETE FROM aggregates_gold_hourly WHERE record_date = %s AND site_id = %s",
			(record_date, site_id),
		)
		if not df.empty:
			columns_sql = ", ".join(GOLD_HOURLY_COLUMNS)
			psycopg2.extras.execute_values(
				cur,
				f"INSERT INTO aggregates_gold_hourly ({columns_sql}) VALUES %s",
				_rows_for(df, GOLD_HOURLY_COLUMNS),
			)
	conn.commit()
