"""Bronze -> Silver/Gold ETL for EnerVision, sur MinIO (S3-compatible) + PostgreSQL.

Lit les objets de mesure bruts du bucket bronze (un objet JSON par mesure, écrit par
collect.py), valide et normalise, écrit des batches silver en Parquet, et pousse les
enregistrements invalides en quarantaine.

Les agrégats gold daily/hourly ne sont PAS recalculés dans ce passage. Recalculer une
partition relit l'intégralité de son silver : fait à chaque cycle de collecte (60 s), le
cycle débordait de son intervalle et le silver n'était plus alimenté qu'une minute sur
deux ; fait à chaque tranche du rattrapage initial (bootstrap.py), c'était le même travail
refait des dizaines de fois. Chaque partition (record_date, site_id) touchée est donc
empilée dans manifests/gold_pending.json, et un passage dédié `--gold-only` la recalcule —
le gold n'est de toute façon consommé qu'aux grains horaire et journalier.

MinIO reste la source de vérité ; les mêmes lignes silver/gold sont répliquées dans
PostgreSQL (voir postgres_writer.py) pour être interrogeables en SQL par l'API/dashboard.
Une panne PostgreSQL n'interrompt pas l'écriture MinIO (voir process_batch).

Buckets :
	bronze       objets bruts        {site_id}/{YYYY-MM-DD}/{HHMMSS}.json
	silver       batches nettoyés    record_date=…/site_id=…/batch_*.parquet
	gold         agrégats            daily|hourly/record_date=…/site_id=…/*.parquet
	quarantine   rejets              record_date=…/site_id=…/rejects_*.parquet
	manifests    état incrémental    etl_state.json      (clés bronze déjà traitées)
	manifests    gold à recalculer   gold_pending.json   (partitions silver modifiées)

Complétude : l'API mock simule des pannes de capteurs vivantes et les applique aux relevés
historiques au moment du fetch (voir /api/v1/sensors/status). Un backfill lancé pendant une
panne ramène des lectures « critical » dont les 7 métriques sont nulles. Elles sont
conservées en silver — le trou doit rester visible et daté — mais marquées is_valid=False,
comptées à part dans le gold (critical_count, empty_count) et jamais confondues avec une
mesure : `total_consumption_kwh` vaut NaN et non 0 quand rien n'a été mesuré, et le grain
horaire est complété à 24 lignes pour qu'une heure sans relevé existe explicitement.
repair.py rejoue ces fenêtres quand les capteurs sont revenus au vert.

Tables PostgreSQL (infra/postgres/init/02_silver.sql, 03_gold.sql, 05_quarantine.sql) :
	measurements_silver, aggregates_gold_daily, aggregates_gold_hourly,
	measurements_quarantine

Environment :
	MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY          (obligatoires)
	MINIO_BUCKET_BRONZE / _SILVER / _GOLD / _QUARANTINE / _MANIFESTS  (défauts homonymes)
	MINIO_USE_SSL                                              (défaut "false")
	POSTGRES_HOST / _PORT / _DB / _USER / _PASSWORD             (voir postgres_writer.py)

Usage :
	python quality.py                                    # bronze -> silver, gold différé
	python quality.py --with-gold                        # ... + gold recalculé dans la foulée
	python quality.py --gold-only                        # gold des partitions en attente
	python quality.py --gold-only --gold-date 2026-09-01 # ... + toute une journée (filet)
	python quality.py --bronze-bucket bronze --silver-bucket silver --gold-bucket gold
	python quality.py --fetch-workers 32   # gros rattrapage : lecture bronze en parallèle
	python quality.py --fetch-workers 1    # forcer la lecture séquentielle
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd
import postgres_writer
import psycopg2
import storage
from botocore.exceptions import BotoCoreError, ClientError

NUMERIC_COLUMNS = [
	"consumption_kw",
	"consumption_kwh",
	"voltage_v",
	"current_a",
	"power_factor",
	"temperature_celsius",
	"humidity_percent",
]

OPTIONAL_NUMERIC_COLUMNS = [
	"voltage_v",
	"current_a",
	"power_factor",
	"temperature_celsius",
	"humidity_percent",
]

MANDATORY_COLUMNS = ["timestamp", "site_id"]

# Niveaux de `data_quality` émis par l'API mock, et pénalité de score associée.
# "critical" (null_reasons = network_loss : TOUTES les métriques absentes) tombait
# auparavant dans la branche par défaut à -5, donc mieux notée que "partial" (-10) et
# "degraded" (-25) : sur un échantillon de 6 330 lignes, les 6 254 lectures critical
# sortaient à 55 contre 40 de moyenne pour degraded. L'ordre est désormais explicite.
QUALITY_PENALTIES = {
	"good": 0,
	"partial": 10,
	"degraded": 25,
	"critical": 45,
}
UNKNOWN_QUALITY_PENALTY = 5
QUALITY_LEVELS = tuple(QUALITY_PENALTIES)

QUARANTINE_COLUMNS = [
	"source_key",
	"error_type",
	"error_message",
	"site_id",
	"record_date",
	"raw_timestamp",
	"raw_record",
	"captured_at",
]

PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE = "application/json"


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="EnerVision ETL bronze -> silver/gold, 100 % MinIO.")
	parser.add_argument("--bronze-bucket", default=os.environ.get("MINIO_BUCKET_BRONZE", "bronze"))
	parser.add_argument("--bronze-prefix", default=os.environ.get("MINIO_PREFIX_BRONZE", ""))
	parser.add_argument("--silver-bucket", default=os.environ.get("MINIO_BUCKET_SILVER", "silver"))
	parser.add_argument("--gold-bucket", default=os.environ.get("MINIO_BUCKET_GOLD", "gold"))
	parser.add_argument(
		"--quarantine-bucket", default=os.environ.get("MINIO_BUCKET_QUARANTINE", "quarantine")
	)
	parser.add_argument("--manifests-bucket", default=os.environ.get("MINIO_BUCKET_MANIFESTS", "manifests"))
	parser.add_argument("--state-key", default=os.environ.get("ETL_STATE_KEY", "etl_state.json"))
	parser.add_argument(
		"--pending-gold-key",
		default=os.environ.get("ETL_PENDING_GOLD_KEY", "gold_pending.json"),
	)
	parser.add_argument(
		"--with-gold",
		action="store_true",
		help="Recalcule le gold à la fin du passage silver au lieu de le différer.",
	)
	parser.add_argument(
		"--gold-only",
		action="store_true",
		help="Ne lit pas le bronze : recalcule seulement le gold des partitions en attente.",
	)
	parser.add_argument(
		"--gold-date",
		default=None,
		help="Avec --gold-only : recalcule aussi TOUTES les partitions de cette journée (YYYY-MM-DD).",
	)
	parser.add_argument(
		"--max-objects",
		type=int,
		default=int(os.environ.get("ETL_MAX_OBJECTS", "0")),
		help="Nombre max d'objets bronze traités par exécution (0 = pas de limite).",
	)
	parser.add_argument(
		"--fetch-workers",
		type=int,
		default=int(os.environ.get("ETL_FETCH_WORKERS", "16")),
		help="Téléchargements bronze menés en parallèle (1 = séquentiel). Défaut 16.",
	)
	return parser


def utc_now_iso() -> str:
	return datetime.now(UTC).isoformat()


def run_stamp() -> str:
	return datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")


# --------------------------------------------------------------------------- S3

def ensure_bucket(s3: Any, bucket: str) -> None:
	"""Crée le bucket s'il n'existe pas (no-op sinon). init-buckets.sh le fait déjà
	à la création de l'infra ; ce garde-fou évite un échec si l'ETL démarre avant."""
	try:
		s3.head_bucket(Bucket=bucket)
		return
	except ClientError:
		pass
	try:
		s3.create_bucket(Bucket=bucket)
	except ClientError as exc:
		code = exc.response.get("Error", {}).get("Code")
		if code not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
			raise


def s3_list_keys(s3: Any, bucket: str, prefix: str = "") -> list[str]:
	keys: list[str] = []
	paginator = s3.get_paginator("list_objects_v2")
	for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
		for obj in page.get("Contents", []):
			keys.append(obj["Key"])
	return keys


def s3_get_bytes(s3: Any, bucket: str, key: str) -> bytes | None:
	try:
		return s3.get_object(Bucket=bucket, Key=key)["Body"].read()
	except ClientError as exc:
		if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
			return None
		raise


def s3_put_bytes(s3: Any, bucket: str, key: str, data: bytes, content_type: str) -> None:
	s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


def s3_put_parquet(s3: Any, bucket: str, key: str, df: pd.DataFrame) -> None:
	buffer = io.BytesIO()
	df.to_parquet(buffer, index=False)
	s3.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue(), ContentType=PARQUET_CONTENT_TYPE)


def s3_read_parquet(s3: Any, bucket: str, key: str) -> pd.DataFrame:
	body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
	return pd.read_parquet(io.BytesIO(body))


# ------------------------------------------------------------------------ state

def load_state(s3: Any, bucket: str, key: str) -> set[str]:
	"""Ensemble des clés d'objets bronze déjà traitées lors des exécutions précédentes."""
	body = s3_get_bytes(s3, bucket, key)
	if body is None:
		return set()
	try:
		raw = json.loads(body.decode("utf-8"))
	except json.JSONDecodeError:
		return set()
	processed = raw.get("processed", [])
	if not isinstance(processed, list):
		return set()
	return {str(k) for k in processed}


