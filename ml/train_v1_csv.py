"""
Persiste en MLflow la V1 des modèles de prévision — un champion par site,
retenu selon la comparaison faite dans train_csv_experiment.py (voir
report_data.json et RAPPORT_ENTRAINEMENT.md) :

    SITE001 office      -> tow_temp   (MASE 0.640)
    SITE002 factory     -> lightgbm   (MASE 0.598)
    SITE003 datacenter  -> tow_temp   (MASE 0.582)
    SITE004 retail      -> lightgbm   (MASE 0.574)
    SITE005 hospital    -> tow_temp   (MASE 0.583)
    SITE006 office      -> tow_temp   (MASE 0.735)
    SITE007 factory     -> lightgbm   (MASE 0.584)

Entraîné sur le CSV synthétique (data/csv/, notre seule base d'entraînement,
voir RAPPORT_ENTRAINEMENT.md). Chaque run est explicitement tagué
data_source=csv_synthetic et stage=Staging (jamais Production) : personne ne
doit pouvoir croire par erreur que ces modèles ont vu de la vraie donnée.

Cible MLFLOW_TRACKING_URI (.env — pointe sur le serveur prod une fois
déployé, profile "mlflow" de compose.yaml). Pour un run local avant
déploiement : MLFLOW_TRACKING_URI=sqlite:///mlflow.db python train_v1_csv.py

Usage :
    python train_v1_csv.py
"""

import json
import os
import warnings

import mlflow
import mlflow.lightgbm
import mlflow.statsmodels
import pandas as pd
from dotenv import load_dotenv
from mlflow.tracking import MlflowClient

from evaluation import conformal_margin, empirical_coverage, mase
from models.naive import naive_forecast
from train_csv_experiment import (
    CSV_DIR,
    predict_lgbm,
    predict_ols,
    train_lgbm,
    train_ols,
)

warnings.filterwarnings("ignore")
load_dotenv()

TRAIN_END = "2024-09-30"
CALIB_END = "2024-11-15"

# Champion retenu par site — figé ici depuis report_data.json (pas recalculé
# à chaque run : la sélection du champion est une décision déjà prise et
# documentée dans RAPPORT_ENTRAINEMENT.md, ce script se contente de la
# rejouer et de la persister).
CHAMPIONS = {
    "SITE001": "tow_temp",
    "SITE002": "lightgbm",
    "SITE003": "tow_temp",
    "SITE004": "lightgbm",
    "SITE005": "tow_temp",
    "SITE006": "tow_temp",
    "SITE007": "lightgbm",
}

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db")
MLFLOW_EXPERIMENT_NAME = "smart-energy-forecast-v1-csv"

FORMULAS = {
    "tow": "consumption_kwh ~ C(time_of_week)",
    "tow_temp": "consumption_kwh ~ C(time_of_week) + temperature_celsius + I(temperature_celsius**2)",
}


def train_and_predict(champion, train, apply_df):
    if champion == "lightgbm":
        model = train_lgbm(train)
        return model, predict_lgbm(model, apply_df), predict_lgbm
    model = train_ols(train, FORMULAS[champion])
    return model, predict_ols(model, apply_df), predict_ols


def persist_site(client, site_id):
    path = os.path.join(CSV_DIR, f"{site_id}.csv")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    site_type = df["site_type"].iloc[0]
    df = df.dropna(subset=["consumption_kwh"]).reset_index(drop=True)

    train = df[df["timestamp"] <= TRAIN_END].reset_index(drop=True)
    calib = df[(df["timestamp"] > TRAIN_END) & (df["timestamp"] <= CALIB_END)].reset_index(drop=True)
    test = df[df["timestamp"] > CALIB_END].reset_index(drop=True)

    naive_pred_full = naive_forecast(df, value_col="consumption_kwh")
    y_true_test = test.set_index("timestamp")["consumption_kwh"]
    naive_on_test = naive_pred_full.loc[test["timestamp"]]

    champion = CHAMPIONS[site_id]
    model, pred_test, predict_fn = train_and_predict(champion, train, test)

    mase_test = mase(y_true_test, pred_test, naive_on_test)
    margin = conformal_margin(lambda d: predict_fn(model, d), calib, alpha=0.10)
    coverage = empirical_coverage(y_true_test, pred_test, margin)

    with mlflow.start_run(run_name=f"v1_{site_id}") as run:
        mlflow.log_param("site_id", site_id)
        mlflow.log_param("site_type", site_type)
        mlflow.log_param("champion", champion)
        mlflow.log_param("data_source", "csv_synthetic")
        mlflow.log_param("train_start", str(train["timestamp"].min()))
        mlflow.log_param("train_end", TRAIN_END)
        mlflow.log_param("n_train", len(train))

        mlflow.log_metric("mase_test", mase_test)
        mlflow.log_metric("conformal_margin_90", margin)
        mlflow.log_metric("coverage_90", coverage)

        mlflow.set_tag("stage_intent", "Staging")
        mlflow.set_tag(
            "caveat",
            "Entraine sur CSV synthetique 2023-2024, pas sur le gold reel. "
            "MAPE 7-44% observe contre les points reels sparse 2025-2026 "
            "(voir RAPPORT_ENTRAINEMENT.md). A reentrainer une fois le volume "
            "reel suffisant.",
        )

        artifact_path = "model"
        if champion == "lightgbm":
            mlflow.lightgbm.log_model(model, artifact_path)
        else:
            mlflow.statsmodels.log_model(model, artifact_path)

        model_uri = f"runs:/{run.info.run_id}/{artifact_path}"
        registered_name = f"enervision-forecast-{site_id.lower()}"
        mv = mlflow.register_model(model_uri, registered_name)

        client.transition_model_version_stage(
            name=registered_name,
            version=mv.version,
            stage="Staging",
            archive_existing_versions=True,
        )
        client.update_model_version(
            name=registered_name,
            version=mv.version,
            description=(
                f"V1 — {champion} entraine sur CSV synthetique (site_type={site_type}). "
                f"MASE test={mase_test:.3f}. NON valide sur donnee reelle production, "
                "voir tag 'caveat' du run et RAPPORT_ENTRAINEMENT.md."
            ),
        )

        print(
            f"{site_id} ({site_type}) champion={champion} "
            f"MASE={mase_test:.3f} margin90={margin:.1f} coverage90={coverage:.1%} "
            f"-> {registered_name} v{mv.version} (Staging)"
        )

        return {
            "site_id": site_id, "site_type": site_type, "champion": champion,
            "run_id": run.info.run_id, "registered_name": registered_name,
            "version": mv.version, "mase_test": round(mase_test, 4),
            "conformal_margin_90": round(margin, 2), "coverage_90": round(coverage, 4),
        }


def main():
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)
    client = MlflowClient()

    rows = [persist_site(client, site_id) for site_id in CHAMPIONS]

    with open("v1_persistence_report.json", "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

    print(f"\n>>> {len(rows)} modeles persistes dans MLflow ({MLFLOW_TRACKING_URI}), "
          f"experiment '{MLFLOW_EXPERIMENT_NAME}', stage=Staging.")
    print(">>> Resume ecrit dans v1_persistence_report.json")


if __name__ == "__main__":
    main()
