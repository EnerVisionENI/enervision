"""Bronze -> Silver/Gold ETL for EnerVision, 100 % sur MinIO (S3-compatible).

Lit les objets de mesure bruts du bucket bronze (un objet JSON par mesure, écrit par
collect.py), valide et normalise, écrit des batches silver en Parquet, reconstruit les
agrégats gold daily/hourly, et pousse les enregistrements invalides en quarantaine.
Aucune donnée n'est écrite sur disque : buckets uniquement.

Buckets :
	bronze       objets bruts        {site_id}/{YYYY-MM-DD}/{HHMMSS}.json
	silver       batches nettoyés    record_date=…/site_id=…/batch_*.parquet
	gold         agrégats            daily|hourly/record_date=…/site_id=…/*.parquet
	quarantine   rejets              {YYYY-MM-DD}/{source_key}__{stamp}.json  (1 objet / rejet)
	manifests    état incrémental    etl_state.json  (liste des clés bronze déjà traitées)

Environment :
	MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY          (obligatoires)
	MINIO_BUCKET_BRONZE / _SILVER / _GOLD / _QUARANTINE / _MANIFESTS  (défauts homonymes)
	MINIO_USE_SSL                                              (défaut "false")

Usage :
	python quality.py
	python quality.py --bronze-bucket bronze --silver-bucket silver --gold-bucket gold
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
from datetime import datetime, timezone
from typing import Any

import boto3
import pandas as pd
from botocore.client import Config
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

PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
JSON_CONTENT_TYPE = "application/json"


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="EnerVision ETL bronze -> silver/gold, 100 % MinIO.")
	parser.add_argument("--bronze-bucket", default=os.environ.get("MINIO_BUCKET_BRONZE", "bronze"))
	parser.add_argument("--bronze-prefix", default=os.environ.get("MINIO_PREFIX_BRONZE", ""))
	parser.add_argument("--silver-bucket", default=os.environ.get("MINIO_BUCKET_SILVER", "silver"))
	parser.add_argument("--gold-bucket", default=os.environ.get("MINIO_BUCKET_GOLD", "gold"))
	parser.add_argument("--quarantine-bucket", default=os.environ.get("MINIO_BUCKET_QUARANTINE", "quarantine"))
	parser.add_argument("--manifests-bucket", default=os.environ.get("MINIO_BUCKET_MANIFESTS", "manifests"))
	parser.add_argument("--state-key", default=os.environ.get("ETL_STATE_KEY", "etl_state.json"))
	parser.add_argument(
		"--max-objects",
		type=int,
		default=int(os.environ.get("ETL_MAX_OBJECTS", "0")),
		help="Nombre max d'objets bronze traités par exécution (0 = pas de limite).",
	)
	return parser


def utc_now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def run_stamp() -> str:
	return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")


# --------------------------------------------------------------------------- S3

def make_s3_client() -> Any:
	"""Client S3 pointé sur MinIO, même configuration que collect.py."""
	endpoint = os.environ["MINIO_ENDPOINT"]
	use_ssl = os.environ.get("MINIO_USE_SSL", "false").lower() == "true"
	return boto3.client(
		"s3",
		endpoint_url=f"{'https' if use_ssl else 'http'}://{endpoint}",
		aws_access_key_id=os.environ["MINIO_ACCESS_KEY"],
		aws_secret_access_key=os.environ["MINIO_SECRET_KEY"],
		config=Config(signature_version="s3v4"),
		region_name="us-east-1",
	)


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
		if exc.response.get("Error", {}).get("Code") not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
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
	s3_put_bytes(s3, bucket, key, json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"), JSON_CONTENT_TYPE)


# ------------------------------------------------------------------ bronze read

def fetch_record(s3: Any, bucket: str, key: str) -> dict[str, Any]:
	"""Télécharge un objet bronze et le décode en dict de mesure.
	Renvoie {"_parse_error": ..., "_raw_line": ...} si le contenu n'est pas du JSON objet valide."""
	body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
	try:
		text = body.decode("utf-8")
	except UnicodeDecodeError as exc:
		return {"_parse_error": f"UnicodeDecodeError: {exc}", "_raw_line": body.decode("utf-8", errors="replace")}

	try:
		payload = json.loads(text)
	except json.JSONDecodeError as exc:
		return {"_parse_error": f"JSONDecodeError: {exc.msg}", "_raw_line": text}

	if not isinstance(payload, dict):
		return {"_parse_error": "objet JSON inattendu (pas un dict)", "_raw_line": text}
	return payload


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
	if source_quality == "good":
		score += 0
	elif source_quality == "partial":
		score -= 10
	elif source_quality == "degraded":
		score -= 25
	else:
		score -= 5

	missing_optional = sum(1 for column in OPTIONAL_NUMERIC_COLUMNS if row.get(column) is None)
	score -= missing_optional * 4

	if row.get("consumption_kw") is None:
		score -= 20

	if not row.get("timestamp"):
		score -= 30

	if not row.get("site_id"):
		score -= 30

	return max(0, min(100, score))


