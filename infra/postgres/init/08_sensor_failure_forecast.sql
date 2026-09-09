-- infra/postgres/init/08_sensor_failure_forecast.sql
-- Prévision du risque de panne capteur, produite par ml/scripts/predict_sensor_failure.py
-- (modèle enervision-sensor-failure-forecast, registry MLflow).
--
-- Modèle global, pas un par site : le motif horaire et la tendance quotidienne sont confirmés
-- identiques sur les 7 sites (à moins de 2 % d'écart chaque jour), voir le tag "caveat" du run
-- MLflow. Une ligne = un risque prédit pour une heure donnée, toutes causes de panne confondues
-- (data_quality != 'good'), pas de site_id dans la clé.
--
-- Même principe d'écriture que predictions_forecast : le job fait DELETE + INSERT de tout
-- target_hour >= début de son horizon à chaque cycle, jamais d'upsert ligne à ligne. Les heures
-- passées ne sont pas réécrites, elles restent le risque réellement prédit pour cette heure-là.

CREATE TABLE IF NOT EXISTS sensor_failure_forecast (
    target_hour     TIMESTAMPTZ  NOT NULL,
    risk            NUMERIC      NOT NULL,
    model_name      VARCHAR(100) NOT NULL,
    model_version   VARCHAR(10)  NOT NULL,
    model_stage     VARCHAR(20),
    run_id          VARCHAR(32),
    auc_test        NUMERIC,
    n_train_days    SMALLINT,
    predicted_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (target_hour)
);

CREATE INDEX IF NOT EXISTS idx_sensor_failure_forecast_ts ON sensor_failure_forecast(target_hour);
