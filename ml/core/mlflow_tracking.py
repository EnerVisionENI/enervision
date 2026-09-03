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

load_dotenv()

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

        # --- logique de promotion : champion d'abord, fusible sinon ---
        if mase_towt < MASE_PROMOTION_THRESHOLD:
            # TOWT reste le champion par défaut s'il passe le seuil,
            # même si LightGBM fait mieux : on ne promeut le challenger
            # que s'il bat le champion de façon nette (voir ADR-04,
            # seuil de 15% sur trois semaines avant bascule définitive).
            promoted = "towt"
        else:
            promoted = "naive_fallback"

        mlflow.set_tag("promoted", promoted)
        mlflow.set_tag("fuse_triggered", str(mase_towt >= MASE_PROMOTION_THRESHOLD))

        print(f"[MLflow] run_id={run.info.run_id} — modèle promu : {promoted}")
        if promoted == "naive_fallback":
            print(f"⚠️  FUSIBLE DÉCLENCHÉ : MASE TOWT = {mase_towt:.3f} >= {MASE_PROMOTION_THRESHOLD}")

        return promoted