def save_state(s3: Any, bucket: str, key: str, processed: set[str]) -> None:
	payload = {
		"last_run_at": utc_now_iso(),
		"processed_count": len(processed),
		"processed": sorted(processed),
	}
	body = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
	s3_put_bytes(s3, bucket, key, body, JSON_CONTENT_TYPE)


# -------------------------------------------------------- gold en attente

def load_pending_gold(s3: Any, bucket: str, key: str) -> set[tuple[str, str]]:
	"""Partitions (record_date, site_id) dont le silver a bougé depuis le dernier recalcul gold.
	Objet distinct de l'état incrémental : le passage silver et le passage gold l'écrivent
	chacun de leur côté, un run gold ne doit pas pouvoir réécrire — ni perdre — la liste des
	clés bronze déjà traitées."""
	body = s3_get_bytes(s3, bucket, key)
	if body is None:
		return set()
	try:
		raw = json.loads(body.decode("utf-8"))
	except json.JSONDecodeError:
		return set()
	partitions = raw.get("partitions", [])
	if not isinstance(partitions, list):
		return set()
	return {
		(str(item[0]), str(item[1]))
		for item in partitions
		if isinstance(item, (list, tuple)) and len(item) == 2
	}


def save_pending_gold(s3: Any, bucket: str, key: str, partitions: set[tuple[str, str]]) -> None:
	payload = {
		"updated_at": utc_now_iso(),
		"partitions_count": len(partitions),
		"partitions": [list(partition) for partition in sorted(partitions)],
	}
	corps = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
	s3_put_bytes(s3, bucket, key, corps, JSON_CONTENT_TYPE)


