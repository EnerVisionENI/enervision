"""Bronze -> Silver/Gold ETL for EnerVision.

This script reads append-only JSONL bronze files, validates and normalizes
records, writes cleaned silver batches as Parquet, generates gold aggregates,
and stores malformed rows in quarantine.

Output layout (defaults):
	bronze/      raw JSONL files
	silver/      cleaned Parquet batches
	gold/        aggregated Parquet batches
	quarantine/  invalid JSON or schema failures
	manifests/   incremental processing state

Usage:
	python quality.py
	python quality.py --bronze-dir bronze --silver-dir silver --gold-dir gold
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


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


@dataclass(slots=True)
class FileState:
	offset: int = 0
	size: int = 0
	mtime: float = 0.0
	last_processed_at: str | None = None


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(description="Process EnerVision bronze JSONL files.")
	parser.add_argument("--bronze-dir", default=os.environ.get("DOSSIER_BRONZE", "bronze"))
	parser.add_argument("--silver-dir", default=os.environ.get("DOSSIER_SILVER", "silver"))
	parser.add_argument("--gold-dir", default=os.environ.get("DOSSIER_GOLD", "gold"))
	parser.add_argument(
		"--quarantine-dir",
		default=os.environ.get("DOSSIER_QUARANTINE", "quarantine"),
	)
	parser.add_argument(
		"--manifest-dir",
		default=os.environ.get("DOSSIER_MANIFESTS", "manifests"),
	)
	parser.add_argument(
		"--state-file",
		default=os.environ.get("ETL_STATE_FILE", "manifests/etl_state.json"),
	)
	return parser


def utc_now_iso() -> str:
	return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> None:
	path.mkdir(parents=True, exist_ok=True)


def load_state(path: Path) -> dict[str, FileState]:
	if not path.exists():
		return {}
	try:
		raw = json.loads(path.read_text(encoding="utf-8"))
	except json.JSONDecodeError:
		return {}

	state: dict[str, FileState] = {}
	for key, value in raw.items():
		try:
			state[key] = FileState(
				offset=int(value.get("offset", 0)),
				size=int(value.get("size", 0)),
				mtime=float(value.get("mtime", 0.0)),
				last_processed_at=value.get("last_processed_at"),
			)
		except (TypeError, ValueError):
			continue
	return state


def save_state(path: Path, state: dict[str, FileState]) -> None:
	ensure_dir(path.parent)
	payload = {key: asdict(value) for key, value in sorted(state.items())}
	tmp_path = path.with_suffix(path.suffix + ".tmp")
	tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
	tmp_path.replace(path)


def discover_bronze_files(bronze_dir: Path) -> list[Path]:
	if not bronze_dir.exists():
		return []
	return sorted(path for path in bronze_dir.rglob("*.jsonl") if path.is_file())


def read_jsonl_batch(path: Path, start_offset: int) -> tuple[list[dict[str, Any]], int]:
	records: list[dict[str, Any]] = []
	with path.open("rb") as handle:
		if start_offset > 0:
			handle.seek(start_offset)

		while True:
			line_start = handle.tell()
			raw = handle.readline()
			if not raw:
				break

			try:
				text = raw.decode("utf-8").strip()
			except UnicodeDecodeError as exc:
				records.append(
					{
						"_parse_error": str(exc),
						"_raw_line": raw.decode("utf-8", errors="replace").strip(),
						"_line_offset": line_start,
					}
				)
				continue

			if not text:
				continue

			try:
				payload = json.loads(text)
			except json.JSONDecodeError as exc:
				records.append(
					{
						"_parse_error": f"JSONDecodeError: {exc.msg}",
						"_raw_line": text,
						"_line_offset": line_start,
					}
				)
				continue

			payload["_line_offset"] = line_start
			records.append(payload)

	return records, path.stat().st_size


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
	source_file: Path,
	source_offset: int,
	source_mtime: float,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
	if record.get("_parse_error"):
		return None, {
			"source_file": str(source_file),
			"source_offset": source_offset,
			"error_type": "parse_error",
			"error_message": record["_parse_error"],
			"raw_line": record.get("_raw_line"),
			"captured_at": utc_now_iso(),
		}

	missing_mandatory = [column for column in MANDATORY_COLUMNS if record.get(column) in (None, "")]
	if missing_mandatory:
		return None, {
			"source_file": str(source_file),
			"source_offset": source_offset,
			"error_type": "missing_mandatory_fields",
			"error_message": ", ".join(missing_mandatory),
			"raw_record": record,
			"captured_at": utc_now_iso(),
		}

	timestamp = parse_timestamp(record.get("timestamp"))
	if timestamp is None:
		return None, {
			"source_file": str(source_file),
			"source_offset": source_offset,
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
		"source_file": str(source_file),
		"source_offset": source_offset,
		"source_mtime": pd.to_datetime(source_mtime, unit="s", utc=True),
		"ingested_at": pd.Timestamp.now(tz="UTC"),
	}
	normalized["record_date"] = normalized["timestamp"].date().isoformat()
	normalized["record_hour"] = normalized["timestamp"].floor("h")
	normalized["missing_fields"] = [column for column in NUMERIC_COLUMNS if normalized.get(column) is None]
	normalized["quality_score"] = compute_quality_score(normalized)
	normalized["is_valid"] = True
	normalized["has_anomaly"] = False

	return normalized, None


def write_parquet(df: pd.DataFrame, output_path: Path) -> None:
	ensure_dir(output_path.parent)
	df.to_parquet(output_path, index=False)


def write_parquet_overwrite(df: pd.DataFrame, output_path: Path) -> None:
	"""Ecrit un fichier Parquet en écrasant l'existant, via un tmp + rename atomique
	pour qu'un lecteur ne voie jamais un fichier à moitié écrit."""
	ensure_dir(output_path.parent)
	tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
	df.to_parquet(tmp_path, index=False)
	tmp_path.replace(output_path)


