"""
Inference du modele enervision-sensor-failure-forecast (registry MLflow) : ecrit le risque
de panne capteur heure par heure, pour les prochaines 24h, dans la table Postgres
sensor_failure_forecast (voir infra/postgres/init/08_sensor_failure_forecast.sql).

day_index (le nombre de jours depuis le debut de l'historique d'entrainement) est fige a sa
valeur d'aujourd'hui pour toute la fenetre de 24h, pas recalcule heure par heure. Le modele
capture une tendance a la hausse sur les 9 jours d'entrainement (29% -> 68%) : la laisser
grandir au-dela d'aujourd'hui l'extrapolerait indefiniment, sans garantie qu'elle se
poursuive. On stabilise la prevision sur le niveau observe maintenant plutot que de parier
sur la suite de la tendance.

--schedule tourne en boucle et reecrit la fenetre a chaque declenchement de
SENSOR_FAILURE_PREDICT_CRON (defaut : chaque heure) : c'est ce qui fait avancer la fenetre de
24h avec le temps, pas un rafraichissement des donnees d'entree (hour/day_index ne dependent
d'aucune nouvelle lecture gold).

day0 (SENSOR_FAILURE_DAY0, core/postgres_store.py) est une ancre fixe, pas relue depuis une
source a chaque cycle : train_sensor_failure_forecast.py doit compter day_index depuis la
meme date, sinon un reentrainement sur une fenetre glissante ferait diverger predict et train.

Usage :
    cd ml && python scripts/predict_sensor_failure.py              # un cycle, ecrit en base
    cd ml && python scripts/predict_sensor_failure.py --dry-run    # calcule et affiche, n'ecrit rien
    cd ml && python scripts/predict_sensor_failure.py --schedule   # mode service, boucle sur SENSOR_FAILURE_PREDICT_CRON
"""

import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import warnings

warnings.filterwarnings("ignore")

from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

os.environ.setdefault("AWS_ACCESS_KEY_ID", os.environ.get("MINIO_ACCESS_KEY", ""))
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.environ.get("MINIO_SECRET_KEY", ""))
os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", os.environ.get("MINIO_ENDPOINT", ""))

import mlflow
import pandas as pd
import psycopg2
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from psycopg2.extras import execute_values

from core.postgres_store import SENSOR_FAILURE_DAY0

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
REGISTERED_NAME = "enervision-sensor-failure-forecast"
MODEL_STAGE = os.environ.get("SENSOR_FAILURE_MODEL_STAGE", "Staging")

HORIZON_HOURS = 24

# Pas d'attente sur un cron ETL/gold : hour/day_index ne dependent d'aucune nouvelle lecture,
# seule la fenetre glissante (ancree sur "maintenant") justifie de rejouer regulierement.
SENSOR_FAILURE_PREDICT_CRON = os.environ.get("SENSOR_FAILURE_PREDICT_CRON", "5 * * * *")
HEARTBEAT_PATH = os.environ.get(
    "SENSOR_FAILURE_HEARTBEAT_PATH",
    os.path.join(tempfile.gettempdir(), "heartbeat-sensor-failure"),
)


def pg_connect():
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5433"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )


def calculer_previsions(model) -> pd.DataFrame:
    now = pd.Timestamp.now(tz="UTC").floor("h")
    hours = pd.date_range(now, periods=HORIZON_HOURS, freq="1h", tz="UTC")

    # Fige, pas recalcule par heure (voir le module docstring) : la fenetre de 24h peut
    # traverser minuit sans que la prevision ne saute au niveau de tendance du lendemain.
    day_index_aujourdhui = (now.normalize().tz_localize(None) - SENSOR_FAILURE_DAY0).days

    future = pd.DataFrame({"target_hour": hours})
    future["hour"] = future["target_hour"].dt.hour
    future["day_index"] = day_index_aujourdhui
    future["risk"] = model.predict(future)
    return future