def add_pending_gold(s3: Any, bucket: str, key: str, partitions: set[tuple[str, str]]) -> None:
	"""Relit l'objet avant d'écrire : un passage gold peut être en train d'en retirer."""
	if not partitions:
		return
	save_pending_gold(s3, bucket, key, load_pending_gold(s3, bucket, key) | set(partitions))


def remove_pending_gold(s3: Any, bucket: str, key: str, partitions: set[tuple[str, str]]) -> None:
	"""Idem : on retire seulement ce qui vient d'être recalculé, sans écraser ce que le
	passage silver a pu empiler pendant le recalcul."""
	if not partitions:
		return
	save_pending_gold(s3, bucket, key, load_pending_gold(s3, bucket, key) - set(partitions))


def partitions_for_date(s3: Any, silver_bucket: str, record_date: str) -> set[tuple[str, str]]:
	"""Toutes les partitions silver d'une journée. Sert de filet au recalcul quotidien :
	une partition perdue (passage gold en échec, redémarrage entre l'écriture silver et
	celle de la file) ne serait sinon jamais reprise."""
	prefix = f"record_date={record_date}/"
	partitions: set[tuple[str, str]] = set()
	for key in s3_list_keys(s3, silver_bucket, prefix):
		segments = key.split("/")
		if len(segments) >= 2 and segments[1].startswith("site_id="):
			partitions.add((str(record_date), segments[1][len("site_id="):]))
	return partitions


# ------------------------------------------------------------------ bronze read

def fetch_record(s3: Any, bucket: str, key: str) -> dict[str, Any]:
	"""Télécharge un objet bronze et le décode en dict de mesure.
	Renvoie {"_parse_error": ..., "_raw_line": ...} si le contenu n'est pas du JSON objet valide."""
	body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
	try:
		text = body.decode("utf-8")
	except UnicodeDecodeError as exc:
		return {
			"_parse_error": f"UnicodeDecodeError: {exc}",
			"_raw_line": body.decode("utf-8", errors="replace"),
		}

	try:
		payload = json.loads(text)
	except json.JSONDecodeError as exc:
		return {"_parse_error": f"JSONDecodeError: {exc.msg}", "_raw_line": text}

	if not isinstance(payload, dict):
		return {"_parse_error": "objet JSON inattendu (pas un dict)", "_raw_line": text}
	return payload


def fetch_records(
	s3: Any,
	bucket: str,
	keys: list[str],
	*,
	workers: int,
) -> tuple[list[tuple[str, dict[str, Any]]], int]:
	"""Télécharge et décode les objets bronze `keys`, en parallèle sur `workers`
	threads (le coût dominant d'un gros run est le round-trip réseau, pas le CPU).

	Renvoie (records, erreurs_lecture) :
		- records : (clé, mesure décodée), réordonnés dans l'ordre de `keys` pour que
			la suite du traitement soit déterministe quel que soit l'ordre d'arrivée ;
		- erreurs_lecture : nombre d'objets dont le GET S3 a échoué. Ces clés sont
			simplement omises (comme dans la version séquentielle) : non marquées
			traitées, elles repasseront au prochain run.

	Une erreur de décodage (JSON invalide, pas un dict) n'est PAS une erreur de
	lecture : fetch_record la renvoie comme {"_parse_error": ...} et la clé part
	en quarantaine, donc reste comptée dans records.
	"""
	if not keys:
		return [], 0

	workers = max(1, min(workers, len(keys)))
	decoded: dict[str, dict[str, Any]] = {}
	fetch_errors = 0

	with ThreadPoolExecutor(max_workers=workers) as pool:
		futures = {pool.submit(fetch_record, s3, bucket, key): key for key in keys}
		for future in as_completed(futures):
			key = futures[future]
			try:
				decoded[key] = future.result()
			except (BotoCoreError, ClientError) as exc:
				fetch_errors += 1
				print(f"{key} : lecture impossible, {exc}")

	ordered = [(key, decoded[key]) for key in keys if key in decoded]
	return ordered, fetch_errors


# ---------------------------------------------------------------- normalisation

def parse_timestamp(value: Any) -> pd.Timestamp | None:
	if value is None:
		return None
	try:
		parsed = pd.to_datetime(value, utc=True, errors="coerce")
	except Exception:
		return None
	if pd.isna(parsed):
		return None
	return parsed


def safe_float(value: Any) -> float | None:
	if value is None or value == "":
		return None
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	if math.isnan(number) or math.isinf(number):
		return None
	return number


def normalize_null_reasons(value: Any) -> list[str]:
	if value is None:
		return []
	if isinstance(value, list):
		return [str(item) for item in value if str(item).strip()]
	if isinstance(value, str):
		value = value.strip()
		if not value:
			return []
		try:
			parsed = json.loads(value)
			if isinstance(parsed, list):
				return [str(item) for item in parsed if str(item).strip()]
		except json.JSONDecodeError:
			pass
		return [part.strip() for part in value.split(",") if part.strip()]
	return [str(value)]


def compute_quality_score(row: dict[str, Any]) -> int:
	score = 100

	source_quality = str(row.get("data_quality") or "").lower()
	score -= QUALITY_PENALTIES.get(source_quality, UNKNOWN_QUALITY_PENALTY)

	missing_optional = sum(1 for column in OPTIONAL_NUMERIC_COLUMNS if row.get(column) is None)
	score -= missing_optional * 4

	if row.get("consumption_kw") is None:
		score -= 20

	if not row.get("timestamp"):
		score -= 30

	if not row.get("site_id"):
		score -= 30

	return max(0, min(100, score))