def build_silver_output_dir(root: Path, row: pd.Series) -> Path:
	return root / f"record_date={row['record_date']}" / f"site_id={row['site_id']}"


def build_gold_output_dir(root: Path, row: pd.Series, grain: str) -> Path:
	return root / f"grain={grain}" / f"record_date={row['record_date']}" / f"site_id={row['site_id']}"


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
	silver_root: Path,
	gold_root: Path,
	record_date: Any,
	site_id: Any,
) -> None:
	"""Recalcule le gold daily/hourly d'une partition (record_date, site_id) à partir
	de TOUS les batches silver de cette partition, puis écrit un seul fichier par grain
	(écrasé à chaque passage). Le gold reste donc toujours un agrégat complet et unique,
	même si le silver reste en micro-batches append-only."""
	partition_dir = silver_root / f"record_date={record_date}" / f"site_id={site_id}"
	silver_files = sorted(partition_dir.glob("*.parquet"))
	if not silver_files:
		return

	partition_df = pd.concat(
		(pd.read_parquet(path) for path in silver_files),
		ignore_index=True,
	)

	daily_dir = gold_root / "daily" / f"record_date={record_date}" / f"site_id={site_id}"
	hourly_dir = gold_root / "hourly" / f"record_date={record_date}" / f"site_id={site_id}"

	# Purge des anciens fichiers batch (daily_*.parquet / hourly_*.parquet) éventuels,
	# pour garantir exactement un fichier par partition.
	for stale in daily_dir.glob("*.parquet"):
		stale.unlink()
	for stale in hourly_dir.glob("*.parquet"):
		stale.unlink()

	write_parquet_overwrite(aggregate_daily(partition_df), daily_dir / "daily.parquet")
	write_parquet_overwrite(aggregate_hourly(partition_df), hourly_dir / "hourly.parquet")


def quarantine_file_path(quarantine_root: Path, source_file: Path) -> Path:
	relative = source_file.as_posix().replace("/", "_")
	return quarantine_root / f"{relative}.jsonl"


