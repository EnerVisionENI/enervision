-- infra/postgres/init/06_predictions.sql
-- Prévisions de consommation produites par ml/predict.py : inférence batch des modèles du
-- registry MLflow (un modèle Staging par site, cf. ml/scripts/train_v1_csv.py).
--
-- Une ligne = une valeur prédite pour un site à un instant donné, et une seule : la clé
-- (site_id, target_ts, step_minutes) garantit qu'un lecteur n'a jamais à choisir entre
-- plusieurs lignes concurrentes pour la même heure, ni à filtrer sur predicted_at.
--
-- predict.py fait DELETE + INSERT de tout target_ts >= début de son horizon (voir
-- ml/core/postgres_store.write_predictions), comme le loader gold recalcule une partition
-- entière plutôt que d'empiler des upserts : le futur est donc toujours exactement la dernière
-- inférence, y compris quand l'horizon raccourcit d'un run à l'autre.
--
-- Les heures déjà passées ne sont jamais réécrites : chaque ligne reste la prévision réellement
-- émise pour cette heure-là, avec la version de modèle qui l'a produite. C'est ce qui permettra
-- de comparer prévu et réalisé (jointure sur aggregates_gold_hourly) sans table supplémentaire.
-- Le volume est négligeable — 7 sites x 24 h, ~61 k lignes par an — mais rien ne purge cette
-- accumulation aujourd'hui : à revoir si le nombre de sites change d'ordre de grandeur.
--
-- step_minutes est dans la clé alors que tous les modèles V1 sont horaires : ces modèles sont
-- calendaires (C(time_of_week), 168 créneaux par semaine, voir ml/models/towt.py), donc
-- structurellement incapables de descendre sous l'heure — 10h00 et 10h50 tombent sur le même
-- coefficient. Un futur modèle au quart d'heure entraîné sur le silver (grain minute)
-- cohabitera ici sans migration, et un lecteur saura toujours quel pas de temps il lit au lieu
-- de le supposer.
--
-- model_version / run_id / data_source sont portés par chaque ligne, pas déduits ailleurs :
-- les modèles V1 sont entraînés sur CSV synthétique (data_source = 'csv_synthetic', tag
-- 'caveat' des runs MLflow, MAPE 7-44 % contre le réel) et personne ne doit pouvoir prendre
-- ces valeurs pour des prévisions validées sur donnée réelle.
--
-- lower_90 / upper_90 = predicted_kwh ± conformal_margin_90, marge lue sur le run qui a
-- produit le modèle (calibrée sur un jeu jamais vu à l'entraînement, voir
-- ml/core/evaluation.conformal_margin). NULL si le run ne porte pas la métrique — jamais égal
-- à predicted_kwh, ce qui se lirait comme un intervalle de largeur nulle.
--
-- temperature_celsius / temperature_source : l'entrée météo réellement utilisée est conservée.
-- C'est la seule variable non calendaire des modèles, et elle est estimée (climatologie du
-- gold) et non mesurée — sans la stocker, une prévision fausse est impossible à expliquer
-- après coup.

CREATE TABLE IF NOT EXISTS predictions_forecast (
    site_id             VARCHAR(20)  NOT NULL REFERENCES sites(site_id),
    target_ts           TIMESTAMPTZ  NOT NULL,
    step_minutes        SMALLINT     NOT NULL DEFAULT 60,
    predicted_kwh       NUMERIC      NOT NULL,
    lower_90            NUMERIC,
    upper_90            NUMERIC,
    model_name          VARCHAR(100) NOT NULL,
    model_version       VARCHAR(10)  NOT NULL,
    model_stage         VARCHAR(20),
    champion            VARCHAR(20),
    run_id              VARCHAR(32),
    data_source         VARCHAR(30),
    temperature_celsius NUMERIC,
    temperature_source  VARCHAR(30),
    horizon_h           SMALLINT,
    predicted_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (site_id, target_ts, step_minutes)
);

-- Lecture principale du dashboard : la courbe d'un site sur une fenêtre.
CREATE INDEX IF NOT EXISTS idx_predictions_site_ts ON predictions_forecast(site_id, target_ts);
-- Lecture transverse : tous les sites à un instant donné (vue de parc).
CREATE INDEX IF NOT EXISTS idx_predictions_ts ON predictions_forecast(target_ts);
-- Supervision : « à quand remonte la dernière inférence », sans scan de toute la table.
CREATE INDEX IF NOT EXISTS idx_predictions_predicted_at ON predictions_forecast(predicted_at);

-- Rejouable sur une base déjà initialisée : les scripts de init/ ne tournent qu'à la création
-- du volume (infra_pgdata est réutilisé depuis l'ancien projet Compose), ces ALTER appliquent
-- le schéma à chaud.
--   docker exec -i ev006-postgres psql -U ev_admin -d ev_monitoring < 06_predictions.sql
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS step_minutes        SMALLINT NOT NULL DEFAULT 60;
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS lower_90            NUMERIC;
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS upper_90            NUMERIC;
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS model_stage         VARCHAR(20);
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS champion            VARCHAR(20);
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS run_id              VARCHAR(32);
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS data_source         VARCHAR(30);
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS temperature_celsius NUMERIC;
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS temperature_source  VARCHAR(30);
ALTER TABLE predictions_forecast ADD COLUMN IF NOT EXISTS horizon_h           SMALLINT;
