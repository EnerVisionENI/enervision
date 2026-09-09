"""
Prevision du risque de panne capteur, heure par heure, sur les prochaines 24h.

Combine les deux signaux trouves separement (voir experiments sensor-failure-prediction
et sensor-failure-trend, tous les deux insuffisants seuls) : la forme horaire (pic 8h-14h,
creux la nuit) ET la tendance quotidienne a la hausse (29% le premier jour observe a 68%
la veille du dernier jour). Un modele qui ne regarde que l'heure ignore la tendance et sous
estime le risque des derniers jours (AUC 0.567). Un modele qui ne regarde que la tendance
donne un seul chiffre par jour, pas actionnable a l'heure pres.

Ce modele donne une courbe de risque heure par heure pour le jour suivant, en tenant
compte des deux, AUC 0.609 sur les 2 derniers jours tenus en test.

Cible : is_failure = data_quality != "good", toutes causes de panne confondues. Confirme
identique sur les 7 sites (motif horaire et tendance tous les deux a moins de 2% d'ecart
entre sites) : un seul modele global, pas un par site.

A reentrainer a chaque nouveau jour de donnees, day_index grandit avec l'historique
disponible.

Usage :
    cd ml && python scripts/train_sensor_failure_forecast.py
"""

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
from mlflow.tracking import MlflowClient
from sklearn.metrics import roc_auc_score

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "silver_snapshot_local.parquet")

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = "sensor-failure-forecast"
REGISTERED_NAME = "enervision-sensor-failure-forecast"

TEST_DAYS = 2


def load_data():
    df = pd.read_parquet(DATA_PATH)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["hour"] = df["timestamp"].dt.hour
    df["is_failure"] = (df["data_quality"] != "good").astype(int)
    day0 = df["timestamp"].dt.normalize().min()
    df["day_index"] = (df["timestamp"].dt.normalize() - day0).dt.days.astype(int)
    return df


def split_train_test(df):
    cutoff = df["timestamp"].max().normalize() - pd.Timedelta(days=TEST_DAYS)
    train = df[df["timestamp"] < cutoff].reset_index(drop=True)
    test = df[df["timestamp"] >= cutoff].reset_index(drop=True)
    return train, test


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    client = MlflowClient()

    df = load_data()
    train, test = split_train_test(df)

    formula = "is_failure ~ C(hour) + day_index"
    model = smf.logit(formula, data=train).fit(disp=0)

    pred_test = model.predict(test)
    auc = roc_auc_score(test["is_failure"], pred_test)

    next_day = df["day_index"].max() + 1
    future = pd.DataFrame({"hour": range(24), "day_index": next_day})
    future["risk"] = model.predict(future)
    print(future.round(3).to_string(index=False))
    print()
    print(f"AUC test : {auc:.3f}")
    print(f"Risque min/max prevu jour {next_day} : {future['risk'].min():.1%} / {future['risk'].max():.1%}")

    with mlflow.start_run(run_name="v1_forecast") as run:
        mlflow.log_param("formula", formula)
        mlflow.log_param("n_train", len(train))
        mlflow.log_param("n_test", len(test))
        mlflow.log_param("data_source", "silver_snapshot_local_9days")

        mlflow.log_metric("auc_test", auc)
        mlflow.log_metric("risk_min_next_day", future["risk"].min())
        mlflow.log_metric("risk_max_next_day", future["risk"].max())

        mlflow.set_tag("stage_intent", "Staging")
        mlflow.set_tag(
            "caveat",
            "Combine heure de la journee et tendance quotidienne (day_index). Remplace "
            "les deux approches separees precedentes, l'une ignorait la tendance (AUC "
            "0.567), l'autre ne donnait qu'un chiffre par jour non actionnable a l'heure "
            "pres. Entraine sur 9 jours seulement (2026-09-01 au 2026-09-09) : a "
            "reentrainer regulierement, la tendance actuelle n'est pas garantie de "
            "se poursuivre indefiniment. Modele global, confirme identique sur les 7 sites.",
        )

        mlflow.statsmodels.log_model(model, "model")

        model_uri = f"runs:/{run.info.run_id}/model"
        mv = mlflow.register_model(model_uri, REGISTERED_NAME)

        client.transition_model_version_stage(
            name=REGISTERED_NAME,
            version=mv.version,
            stage="Staging",
            archive_existing_versions=False,
        )

        print(f"\n>>> {REGISTERED_NAME} v{mv.version} (Staging) dans MLflow ({MLFLOW_TRACKING_URI})")


if __name__ == "__main__":
    main()
