"""
Entrainement et reentrainement hebdomadaire du modele enervision-sensor-failure-forecast.

Combine deux signaux trouves separement (voir historique du repo, experiments
sensor-failure-prediction et sensor-failure-trend, tous les deux insuffisants seuls) : la
forme horaire (pic 8h-14h, creux la nuit) ET la tendance recente (day_index). Un modele qui
ne regarde que l'heure ignore la tendance et sous-estime le risque des derniers jours. Un
modele qui ne regarde que la tendance donne un seul chiffre par jour, pas actionnable a
l'heure pres.

Cible : is_failure = data_quality != "good", toutes causes de panne confondues. Confirme
identique sur les 7 sites (correlation 0.93-0.95 du taux horaire entre chaque paire de
sites) : un seul modele global, pas un par site.

Donnee vivante, pas un snapshot fige : lit measurements_silver sur une fenetre glissante
(SENSOR_FAILURE_TRAIN_LOOKBACK_DAYS, defaut 30 j), day_index compte depuis une ancre fixe
(SENSOR_FAILURE_DAY0, voir core/postgres_store.py) plutot que depuis le minimum de la fenetre
- sinon chaque reentrainement recalculerait un day_index different pour la meme date, et
predict_sensor_failure.py divergerait silencieusement.

--schedule reentraine chaque semaine et ne promeut la nouvelle version en Staging QUE si son
AUC de test egale ou depasse celle de la version actuellement en Staging - sinon elle reste
enregistree (tracable) mais non promue, et l'ancienne version continue de servir les
predictions. Reponse au risque de day_index qui derive sans reentrainement (voir tag
"caveat" du run) : ~15 j suffisent pour saturer la courbe vers 100% sans ce mecanisme -
un cycle hebdomadaire garde une marge x2, pour un cout en calcul negligeable (regression
logistique a ~25 parametres, quelques secondes) mais sans encombrer le registre MLflow
d'une version par nuit la plupart du temps sans rien a promouvoir.

Usage :
    cd ml && python scripts/train_sensor_failure_forecast.py              # un cycle
    cd ml && python scripts/train_sensor_failure_forecast.py --schedule   # mode service, cron hebdomadaire
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings

warnings.filterwarnings("ignore")

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

import mlflow
import mlflow.statsmodels
import pandas as pd
import statsmodels.formula.api as smf
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from core.postgres_store import SENSOR_FAILURE_DAY0, load_failure_training_data, make_pg_connection
from mlflow.tracking import MlflowClient
from sklearn.metrics import roc_auc_score

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = "sensor-failure-forecast"
REGISTERED_NAME = "enervision-sensor-failure-forecast"
MODEL_STAGE = os.environ.get("SENSOR_FAILURE_MODEL_STAGE", "Staging")

TEST_DAYS = 2
LOOKBACK_DAYS = int(os.environ.get("SENSOR_FAILURE_TRAIN_LOOKBACK_DAYS", "30"))
TRAIN_CRON = os.environ.get("SENSOR_FAILURE_TRAIN_CRON", "30 2 * * 0")


def load_data(conn) -> pd.DataFrame:
    df = load_failure_training_data(conn, LOOKBACK_DAYS)
    if df.empty:
        raise SystemExit(
            f"measurements_silver ne renvoie rien sur les {LOOKBACK_DAYS} derniers jours, rien a entrainer"
        )
    df["hour"] = df["timestamp"].dt.hour
    df["is_failure"] = (df["data_quality"] != "good").astype(int)
    df["day_index"] = (df["timestamp"].dt.normalize().dt.tz_localize(None) - SENSOR_FAILURE_DAY0).dt.days
    return df


def split_train_test(df: pd.DataFrame):
    cutoff = df["timestamp"].max().normalize() - pd.Timedelta(days=TEST_DAYS)
    train = df[df["timestamp"] < cutoff].reset_index(drop=True)
    test = df[df["timestamp"] >= cutoff].reset_index(drop=True)
    return train, test


def auc_de_la_version_en_service(client: MlflowClient) -> float | None:
    """AUC de la version actuellement au stage MODEL_STAGE, pour decider si le nouveau run
    fait mieux. None si aucune version n'y est encore promue (tout premier entrainement)."""
    versions = client.search_model_versions(f"name='{REGISTERED_NAME}'")
    candidates = [v for v in versions if v.current_stage == MODEL_STAGE]
    if not candidates:
        return None
    version = max(candidates, key=lambda v: int(v.version))
    run = client.get_run(version.run_id)
    return run.data.metrics.get("auc_test")