def build_quarantine_row(
	source_key: str,
	error_type: str,
	error_message: str,
	*,
	record: dict[str, Any] | None = None,
	raw_text: str | None = None,
) -> dict[str, Any]:
	"""Ligne de quarantaine au schéma fixe (QUARANTINE_COLUMNS).

	`site_id` / `record_date` sont extraits du rejet quand ils sont lisibles, pour que le
	bucket soit partitionné sur la date DE LA MESURE comme silver et gold — et non plus sur
	la date de traitement, qui ne permettait pas de recoller un rejet à son trou dans le
	gold. Ils restent nuls (partition "unknown") si l'enregistrement est illisible.
	`raw_record` est toujours une chaîne JSON : un schéma stable est indispensable pour
	empiler les rejets en Parquet."""
	site_id = record.get("site_id") if isinstance(record, dict) else None
	raw_timestamp = record.get("timestamp") if isinstance(record, dict) else None
	timestamp = parse_timestamp(raw_timestamp)

	raw_record = json.dumps(record, ensure_ascii=False, default=str) if record is not None else raw_text

	return {
		"source_key": source_key,
		"error_type": error_type,
		"error_message": error_message,
		"site_id": str(site_id) if site_id not in (None, "") else None,
		"record_date": timestamp.date().isoformat() if timestamp is not None else None,
		"raw_timestamp": str(raw_timestamp) if raw_timestamp not in (None, "") else None,
		"raw_record": raw_record,
		"captured_at": utc_now_iso(),
	}