def append_quarantine(quarantine_root: Path, source_file: Path, bad_rows: Iterable[dict[str, Any]]) -> int:
	bad_rows = list(bad_rows)
	if not bad_rows:
		return 0

	ensure_dir(quarantine_root)
	path = quarantine_file_path(quarantine_root, source_file)
	with path.open("a", encoding="utf-8") as handle:
		for row in bad_rows:
			handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
	return len(bad_rows)


def process_file(
	source_file: Path,
	bronze_root: Path,
	silver_root: Path,
	gold_root: Path,
	quarantine_root: Path,
	state: dict[str, FileState],
) -> tuple[int, int, int]:
	relative_key = source_file.relative_to(bronze_root).as_posix()
	file_stat = source_file.stat()
	current_state = state.get(relative_key, FileState())
	start_offset = current_state.offset if file_stat.st_size >= current_state.offset else 0

	raw_rows, current_size = read_jsonl_batch(source_file, start_offset)
	if not raw_rows:
		state[relative_key] = FileState(
			offset=current_size,
			size=current_size,
			mtime=file_stat.st_mtime,
			last_processed_at=current_state.last_processed_at,
		)
		return 0, 0, 0

	silver_rows: list[dict[str, Any]] = []
	quarantine_rows: list[dict[str, Any]] = []

	for row in raw_rows:
		source_offset = int(row.get("_line_offset", start_offset))
		normalized, quarantine_row = normalize_record(row, source_file, source_offset, file_stat.st_mtime)
		if normalized is not None:
			silver_rows.append(normalized)
		elif quarantine_row is not None:
			quarantine_rows.append(quarantine_row)

	if silver_rows:
		silver_df = pd.DataFrame(silver_rows)
		silver_df = add_simple_anomalies(silver_df)

		touched_partitions: set[tuple[Any, Any]] = set()
		for (record_date, site_id), group in silver_df.groupby(["record_date", "site_id"], dropna=False):
			output_dir = silver_root / f"record_date={record_date}" / f"site_id={site_id}"
			batch_name = f"{source_file.stem}_{start_offset}_{current_size}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.parquet"
			write_parquet(group, output_dir / batch_name)
			touched_partitions.add((record_date, site_id))

		# Gold recalculé depuis l'intégralité du silver de chaque partition touchée :
		# un seul daily.parquet / hourly.parquet par (record_date, site_id), toujours à jour.
		for record_date, site_id in sorted(touched_partitions, key=lambda item: (str(item[0]), str(item[1]))):
			rebuild_gold_partition(silver_root, gold_root, record_date, site_id)

	quarantine_count = append_quarantine(quarantine_root, source_file, quarantine_rows)

	state[relative_key] = FileState(
		offset=current_size,
		size=current_size,
		mtime=file_stat.st_mtime,
		last_processed_at=utc_now_iso(),
	)
	return len(silver_rows), quarantine_count, len(raw_rows)


def main() -> int:
	parser = build_parser()
	args = parser.parse_args()

	bronze_root = Path(args.bronze_dir)
	silver_root = Path(args.silver_dir)
	gold_root = Path(args.gold_dir)
	quarantine_root = Path(args.quarantine_dir)
	manifest_dir = Path(args.manifest_dir)
	state_file = Path(args.state_file)

	ensure_dir(silver_root)
	ensure_dir(gold_root)
	ensure_dir(quarantine_root)
	ensure_dir(manifest_dir)

	state = load_state(state_file)
	files = discover_bronze_files(bronze_root)

	if not files:
		print(f"Aucun fichier bronze trouvé dans {bronze_root}")
		return 0

	total_silver = 0
	total_quarantine = 0
	total_raw = 0

	for source_file in files:
		silver_count, quarantine_count, raw_count = process_file(
			source_file=source_file,
			bronze_root=bronze_root,
			silver_root=silver_root,
			gold_root=gold_root,
			quarantine_root=quarantine_root,
			state=state,
		)
		total_silver += silver_count
		total_quarantine += quarantine_count
		total_raw += raw_count

	save_state(state_file, state)

	print(
		"Traitement terminé | "
		f"fichiers={len(files)} | lignes_lues={total_raw} | "
		f"lignes_silver={total_silver} | quarantined={total_quarantine}"
	)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
