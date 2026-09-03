-- infra/postgres/init/03_gold.sql
-- EV-035 : réplique Postgres de la couche gold (etl/quality.py -> bucket MinIO "gold").
-- Une table par grain (daily / hourly), même découpage que les préfixes MinIO
-- gold/daily/... et gold/hourly/.... Le gold est recalculé en entier à chaque run pour
-- une partition (record_date, site_id) : le loader Postgres fait donc un
-- DELETE + INSERT par partition plutôt qu'un upsert ligne à ligne, pour rester le miroir
-- exact du Parquet (pas d'accumulation de doublons si le nombre de lignes change).
--
-- Les deux grains portent le MÊME jeu de colonnes. L'horaire n'exposait que records_count,
-- avg/min/max de consommation et avg_quality_score : on ne pouvait pas y distinguer « aucun
-- relevé reçu » de « relevés reçus mais tous vides », alors que c'est le grain sur lequel
-- s'entraînent les modèles de charge.
--
-- Compteurs de couverture : good + partial + degraded + critical + unknown = records_count,
-- sans reste. Les lectures "critical" (network_loss, 7 métriques nulles) n'étaient comptées
-- dans aucun compteur ; elles sont désormais explicites, tout comme empty_count (aucune
-- métrique exploitable) et usable_count (au moins une).
--
-- Les moyennes valent NULL — jamais 0 — quand rien n'a été mesuré, total_consumption_kwh
-- compris : un 0 factice s'apprend comme une consommation nulle réelle.

CREATE TABLE IF NOT EXISTS aggregates_gold_daily (
    record_date                 DATE NOT NULL,
    site_id                     VARCHAR(20) NOT NULL REFERENCES sites(site_id),
    site_type                   VARCHAR(50),
    records_count               INTEGER NOT NULL DEFAULT 0,
    usable_count                INTEGER NOT NULL DEFAULT 0,
    empty_count                 INTEGER NOT NULL DEFAULT 0,
    good_count                  INTEGER NOT NULL DEFAULT 0,
    partial_count               INTEGER NOT NULL DEFAULT 0,
    degraded_count              INTEGER NOT NULL DEFAULT 0,
    critical_count              INTEGER NOT NULL DEFAULT 0,
    unknown_count               INTEGER NOT NULL DEFAULT 0,
    missing_consumption_count   INTEGER NOT NULL DEFAULT 0,
    anomaly_count               INTEGER NOT NULL DEFAULT 0,
    avg_consumption_kw          NUMERIC,
    min_consumption_kw          NUMERIC,
    max_consumption_kw          NUMERIC,
    total_consumption_kwh       NUMERIC,
    avg_voltage_v               NUMERIC,
    avg_current_a               NUMERIC,
    avg_power_factor            NUMERIC,
    avg_temperature_celsius     NUMERIC,
    avg_humidity_percent        NUMERIC,
    avg_quality_score           NUMERIC,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (record_date, site_id)
);

-- Le grain horaire est complété à 24 lignes par (record_date, site_id) : une heure sans
-- relevé existe avec records_count = 0 et des métriques nulles, au lieu d'être une ligne
-- absente qu'un modèle de série temporelle recolle sans le savoir.
CREATE TABLE IF NOT EXISTS aggregates_gold_hourly (
    record_date                 DATE NOT NULL,
    record_hour                 TIMESTAMPTZ NOT NULL,
    site_id                     VARCHAR(20) NOT NULL REFERENCES sites(site_id),
    site_type                   VARCHAR(50),
    records_count               INTEGER NOT NULL DEFAULT 0,
    usable_count                INTEGER NOT NULL DEFAULT 0,
    empty_count                 INTEGER NOT NULL DEFAULT 0,
    good_count                  INTEGER NOT NULL DEFAULT 0,
    partial_count               INTEGER NOT NULL DEFAULT 0,
    degraded_count              INTEGER NOT NULL DEFAULT 0,
    critical_count              INTEGER NOT NULL DEFAULT 0,
    unknown_count               INTEGER NOT NULL DEFAULT 0,
    missing_consumption_count   INTEGER NOT NULL DEFAULT 0,
    anomaly_count               INTEGER NOT NULL DEFAULT 0,
    avg_consumption_kw          NUMERIC,
    min_consumption_kw          NUMERIC,
    max_consumption_kw          NUMERIC,
    total_consumption_kwh       NUMERIC,
    avg_voltage_v               NUMERIC,
    avg_current_a               NUMERIC,
    avg_power_factor            NUMERIC,
    avg_temperature_celsius     NUMERIC,
    avg_humidity_percent        NUMERIC,
    avg_quality_score           NUMERIC,
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (record_hour, site_id)
);

-- Rejouable sur une base déjà initialisée : les scripts de init/ ne tournent qu'à la
-- création du volume, ces ALTER appliquent le nouveau schéma à chaud.
--   docker exec -i ev006-postgres psql -U ev_admin -d ev_monitoring < 03_gold.sql
ALTER TABLE aggregates_gold_daily  ADD COLUMN IF NOT EXISTS usable_count   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_daily  ADD COLUMN IF NOT EXISTS empty_count    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_daily  ADD COLUMN IF NOT EXISTS critical_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_daily  ADD COLUMN IF NOT EXISTS unknown_count  INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_daily  ADD COLUMN IF NOT EXISTS anomaly_count  INTEGER NOT NULL DEFAULT 0;

ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS usable_count              INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS empty_count               INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS good_count                INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS partial_count             INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS degraded_count            INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS critical_count            INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS unknown_count             INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS missing_consumption_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS anomaly_count             INTEGER NOT NULL DEFAULT 0;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS total_consumption_kwh     NUMERIC;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS avg_voltage_v             NUMERIC;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS avg_current_a             NUMERIC;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS avg_power_factor          NUMERIC;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS avg_temperature_celsius   NUMERIC;
ALTER TABLE aggregates_gold_hourly ADD COLUMN IF NOT EXISTS avg_humidity_percent      NUMERIC;

CREATE INDEX IF NOT EXISTS idx_gold_daily_site ON aggregates_gold_daily(site_id);
CREATE INDEX IF NOT EXISTS idx_gold_hourly_site ON aggregates_gold_hourly(site_id);
CREATE INDEX IF NOT EXISTS idx_gold_hourly_date ON aggregates_gold_hourly(record_date);
-- Repérage des fenêtres à rejouer par etl/repair.py : les partitions sans aucune mesure.
CREATE INDEX IF NOT EXISTS idx_gold_daily_vides ON aggregates_gold_daily(record_date, site_id) WHERE usable_count = 0;