def normalize_record(
	record: dict[str, Any],
	source_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
	if record.get("_parse_error"):
		return None, build_quarantine_row(
			source_key,
			"parse_error",
			record["_parse_error"],
			raw_text=record.get("_raw_line"),
		)

	missing_mandatory = [column for column in MANDATORY_COLUMNS if record.get(column) in (None, "")]
	if missing_mandatory:
		return None, build_quarantine_row(
			source_key,
			"missing_mandatory_fields",
			", ".join(missing_mandatory),
			record=record,
		)

	timestamp = parse_timestamp(record.get("timestamp"))
	if timestamp is None:
		return None, build_quarantine_row(
			source_key,
			"invalid_timestamp",
			f"Invalid timestamp: {record.get('timestamp')}",
			record=record,
		)

	normalized: dict[str, Any] = {
		"timestamp": timestamp,
		"site_id": str(record.get("site_id")),
		"site_type": record.get("site_type"),
		"consumption_kw": safe_float(record.get("consumption_kw")),
		"consumption_kwh": safe_float(record.get("consumption_kwh")),
		"voltage_v": safe_float(record.get("voltage_v")),
		"current_a": safe_float(record.get("current_a")),
		"power_factor": safe_float(record.get("power_factor")),
		"temperature_celsius": safe_float(record.get("temperature_celsius")),
		"humidity_percent": safe_float(record.get("humidity_percent")),
		"null_reasons": normalize_null_reasons(record.get("null_reasons")),
		"data_quality": record.get("data_quality") or "unknown",
		"source_key": source_key,
		"ingested_at": pd.Timestamp.now(tz="UTC"),
	}
	normalized["record_date"] = normalized["timestamp"].date().isoformat()
	normalized["record_hour"] = normalized["timestamp"].floor("h")
	normalized["missing_fields"] = [column for column in NUMERIC_COLUMNS if normalized.get(column) is None]
	normalized["usable_metrics_count"] = len(NUMERIC_COLUMNS) - len(normalized["missing_fields"])
	normalized["quality_score"] = compute_quality_score(normalized)
	# `is_valid` ne dit pas "bien formé" — c'est déjà acquis ici, sinon la ligne serait
	# partie en quarantaine — mais "porte au moins une mesure". Une lecture critical /
	# network_loss a ses 7 métriques à null : elle reste en silver pour que le trou soit
	# visible et daté, mais elle ne doit pas peser comme une mesure dans le gold.
	normalized["is_valid"] = normalized["usable_metrics_count"] > 0
	normalized["has_anomaly"] = False

	return normalized, None


# -------------------------------------------------------------------- agrégats

def _somme_ou_nan(series: pd.Series) -> float:
	"""Somme qui vaut NaN — et non 0 — quand le groupe n'a aucune valeur.

	`Series.sum()` renvoie 0.0 sur un groupe entièrement NaN. Le gold annonçait donc
	`total_consumption_kwh = 0` pour une journée dont les 10 relevés avaient une
	consommation absente : un modèle entraîné là-dessus apprend une conso nulle réelle au
	lieu de voir un trou. `min_count=1` rend l'inconnu explicite."""
	return series.sum(min_count=1)


# Compteurs de couverture, identiques aux deux grains. Ils partitionnent `records_count`
# sans reste : good + partial + degraded + critical + unknown == records_count, ce qui
# n'était pas le cas avant (les lignes critical n'étaient comptées nulle part).
GOLD_COUNTERS: dict[str, tuple[str, Any]] = {
	"records_count": ("site_id", "size"),
	"usable_count": ("is_usable", "sum"),
	"empty_count": ("is_empty_reading", "sum"),
	"good_count": ("is_good", "sum"),
	"partial_count": ("is_partial", "sum"),
	"degraded_count": ("is_degraded", "sum"),
	"critical_count": ("is_critical", "sum"),
	"unknown_count": ("is_unknown", "sum"),
	"missing_consumption_count": ("missing_consumption", "sum"),
	"anomaly_count": ("anomaly", "sum"),
}

GOLD_METRICS: dict[str, tuple[str, Any]] = {
	"avg_consumption_kw": ("consumption_kw", "mean"),
	"min_consumption_kw": ("consumption_kw", "min"),
	"max_consumption_kw": ("consumption_kw", "max"),
	"total_consumption_kwh": ("consumption_kwh", _somme_ou_nan),
	"avg_voltage_v": ("voltage_v", "mean"),
	"avg_current_a": ("current_a", "mean"),
	"avg_power_factor": ("power_factor", "mean"),
	"avg_temperature_celsius": ("temperature_celsius", "mean"),
	"avg_humidity_percent": ("humidity_percent", "mean"),
	"avg_quality_score": ("quality_score", "mean"),
}

GOLD_COUNTER_NAMES = list(GOLD_COUNTERS)
GOLD_METRIC_NAMES = list(GOLD_METRICS)


def _completeness_flags(df: pd.DataFrame) -> pd.DataFrame:
	"""Indicateurs booléens dérivés, base commune des deux grains.

	Tout est recalculé depuis les colonnes de mesure plutôt que lu dans `is_valid` /
	`usable_metrics_count` : un recalcul gold relit des batches silver écrits par des
	versions antérieures du pipeline, où ces colonnes sont absentes ou valent True partout.
	Dériver garantit le même verdict sur l'ancien et le nouveau silver."""
	if "data_quality" in df.columns:
		quality = df["data_quality"].astype(str).str.lower()
	else:
		quality = pd.Series("", index=df.index, dtype="object")

	presentes = [column for column in NUMERIC_COLUMNS if column in df.columns]
	utilisables = df[presentes].notna().sum(axis=1) if presentes else pd.Series(0, index=df.index)

	flags: dict[str, Any] = {f"is_{niveau}": quality.eq(niveau) for niveau in QUALITY_LEVELS}
	flags["is_unknown"] = ~quality.isin(QUALITY_LEVELS)
	flags["is_usable"] = utilisables > 0
	flags["is_empty_reading"] = utilisables == 0
	flags["missing_consumption"] = (
		df["consumption_kw"].isna() if "consumption_kw" in df.columns
		else pd.Series(True, index=df.index)
	)
	flags["anomaly"] = (
		df["has_anomaly"].fillna(False).astype(bool) if "has_anomaly" in df.columns
		else pd.Series(False, index=df.index)
	)
	return df.assign(**flags)


def _aggregate(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
	agrege = (
		_completeness_flags(df)
		.groupby(keys, dropna=False)
		.agg(**GOLD_COUNTERS, **GOLD_METRICS)
		.reset_index()
	)
	for compteur in GOLD_COUNTER_NAMES:
		agrege[compteur] = agrege[compteur].fillna(0).astype("int64")
	return agrege


def aggregate_daily(df: pd.DataFrame) -> pd.DataFrame:
	return _aggregate(df, ["record_date", "site_id", "site_type"])


def aggregate_hourly(df: pd.DataFrame) -> pd.DataFrame:
	"""Même jeu de colonnes que le grain journalier.

	L'horaire ne portait que records_count / avg-min-max consommation / avg_quality_score :
	impossible d'y distinguer « aucun relevé » de « relevés reçus mais tous vides », alors
	que c'est le grain sur lequel s'entraînent les modèles de charge."""
	horaire = df.assign(record_hour=df["record_hour"].dt.strftime("%Y-%m-%dT%H:00:00Z"))
	return _aggregate(horaire, ["record_date", "record_hour", "site_id", "site_type"])


def fill_hourly_grid(hourly: pd.DataFrame, record_date: Any, site_id: Any) -> pd.DataFrame:
	"""Complète la journée aux 24 heures.

	Une heure sans aucun relevé n'apparaît pas dans le groupby : elle est alors
	indiscernable d'une heure absente du jeu de données, et un modèle de série temporelle
	recolle deux heures non adjacentes sans le savoir. On la matérialise avec des compteurs
	à 0 et des métriques à NaN — « on sait qu'on ne sait pas »."""
	if hourly.empty:
		return hourly

	types = hourly["site_type"].dropna()
	grille = pd.DataFrame({
		"record_date": str(record_date),
		"record_hour": [f"{record_date}T{heure:02d}:00:00Z" for heure in range(24)],
		"site_id": str(site_id),
		"site_type": types.iloc[0] if not types.empty else None,
	})

	complet = grille.merge(
		hourly.drop(columns=["record_date", "site_id", "site_type"]),
		on="record_hour",
		how="left",
	)
	for compteur in GOLD_COUNTER_NAMES:
		if compteur in complet.columns:
			complet[compteur] = complet[compteur].fillna(0).astype("int64")
	return complet.sort_values("record_hour").reset_index(drop=True)


def add_simple_anomalies(df: pd.DataFrame) -> pd.DataFrame:
	if df.empty:
		return df

	df = df.sort_values(["site_id", "timestamp"]).copy()
	df["prev_consumption_kw"] = df.groupby("site_id")["consumption_kw"].shift(1)
	prev_abs = df["prev_consumption_kw"].abs().replace(0, pd.NA)
	df["consumption_change_pct"] = (df["consumption_kw"] - df["prev_consumption_kw"]).abs() / prev_abs
	df.loc[df["prev_consumption_kw"].isna(), "consumption_change_pct"] = pd.NA

	site_stats = (
		df.groupby("site_id")["consumption_kw"]
		.agg(["mean", "std"])
		.rename(columns={"mean": "site_mean", "std": "site_std"})
	)
	df = df.join(site_stats, on="site_id")

	df["zscore_flag"] = False
	valid_std = df["site_std"].fillna(0) > 0
	df.loc[valid_std, "zscore_flag"] = (
		(df.loc[valid_std, "consumption_kw"] - df.loc[valid_std, "site_mean"]).abs()
		> 3 * df.loc[valid_std, "site_std"]
	)

	df["delta_flag"] = df["consumption_change_pct"].fillna(0) > 0.35
	df["has_anomaly"] = df["zscore_flag"] | df["delta_flag"]
	return df.drop(columns=["prev_consumption_kw", "site_mean", "site_std", "zscore_flag", "delta_flag"])


def rebuild_gold_partition(
	s3: Any,
	silver_bucket: str,
	gold_bucket: str,
	record_date: Any,
	site_id: Any,
	pg_conn: Any = None,
) -> None:
	"""Recalcule le gold daily/hourly d'une partition (record_date, site_id) à partir
	de TOUS les batches silver de cette partition, puis écrase un unique objet par grain.
	Le gold reste un agrégat complet et unique par partition, même si le silver est en
	micro-batches append-only. put_object étant atomique, pas besoin de tmp + rename."""
	prefix = f"record_date={record_date}/site_id={site_id}/"
	silver_keys = sorted(k for k in s3_list_keys(s3, silver_bucket, prefix) if k.endswith(".parquet"))
	if not silver_keys:
		return

	partition_df = pd.concat(
		(s3_read_parquet(s3, silver_bucket, k) for k in silver_keys),
		ignore_index=True,
	)

	# Le silver est append-only : réingérer une clé bronze (état incrémental purgé, reprise
	# d'un backfill vide par repair.py) ajoute un batch sans retirer l'ancien. Sans ce
	# dédoublonnage, la mesure serait comptée deux fois dans records_count et pèserait
	# double dans chaque moyenne. On garde la version la plus récemment ingérée.
	if "source_key" in partition_df.columns:
		tri = "ingested_at" if "ingested_at" in partition_df.columns else "source_key"
		partition_df = (
			partition_df.sort_values(tri)
			.drop_duplicates("source_key", keep="last")
			.reset_index(drop=True)
		)

	for column in NUMERIC_COLUMNS:
		if column in partition_df.columns:
			partition_df[column] = pd.to_numeric(partition_df[column], errors="coerce")

	# Les flags d'anomalie écrits en silver sont calculés à l'échelle du batch. En collecte
	# temps réel un batch vaut une ligne par site : shift(1) et l'écart-type y sont toujours
	# NaN, donc has_anomaly ne se déclenchait jamais (1 cas sur 9 478 lignes mesurées). Ici
	# on tient TOUTE la partition, l'écart au précédent et le z-score ont enfin un sens.
	partition_df = add_simple_anomalies(partition_df)

	daily_df = aggregate_daily(partition_df)
	hourly_df = fill_hourly_grid(aggregate_hourly(partition_df), record_date, site_id)

	s3_put_parquet(
		s3, gold_bucket,
		f"daily/record_date={record_date}/site_id={site_id}/daily.parquet",
		daily_df,
	)
	s3_put_parquet(
		s3, gold_bucket,
		f"hourly/record_date={record_date}/site_id={site_id}/hourly.parquet",
		hourly_df,
	)

	if pg_conn is not None:
		try:
			postgres_writer.write_gold_daily(pg_conn, daily_df, record_date, site_id)
			postgres_writer.write_gold_hourly(pg_conn, hourly_df, record_date, site_id)
		except psycopg2.Error as exc:
			pg_conn.rollback()
			print(f"PostgreSQL : écriture gold ({record_date}, {site_id}) échouée, {exc}")


def write_quarantine(
	s3: Any,
	bucket: str,
	bad_rows: list[dict[str, Any]],
	*,
	pg_conn: Any = None,
) -> int:
	"""Écrit les rejets en Parquet, partitionnés comme silver et gold.

	L'ancienne disposition — un petit objet JSON par rejet, sous la date de TRAITEMENT —
	rendait la quarantaine inexploitable : il fallait lister puis GET des milliers d'objets
	pour répondre à « quels sites rejettent, et quand », et un rejet ne pouvait pas être
	rapproché du trou qu'il laisse dans le gold. On empile désormais un Parquet par lot sous
	record_date=…/site_id=…/, au schéma fixe QUARANTINE_COLUMNS, avec réplique Postgres.
	Les rejets dont la date ou le site sont illisibles vont dans la partition "unknown"."""
	if not bad_rows:
		return 0

	rejets = pd.DataFrame(bad_rows, columns=QUARANTINE_COLUMNS)
	stamp = run_stamp()
	partitionne = rejets.assign(
		_record_date=rejets["record_date"].fillna("unknown"),
		_site_id=rejets["site_id"].fillna("unknown"),
	)

	for (record_date, site_id), groupe in partitionne.groupby(["_record_date", "_site_id"], dropna=False):
		key = f"record_date={record_date}/site_id={site_id}/rejects_{stamp}.parquet"
		s3_put_parquet(s3, bucket, key, groupe.drop(columns=["_record_date", "_site_id"]))

	if pg_conn is not None:
		try:
			postgres_writer.write_quarantine(pg_conn, rejets)
		except psycopg2.Error as exc:
			pg_conn.rollback()
			print(f"PostgreSQL : écriture quarantaine échouée, {exc}")

	return len(bad_rows)


def process_batch(
	s3: Any,
	records: list[tuple[str, dict[str, Any]]],
	*,
	silver_bucket: str,
	gold_bucket: str,
	quarantine_bucket: str,
	pg_conn: Any = None,
	rebuild_gold: bool = False,
) -> tuple[int, int, set[tuple[str, str]]]:
	"""records : (clé bronze, mesure décodée).
	Renvoie (lignes_silver, quarantined, partitions touchées).

	rebuild_gold=False par défaut : le gold des partitions renvoyées est recalculé plus tard
	par un passage --gold-only, pour que ce chemin-ci — appelé chaque minute en collecte et à
	chaque tranche pendant le rattrapage — reste borné par la taille du lot et non par celle
	du silver déjà accumulé."""
	silver_rows: list[dict[str, Any]] = []
	quarantine_rows: list[dict[str, Any]] = []

	for source_key, record in records:
		normalized, quarantine_row = normalize_record(record, source_key)
		if normalized is not None:
			silver_rows.append(normalized)
		elif quarantine_row is not None:
			quarantine_rows.append(quarantine_row)

	quarantine_count = write_quarantine(s3, quarantine_bucket, quarantine_rows, pg_conn=pg_conn)

	if not silver_rows:
		return 0, quarantine_count, set()

	silver_df = pd.DataFrame(silver_rows)
	# Force les colonnes numériques en float64 : si un lot ne contient QUE des
	# valeurs nulles (l'API mock injecte des null en rafale), pandas laisserait la
	# colonne en dtype object rempli de None, et .abs()/.mean() en aval planteraient
	# ("bad operand type for abs(): 'NoneType'"). errors="coerce" -> NaN.
	for column in NUMERIC_COLUMNS:
		if column in silver_df.columns:
			silver_df[column] = pd.to_numeric(silver_df[column], errors="coerce")
	silver_df = add_simple_anomalies(silver_df)

	stamp = run_stamp()
	touched_partitions: set[tuple[str, str]] = set()
	for (record_date, site_id), group in silver_df.groupby(["record_date", "site_id"], dropna=False):
		key = f"record_date={record_date}/site_id={site_id}/batch_{stamp}.parquet"
		s3_put_parquet(s3, silver_bucket, key, group)
		touched_partitions.add((str(record_date), str(site_id)))

	if pg_conn is not None:
		try:
			postgres_writer.write_silver(pg_conn, silver_df)
		except psycopg2.Error as exc:
			pg_conn.rollback()
			print(f"PostgreSQL : écriture silver échouée, {exc}")

	# Gold recalculé depuis l'intégralité du silver de chaque partition touchée.
	if rebuild_gold:
		for record_date, site_id in sorted(touched_partitions):
			rebuild_gold_partition(s3, silver_bucket, gold_bucket, record_date, site_id, pg_conn=pg_conn)

	return len(silver_rows), quarantine_count, touched_partitions


@dataclass
class RunResult:
	"""Compteurs d'un passage. `ok=False` + `message` sur les échecs de préparation
	(env MinIO manquant, bucket indisponible, listing bronze impossible)."""
	ok: bool
	objets_bronze: int = 0
	nouveaux: int = 0
	lus: int = 0
	erreurs_lecture: int = 0
	lignes_silver: int = 0
	quarantined: int = 0
	processed_total: int = 0
	gold_en_attente: int = 0
	message: str = ""


@dataclass
class GoldResult:
	"""Compteurs d'un passage gold. `partitions` inclut celles reprises via --gold-date ;
	`echecs` sont restées en file et repasseront."""
	ok: bool
	partitions: int = 0
	recalculees: int = 0
	echecs: int = 0
	message: str = ""


def prepare_s3(args: argparse.Namespace) -> tuple[Any, str]:
	"""Client S3 + buckets de sortie garantis. Renvoie (None, message) sur échec, pour que
	l'appelant décide de son type de retour (RunResult ou GoldResult)."""
	try:
		s3 = storage.get_s3()
	except KeyError as exc:
		return None, f"Variable d'environnement MinIO manquante : {exc}"

	for bucket in (args.silver_bucket, args.gold_bucket, args.quarantine_bucket, args.manifests_bucket):
		try:
			ensure_bucket(s3, bucket)
		except (BotoCoreError, ClientError) as exc:
			return None, f"Bucket '{bucket}' indisponible : {exc}"

	return s3, ""


def run_gold(argv: list[str] | None = None) -> GoldResult:
	"""Recalcule le gold des partitions en attente (et, avec --gold-date, de toute une
	journée). Une partition en échec n'est pas retirée de la file : elle repassera au
	passage suivant plutôt que de laisser un agrégat figé sur un silver déjà plus récent."""
	args = build_parser().parse_args(argv)

	s3, message = prepare_s3(args)
	if s3 is None:
		return GoldResult(ok=False, message=message)

	try:
		partitions = load_pending_gold(s3, args.manifests_bucket, args.pending_gold_key)
	except (BotoCoreError, ClientError) as exc:
		return GoldResult(ok=False, message=f"File des partitions gold illisible : {exc}")

	if args.gold_date:
		try:
			partitions |= partitions_for_date(s3, args.silver_bucket, args.gold_date)
		except (BotoCoreError, ClientError) as exc:
			print(f"Partitions silver du {args.gold_date} illisibles : {exc}")

	if not partitions:
		return GoldResult(ok=True)

	try:
		pg_conn = postgres_writer.make_pg_connection()
	except (KeyError, psycopg2.OperationalError) as exc:
		print(f"PostgreSQL indisponible, écriture désactivée pour ce passage : {exc}")
		pg_conn = None

	rebuilt: set[tuple[str, str]] = set()
	failures = 0
	try:
		for record_date, site_id in sorted(partitions):
			try:
				rebuild_gold_partition(
					s3, args.silver_bucket, args.gold_bucket, record_date, site_id, pg_conn=pg_conn
				)
				rebuilt.add((record_date, site_id))
			# Filet volontairement large : un parquet silver illisible (pyarrow) ne doit pas
			# priver les autres partitions de leur recalcul. Le passage est idempotent, la
			# partition reste en file et repassera.
			except Exception as exc:
				failures += 1
				print(f"Partition gold ({record_date}, {site_id}) en échec, laissée en attente : {exc}")
	finally:
		if pg_conn is not None:
			pg_conn.close()

	remove_pending_gold(s3, args.manifests_bucket, args.pending_gold_key, rebuilt)

	return GoldResult(ok=True, partitions=len(partitions), recalculees=len(rebuilt), echecs=failures)


def run(argv: list[str] | None = None) -> RunResult:
	"""Un passage bronze -> silver. N'imprime que les erreurs de lecture par
	clé (dans fetch_records) ; le résumé et le code de sortie sont l'affaire de
	main(). bootstrap.py appelle run() en boucle et exploite les compteurs
	(objets_bronze, processed_total) pour suivre l'avancement du rattrapage.

	Le gold des partitions touchées est empilé pour un passage --gold-only, sauf
	--with-gold."""
	args = build_parser().parse_args(argv)

	s3, message = prepare_s3(args)
	if s3 is None:
		return RunResult(ok=False, message=message)

	processed = load_state(s3, args.manifests_bucket, args.state_key)

	try:
		all_keys = sorted(
			key for key in s3_list_keys(s3, args.bronze_bucket, args.bronze_prefix) if key.endswith(".json")
		)
	except (BotoCoreError, ClientError) as exc:
		return RunResult(ok=False, message=f"Impossible de lister le bucket bronze '{args.bronze_bucket}' : {exc}")

	new_keys = [key for key in all_keys if key not in processed]
	if args.max_objects > 0:
		new_keys = new_keys[: args.max_objects]

	if not new_keys:
		return RunResult(ok=True, objets_bronze=len(all_keys), nouveaux=0, processed_total=len(processed))

	records, fetch_errors = fetch_records(
		s3, args.bronze_bucket, new_keys, workers=args.fetch_workers,
	)

	try:
		pg_conn = postgres_writer.make_pg_connection()
	except (KeyError, psycopg2.OperationalError) as exc:
		print(f"PostgreSQL indisponible, écriture désactivée pour ce run : {exc}")
		pg_conn = None

	try:
		silver_count, quarantine_count, touched_partitions = process_batch(
			s3,
			records,
			silver_bucket=args.silver_bucket,
			gold_bucket=args.gold_bucket,
			quarantine_bucket=args.quarantine_bucket,
			pg_conn=pg_conn,
			rebuild_gold=args.with_gold,
		)
	finally:
		if pg_conn is not None:
			pg_conn.close()

	pending_count = 0
	if touched_partitions and not args.with_gold:
		try:
			add_pending_gold(s3, args.manifests_bucket, args.pending_gold_key, touched_partitions)
			pending_count = len(touched_partitions)
		except (BotoCoreError, ClientError) as exc:
			# Le silver est déjà écrit : on ne rejoue pas le lot, le recalcul quotidien
			# (--gold-date) rattrapera la partition oubliée.
			print(f"File des partitions gold non mise à jour : {exc}")

	# Un objet lu (même mis en quarantaine) est marqué traité : pas de nouvelle tentative.
	# Les objets dont la lecture S3 a échoué ne sont PAS marqués et repasseront au prochain run.
	processed.update(key for key, _ in records)
	save_state(s3, args.manifests_bucket, args.state_key, processed)

	return RunResult(
		ok=True,
		objets_bronze=len(all_keys),
		nouveaux=len(new_keys),
		lus=len(records),
		erreurs_lecture=fetch_errors,
		lignes_silver=silver_count,
		quarantined=quarantine_count,
		processed_total=len(processed),
		gold_en_attente=pending_count,
	)


def main(argv: list[str] | None = None) -> int:
	if build_parser().parse_args(argv).gold_only:
		gold = run_gold(argv)
		if not gold.ok:
			print(gold.message)
			return 1
		print(
			"Recalcul gold terminé | "
			f"partitions={gold.partitions} | recalculées={gold.recalculees} | échecs={gold.echecs}"
		)
		return 0

	result = run(argv)
	if not result.ok:
		print(result.message)
		return 1

	if result.nouveaux == 0:
		print(
			f"Traitement terminé | objets_bronze={result.objets_bronze} | nouveaux=0 | "
			f"lignes_silver=0 | quarantined=0"
		)
		return 0

	print(
		"Traitement terminé | "
		f"objets_bronze={result.objets_bronze} | nouveaux={result.nouveaux} | "
		f"lus={result.lus} | erreurs_lecture={result.erreurs_lecture} | "
		f"lignes_silver={result.lignes_silver} | quarantined={result.quarantined} | "
		f"gold_en_attente={result.gold_en_attente}"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
