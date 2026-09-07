"""
Traçabilité MLflow : quel modèle, entraîné avec quelles données,
avec quelle performance, et lequel est actuellement promu.

La logique de promotion (fusible ADR-04) vit ici : le champion n'est
promu que si son MASE glissant reste sous le seuil. Sinon, le tag
'promoted' pointe vers le naïf, et l'API doit le respecter.
"""

import os

import mlflow
import mlflow.lightgbm
import mlflow.statsmodels
from dotenv import load_dotenv
from mlflow.tracking import MlflowClient

load_dotenv()

# Le client MLflow uploade les artifacts directement vers MinIO (le serveur ne
# fait pas relai) : il lui faut donc les mêmes credentials que core/data.py.
os.environ.setdefault("AWS_ACCESS_KEY_ID", os.environ.get("MINIO_ACCESS_KEY", ""))
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.environ.get("MINIO_SECRET_KEY", ""))
os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", os.environ.get("MINIO_ENDPOINT", ""))

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MLFLOW_EXPERIMENT_NAME = os.environ.get("MLFLOW_EXPERIMENT_NAME", "smart-energy-forecast")
MASE_PROMOTION_THRESHOLD = float(os.environ.get("MASE_PROMOTION_THRESHOLD", "1.0"))


def log_run(
    site_id: str,
    train_start: str, train_end: str,
    mase_towt: float,
    mase_lgbm: float,
    conformal_margin_90: float,
    coverage: float,
    towt_model,
    lgbm_model,
) -> str:
    """
    Journalise un run complet et applique la règle de promotion.
    Renvoie le nom du modèle promu ('towt', 'lightgbm' ou 'naive_fallback').
    """
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    mlflow.set_experiment(MLFLOW_EXPERIMENT_NAME)

    with mlflow.start_run(run_name=f"forecast_{site_id}") as run:
        mlflow.log_param("site_id", site_id)
        mlflow.log_param("train_start", train_start)
        mlflow.log_param("train_end", train_end)
        mlflow.log_param("mase_promotion_threshold", MASE_PROMOTION_THRESHOLD)

        mlflow.log_metric("mase_towt", mase_towt)
        mlflow.log_metric("mase_lightgbm", mase_lgbm)
        mlflow.log_metric("conformal_margin_90", conformal_margin_90)
        mlflow.log_metric("empirical_coverage_90", coverage)

        mlflow.statsmodels.log_model(towt_model, "towt_model")
        mlflow.lightgbm.log_model(lgbm_model, "lightgbm_model")

        # TOWT reste champion par défaut sous le seuil, même si LightGBM fait
        # mieux : on ne bascule que sur un gain net et durable (ADR-04).
        if mase_towt < MASE_PROMOTION_THRESHOLD:
            promoted = "towt"
        else:
            promoted = "naive_fallback"

        mlflow.set_tag("promoted", promoted)
        mlflow.set_tag("data_source", "gold_real")
        mlflow.set_tag("fuse_triggered", str(mase_towt >= MASE_PROMOTION_THRESHOLD))

        print(f"[MLflow] run_id={run.info.run_id} — modèle promu : {promoted}")

        # Sous le seuil : le champion réel (towt) est enregistré comme nouvelle
        # version dans le Model Registry — même convention de nom que
        # scripts/train_v1_csv.py (enervision-forecast-<site>), donc un
        # réentraînement sur gold réel vient s'empiler sur les versions CSV
        # existantes plutôt que de créer un modèle séparé. Toujours en Staging :
        # jamais promu Production automatiquement, un humain décide.
        # Au-dessus du seuil (fusible déclenché) : rien à enregistrer, le naïf
        # n'est pas un objet modèle logué (cf. plus haut, seuls towt_model et
        # lightgbm_model sont loggés).
        if promoted == "towt":
            client = MlflowClient()
            registered_name = f"enervision-forecast-{site_id.lower()}"
            model_uri = f"runs:/{run.info.run_id}/towt_model"
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
                    f"Réentraîné sur gold réel ({train_start} -> {train_end}). "
                    f"MASE TOWT={mase_towt:.3f}, marge conforme 90%={conformal_margin_90:.1f}, "
                    f"couverture empirique={coverage:.1%}. "
                    "Attention : température constante (pas encore dans le gold) et heures "
                    "manquantes comblées par ffill/bfill — voir FIXME de core/data.py."
                ),
            )
            print(f"[MLflow] -> {registered_name} v{mv.version} (Staging)")
        else:
            print(f"⚠️  FUSIBLE DÉCLENCHÉ : MASE TOWT = {mase_towt:.3f} >= {MASE_PROMOTION_THRESHOLD} — pas de nouvelle version enregistrée")

        return promoted
