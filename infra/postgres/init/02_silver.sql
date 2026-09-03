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
    capacity_kw              NUMERIC,          -- métadonnée du site (table sites), copiée ici
    load_percent             NUMERIC,          -- consumption_kw / capacity_kw * 100
    null_reasons             JSONB NOT NULL DEFAULT '[]',
    data_quality             VARCHAR(20),
    ingested_at              TIMESTAMPTZ NOT NULL,
    record_date              DATE NOT NULL,
    record_hour              TIMESTAMPTZ NOT NULL,
    missing_fields           JSONB NOT NULL DEFAULT '[]',
    usable_metrics_count     SMALLINT NOT NULL DEFAULT 0,
    quality_score            SMALLINT,
    is_valid                 BOOLEAN NOT NULL DEFAULT true,
    has_anomaly              BOOLEAN NOT NULL DEFAULT false,
    consumption_change_pct   NUMERIC
);

-- Rejouable sur une base déjà initialisée : les scripts de init/ ne tournent qu'à la
-- création du volume, ces ALTER appliquent le nouveau schéma à chaud.
--   docker exec -i ev006-postgres psql -U ev_admin -d ev_monitoring < 02_silver.sql
-- usable_metrics_count : nombre de métriques réellement mesurées sur les 7 possibles.
-- is_valid vaut désormais (usable_metrics_count > 0) : une lecture critical / network_loss
-- est conservée — le trou doit rester daté — mais n'est pas comptée comme une mesure.
-- load_percent : seul indicateur de charge comparable d'un site à l'autre.
ALTER TABLE measurements_silver ADD COLUMN IF NOT EXISTS usable_metrics_count SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE measurements_silver ADD COLUMN IF NOT EXISTS capacity_kw  NUMERIC;
ALTER TABLE measurements_silver ADD COLUMN IF NOT EXISTS load_percent NUMERIC;

CREATE INDEX IF NOT EXISTS idx_measurements_silver_site_date ON measurements_silver(site_id, record_date);
CREATE INDEX IF NOT EXISTS idx_measurements_silver_record_hour ON measurements_silver(record_hour);
CREATE INDEX IF NOT EXISTS idx_measurements_silver_anomaly ON measurements_silver(has_anomaly) WHERE has_anomaly;
-- Sélection du jeu d'entraînement : « toutes les mesures réellement exploitables ».
CREATE INDEX IF NOT EXISTS idx_measurements_silver_valid ON measurements_silver(site_id, record_hour) WHERE is_valid;