def ecrire_previsions(conn, future: pd.DataFrame, version, auc_test) -> int:
    now = future["target_hour"].min()
    rows = [
        (
            row.target_hour.to_pydatetime(),
            float(row.risk),
            REGISTERED_NAME,
            str(version.version),
            MODEL_STAGE,
            version.run_id,
            float(auc_test) if auc_test is not None else None,
            int(future["day_index"].max()) + 1,
            datetime.now(UTC),
        )
        for row in future.itertuples()
    ]

    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM sensor_failure_forecast WHERE target_hour >= %s",
            (now.to_pydatetime(),),
        )
        execute_values(
            cur,
            """
            INSERT INTO sensor_failure_forecast
                (target_hour, risk, model_name, model_version, model_stage,
                 run_id, auc_test, n_train_days, predicted_at)
            VALUES %s
            ON CONFLICT (target_hour) DO UPDATE SET
                risk = EXCLUDED.risk,
                model_name = EXCLUDED.model_name,
                model_version = EXCLUDED.model_version,
                model_stage = EXCLUDED.model_stage,
                run_id = EXCLUDED.run_id,
                auc_test = EXCLUDED.auc_test,
                n_train_days = EXCLUDED.n_train_days,
                predicted_at = EXCLUDED.predicted_at
            """,
            rows,
        )
    conn.commit()
    return len(rows)


def charger_modele():
    client = mlflow.tracking.MlflowClient()
    versions = client.search_model_versions(f"name='{REGISTERED_NAME}'")
    candidates = [v for v in versions if v.current_stage == MODEL_STAGE]
    if not candidates:
        raise SystemExit(f"Aucune version de {REGISTERED_NAME} au stage {MODEL_STAGE} : rien a predire.")
    version = max(candidates, key=lambda v: int(v.version))
    run = client.get_run(version.run_id)
    auc_test = run.data.metrics.get("auc_test")
    model = mlflow.statsmodels.load_model(f"models:/{REGISTERED_NAME}/{MODEL_STAGE}")
    return model, version, auc_test


def cycle(args) -> int:
    """Un cycle : aucune exception ne remonte en mode --schedule, un MLflow ou un Postgres
    indisponible doit se rattraper au declenchement suivant, pas arreter le service."""
    try:
        model, version, auc_test = charger_modele()
        future = calculer_previsions(model)

        if args.dry_run:
            print(future[["target_hour", "hour", "day_index", "risk"]])
            print(f"Modele : {REGISTERED_NAME} v{version.version} ({MODEL_STAGE}), AUC={auc_test}")
            return 0

        conn = pg_connect()
        try:
            n = ecrire_previsions(conn, future, version, auc_test)
        finally:
            conn.close()

        with open(HEARTBEAT_PATH, "w", encoding="utf-8") as fichier:
            fichier.write(pd.Timestamp.now(tz="UTC").isoformat())

        print(f"{n} heures ecrites dans sensor_failure_forecast")
        print(f"Modele : {REGISTERED_NAME} v{version.version} ({MODEL_STAGE}), AUC={auc_test}")
        print(f"Fenetre : {future['target_hour'].min()} -> {future['target_hour'].max()}")
        print(f"Risque min/max : {future['risk'].min():.1%} / {future['risk'].max():.1%}")
        return 0
    except Exception as erreur:
        print(f"Cycle de prevision en echec, nouvelle tentative au prochain declenchement : {erreur}")
        return 1


def run_scheduled(args) -> int:
    """Mode service : un premier cycle une minute apres le demarrage (evite une table vide
    pendant l'heure qui suit un deploiement), puis a chaque declenchement de
    SENSOR_FAILURE_PREDICT_CRON."""
    print(f"Planificateur : cron '{SENSOR_FAILURE_PREDICT_CRON}' (UTC), stage {MODEL_STAGE}")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        cycle,
        CronTrigger.from_crontab(SENSOR_FAILURE_PREDICT_CRON, timezone="UTC"),
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

    parser = argparse.ArgumentParser(description="Inference du risque de panne capteur.")
    parser.add_argument("--dry-run", action="store_true", help="calcule et affiche, n'ecrit rien")
    parser.add_argument("--schedule", action="store_true", help="mode service : rejoue selon SENSOR_FAILURE_PREDICT_CRON")
    args = parser.parse_args()

    if args.schedule:
        return run_scheduled(args)
    return cycle(args)


if __name__ == "__main__":
    sys.exit(main())
