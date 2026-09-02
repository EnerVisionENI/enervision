-- infra/postgres/init/03_gold.sql
-- EV-035 : réplique Postgres de la couche gold (etl/quality.py -> bucket MinIO "gold").
-- Une table par grain (daily / hourly), même découpage que les préfixes MinIO
-- gold/daily/... et gold/hourly/.... Le gold est recalculé en entier à chaque run pour
-- une partition (record_date, site_id) : le loader Postgres fait donc un
-- DELETE + INSERT par partition plutôt qu'un upsert ligne à ligne, pour rester le miroir
-- exact du Parquet (pas d'accumulation de doublons si le nombre de lignes change).

CREATE TABLE IF NOT EXISTS aggregates_gold_daily (
    record_date                DATE NOT NULL,
    site_id                     VARCHAR(20) NOT NULL REFERENCES sites(site_id),
    site_type                   VARCHAR(50),
    records_count               INTEGER NOT NULL DEFAULT 0,
    good_count                  INTEGER NOT NULL DEFAULT 0,
    partial_count               INTEGER NOT NULL DEFAULT 0,
    degraded_count              INTEGER NOT NULL DEFAULT 0,
    missing_consumption_count   INTEGER NOT NULL DEFAULT 0,
    avg_consumption_kw          NUMERIC,
    min_consumption_kw          NUMERIC,
    max_consumption_kw          NUMERIC,
    total_consumption_kwh       NUMERIC,
    avg_voltage_v                NUMERIC,
    avg_current_a                NUMERIC,
    avg_power_factor             NUMERIC,
    avg_temperature_celsius      NUMERIC,
    avg_humidity_percent         NUMERIC,
    avg_quality_score            NUMERIC,
    updated_at                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (record_date, site_id)
);

CREATE TABLE IF NOT EXISTS aggregates_gold_hourly (
    record_date        DATE NOT NULL,
    record_hour        TIMESTAMPTZ NOT NULL,
    site_id            VARCHAR(20) NOT NULL REFERENCES sites(site_id),
    site_type          VARCHAR(50),
    records_count      INTEGER NOT NULL DEFAULT 0,
    avg_consumption_kw NUMERIC,
    max_consumption_kw NUMERIC,
    min_consumption_kw NUMERIC,
    avg_quality_score  NUMERIC,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (record_hour, site_id)
);

CREATE INDEX IF NOT EXISTS idx_gold_daily_site ON aggregates_gold_daily(site_id);
CREATE INDEX IF NOT EXISTS idx_gold_hourly_site ON aggregates_gold_hourly(site_id);
CREATE INDEX IF NOT EXISTS idx_gold_hourly_date ON aggregates_gold_hourly(record_date);