def cycle(args) -> int:
    """Un cycle : aucune exception ne remonte en mode --schedule, un Postgres ou un MLflow
    indisponible doit se rattraper au declenchement suivant, pas arreter le service."""
    try:
        client = MlflowClient()
        auc_en_service = auc_de_la_version_en_service(client)

        conn = make_pg_connection()
        try:
            df = load_data(conn)
        finally:
            conn.close()

        train, test = split_train_test(df)
        formula = "is_failure ~ C(hour) + day_index"
        model = smf.logit(formula, data=train).fit(disp=0)
        auc = roc_auc_score(test["is_failure"], model.predict(test))

        with mlflow.start_run(run_name="reentrainement_hebdomadaire" if args.schedule else "v1_forecast") as run:
            mlflow.log_param("formula", formula)
            mlflow.log_param("n_train", len(train))
            mlflow.log_param("n_test", len(test))
            mlflow.log_param("lookback_days", LOOKBACK_DAYS)
            mlflow.log_param("day0", str(SENSOR_FAILURE_DAY0.date()))
            mlflow.log_param("data_source", "postgres_measurements_silver_live")

            mlflow.log_metric("auc_test", auc)

            mlflow.set_tag("stage_intent", MODEL_STAGE)
            mlflow.set_tag(
                "caveat",
                "Reentraine sur une fenetre glissante de measurements_silver (pas un snapshot "
                "fige). day_index compte depuis une ancre fixe (SENSOR_FAILURE_DAY0 = "
                f"{SENSOR_FAILURE_DAY0.date()}, core/postgres_store.py), pas depuis le debut de "
                "la fenetre d'entrainement. Sans reentrainement regulier, ce terme de tendance "
                "sature la courbe vers 100% en ~15 jours (coefficient +0.177/jour sur "
                "l'echelle logit, mesure sur le premier entrainement).",
            )

            mlflow.statsmodels.log_model(model, "model")
            model_uri = f"runs:/{run.info.run_id}/model"
            mv = mlflow.register_model(model_uri, REGISTERED_NAME)

            promu = auc_en_service is None or auc >= auc_en_service
            if promu:
                client.transition_model_version_stage(
                    name=REGISTERED_NAME,
                    version=mv.version,
                    stage=MODEL_STAGE,
                    # True, contrairement au tout premier entrainement : un reentrainement qui
                    # fait mieux ou pareil doit remplacer la version en service, pas s'empiler
                    # a cote. archive_existing_versions n'archive que le stage cible (Staging),
                    # jamais une eventuelle version en Production ailleurs.
                    archive_existing_versions=True,
                )
                reference = f"{auc_en_service:.3f}" if auc_en_service is not None else "aucune version precedente"
                print(f">>> v{mv.version} promu {MODEL_STAGE} (AUC {auc:.3f} >= {reference})")
            else:
                print(
                    f">>> v{mv.version} enregistre mais NON promu : AUC {auc:.3f} < {MODEL_STAGE} actuel ({auc_en_service:.3f})"
                )

        return 0
    except Exception as erreur:
        print(f"Cycle d'entrainement en echec, nouvelle tentative au prochain declenchement : {erreur}")
        return 1


def run_scheduled(args) -> int:
    """Mode service : un premier cycle une minute apres le demarrage, puis a chaque
    declenchement de SENSOR_FAILURE_TRAIN_CRON (hebdomadaire par defaut)."""
    print(f"Planificateur : cron '{TRAIN_CRON}' (UTC), fenetre {LOOKBACK_DAYS} j, stage cible {MODEL_STAGE}")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        cycle,
        CronTrigger.from_crontab(TRAIN_CRON, timezone="UTC"),
        args=[args],
        next_run_time=(pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=60)).to_pydatetime(),
        max_instances=1,
        coalesce=True,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Planificateur arrete")
    return 0


def main() -> int:
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    parser = argparse.ArgumentParser(description="Entrainement du risque de panne capteur.")
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="mode service : reentraine chaque semaine selon SENSOR_FAILURE_TRAIN_CRON",
    )
    args = parser.parse_args()

    if args.schedule:
        return run_scheduled(args)
    return cycle(args)


if __name__ == "__main__":
    sys.exit(main())
