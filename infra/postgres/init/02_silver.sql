-- infra/postgres/init/02_silver.sql
-- EV-035 : réplique Postgres de la couche silver (etl/quality.py -> bucket MinIO "silver").
-- MinIO/Parquet reste la source de vérité ; cette table est une copie interrogeable en SQL
-- pour l'API/dashboard. Une ligne par mesure normalisée, clé = la clé de l'objet bronze
-- d'origine (déjà unique par site + timestamp), ce qui rend le rejeu de l'ETL idempotent.

CREATE TABLE IF NOT EXISTS measurements_silver (
    source_key              VARCHAR(255) PRIMARY KEY,
    "timestamp"              TIMESTAMPTZ NOT NULL,
    site_id                  VARCHAR(20) NOT NULL REFERENCES sites(site_id),
    site_type                VARCHAR(50),
    consumption_kw           NUMERIC,
    consumption_kwh          NUMERIC,
    voltage_v                NUMERIC,
    current_a                NUMERIC,
    power_factor             NUMERIC,
    temperature_celsius      NUMERIC,
    humidity_percent         NUMERIC,
    null_reasons             JSONB NOT NULL DEFAULT '[]',
    data_quality             VARCHAR(20),
    ingested_at              TIMESTAMPTZ NOT NULL,
    record_date              DATE NOT NULL,
    record_hour              TIMESTAMPTZ NOT NULL,
    missing_fields           JSONB NOT NULL DEFAULT '[]',
    quality_score            SMALLINT,
    is_valid                 BOOLEAN NOT NULL DEFAULT true,
    has_anomaly              BOOLEAN NOT NULL DEFAULT false,
    consumption_change_pct   NUMERIC
);

CREATE INDEX IF NOT EXISTS idx_measurements_silver_site_date ON measurements_silver(site_id, record_date);
CREATE INDEX IF NOT EXISTS idx_measurements_silver_record_hour ON measurements_silver(record_hour);
CREATE INDEX IF NOT EXISTS idx_measurements_silver_anomaly ON measurements_silver(has_anomaly) WHERE has_anomaly;