def normalize_record(
	record: dict[str, Any],
	source_key: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
	if record.get("_parse_error"):
		return None, {
			"source_key": source_key,
			"error_type": "parse_error",
			"error_message": record["_parse_error"],
			"raw_line": record.get("_raw_line"),
			"captured_at": utc_now_iso(),
		}

	missing_mandatory = [column for column in MANDATORY_COLUMNS if record.get(column) in (None, "")]
	if missing_mandatory:
		return None, {
			"source_key": source_key,
			"error_type": "missing_mandatory_fields",
			"error_message": ", ".join(missing_mandatory),
			"raw_record": record,
			"captured_at": utc_now_iso(),
		}

	timestamp = parse_timestamp(record.get("timestamp"))
	if timestamp is None:
		return None, {
			"source_key": source_key,
			"error_type": "invalid_timestamp",
			"error_message": f"Invalid timestamp: {record.get('timestamp')}",
			"raw_record": record,
			"captured_at": utc_now_iso(),
		}

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
	normalized["quality_score"] = compute_quality_score(normalized)
	normalized["is_valid"] = True
	normalized["has_anomaly"] = False

	return normalized, None


# -------------------------------------------------------------------- agrégats

def aggregate_daily(df: pd.DataFrame) -> pd.DataFrame:
	daily = (
		df.assign(
			is_good=df["data_quality"].astype(str).str.lower().eq("good"),
			is_partial=df["data_quality"].astype(str).str.lower().eq("partial"),
			is_degraded=df["data_quality"].astype(str).str.lower().eq("degraded"),
			missing_consumption=df["consumption_kw"].isna(),
		)
		.groupby(["record_date", "site_id", "site_type"], dropna=False)
		.agg(
			records_count=("site_id", "size"),
			good_count=("is_good", "sum"),
			partial_count=("is_partial", "sum"),
			degraded_count=("is_degraded", "sum"),
			missing_consumption_count=("missing_consumption", "sum"),
			avg_consumption_kw=("consumption_kw", "mean"),
			min_consumption_kw=("consumption_kw", "min"),
			max_consumption_kw=("consumption_kw", "max"),
			total_consumption_kwh=("consumption_kwh", "sum"),
			avg_voltage_v=("voltage_v", "mean"),
			avg_current_a=("current_a", "mean"),
			avg_power_factor=("power_factor", "mean"),
			avg_temperature_celsius=("temperature_celsius", "mean"),
			avg_humidity_percent=("humidity_percent", "mean"),
			avg_quality_score=("quality_score", "mean"),
		)
		.reset_index()
	)
	return daily


def aggregate_hourly(df: pd.DataFrame) -> pd.DataFrame:
	hourly = (
		df.assign(record_hour=df["record_hour"].dt.strftime("%Y-%m-%dT%H:00:00Z"))
		.groupby(["record_date", "record_hour", "site_id", "site_type"], dropna=False)
		.agg(
			records_count=("site_id", "size"),
			avg_consumption_kw=("consumption_kw", "mean"),
			max_consumption_kw=("consumption_kw", "max"),
			min_consumption_kw=("consumption_kw", "min"),
			avg_quality_score=("quality_score", "mean"),
		)
		.reset_index()
	)
	return hourly


def add_simple_anomalies(df: pd.DataFrame) -> pd.DataFrame:
	if df.empty:
		return df

	df = df.sort_values(["site_id", "timestamp"]).copy()
	df["prev_consumption_kw"] = df.groupby("site_id")["consumption_kw"].shift(1)
	prev_abs = df["prev_consumption_kw"].abs().replace(0, pd.NA)
	df["consumption_change_pct"] = (df["consumption_kw"] - df["prev_consumption_kw"]).abs() / prev_abs
	df.loc[df["prev_consumption_kw"].isna(), "consumption_change_pct"] = pd.NA

	site_stats = df.groupby("site_id")["consumption_kw"].agg(["mean", "std"]).rename(columns={"mean": "site_mean", "std": "site_std"})
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

	s3_put_parquet(
		s3, gold_bucket,
		f"daily/record_date={record_date}/site_id={site_id}/daily.parquet",
		aggregate_daily(partition_df),
	)
	s3_put_parquet(
		s3, gold_bucket,
		f"hourly/record_date={record_date}/site_id={site_id}/hourly.parquet",
		aggregate_hourly(partition_df),
	)


def write_quarantine(s3: Any, bucket: str, bad_rows: list[dict[str, Any]]) -> int:
	"""Un objet JSON par enregistrement rejeté (S3 n'a pas d'append) :
	{YYYY-MM-DD}/{source_key aplati}__{stamp}_{i}.json"""
	if not bad_rows:
		return 0

	day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
	stamp = run_stamp()
	for index, row in enumerate(bad_rows):
		flat_source = str(row.get("source_key", "unknown")).replace("/", "_")
		key = f"{day}/{flat_source}__{stamp}_{index}.json"
		s3_put_bytes(
			s3, bucket, key,
			json.dumps(row, ensure_ascii=False, default=str).encode("utf-8"),
			JSON_CONTENT_TYPE,
		)
	return len(bad_rows)


def process_batch(
	s3: Any,
	records: list[tuple[str, dict[str, Any]]],
	*,
	silver_bucket: str,
	gold_bucket: str,
	quarantine_bucket: str,
) -> tuple[int, int]:
	"""records : (clé bronze, mesure décodée). Renvoie (lignes_silver, quarantined)."""
	silver_rows: list[dict[str, Any]] = []
	quarantine_rows: list[dict[str, Any]] = []

	for source_key, record in records:
		normalized, quarantine_row = normalize_record(record, source_key)
		if normalized is not None:
			silver_rows.append(normalized)
		elif quarantine_row is not None:
			quarantine_rows.append(quarantine_row)

	quarantine_count = write_quarantine(s3, quarantine_bucket, quarantine_rows)

	if not silver_rows:
		return 0, quarantine_count

	silver_df = pd.DataFrame(silver_rows)
	silver_df = add_simple_anomalies(silver_df)

	stamp = run_stamp()
	touched_partitions: set[tuple[Any, Any]] = set()
	for (record_date, site_id), group in silver_df.groupby(["record_date", "site_id"], dropna=False):
		key = f"record_date={record_date}/site_id={site_id}/batch_{stamp}.parquet"
		s3_put_parquet(s3, silver_bucket, key, group)
		touched_partitions.add((record_date, site_id))

	# Gold recalculé depuis l'intégralité du silver de chaque partition touchée.
	for record_date, site_id in sorted(touched_partitions, key=lambda item: (str(item[0]), str(item[1]))):
		rebuild_gold_partition(s3, silver_bucket, gold_bucket, record_date, site_id)

	return len(silver_rows), quarantine_count


def main() -> int:
	args = build_parser().parse_args()

	try:
		s3 = make_s3_client()
	except KeyError as exc:
		print(f"Variable d'environnement MinIO manquante : {exc}")
		return 1

	for bucket in (args.silver_bucket, args.gold_bucket, args.quarantine_bucket, args.manifests_bucket):
		try:
			ensure_bucket(s3, bucket)
		except (BotoCoreError, ClientError) as exc:
			print(f"Bucket '{bucket}' indisponible : {exc}")
			return 1

	processed = load_state(s3, args.manifests_bucket, args.state_key)

	try:
		all_keys = sorted(
			key for key in s3_list_keys(s3, args.bronze_bucket, args.bronze_prefix) if key.endswith(".json")
		)
	except (BotoCoreError, ClientError) as exc:
		print(f"Impossible de lister le bucket bronze '{args.bronze_bucket}' : {exc}")
		return 1

	new_keys = [key for key in all_keys if key not in processed]
	if args.max_objects > 0:
		new_keys = new_keys[: args.max_objects]

	if not new_keys:
		print(
			f"Traitement terminé | objets_bronze={len(all_keys)} | nouveaux=0 | "
			f"lignes_silver=0 | quarantined=0"
		)
		return 0

	records: list[tuple[str, dict[str, Any]]] = []
	fetch_errors = 0
	for key in new_keys:
		try:
			records.append((key, fetch_record(s3, args.bronze_bucket, key)))
		except (BotoCoreError, ClientError) as exc:
			fetch_errors += 1
			print(f"{key} : lecture impossible, {exc}")

	silver_count, quarantine_count = process_batch(
		s3,
		records,
		silver_bucket=args.silver_bucket,
		gold_bucket=args.gold_bucket,
		quarantine_bucket=args.quarantine_bucket,
	)

	# Un objet lu (même mis en quarantaine) est marqué traité : pas de nouvelle tentative.
	# Les objets dont la lecture S3 a échoué ne sont PAS marqués et repasseront au prochain run.
	processed.update(key for key, _ in records)
	save_state(s3, args.manifests_bucket, args.state_key, processed)

	print(
		"Traitement terminé | "
		f"objets_bronze={len(all_keys)} | nouveaux={len(new_keys)} | "
		f"lus={len(records)} | erreurs_lecture={fetch_errors} | "
		f"lignes_silver={silver_count} | quarantined={quarantine_count}"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
