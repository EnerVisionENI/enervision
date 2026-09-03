"""
Point d'entrée : entraîne le naïf, TOWT et le challenger sur un site,
calcule le MASE et l'intervalle conforme, journalise dans MLflow.

Usage :
    python train.py
    python train.py --site site_04
"""

import argparse
import os

from dotenv import load_dotenv

from data import load_gold_hourly, split_train_calib_test
from evaluation import conformal_margin, empirical_coverage, mase
from mlflow_tracking import log_run
from models.challenger import predict_challenger, train_challenger
from models.naive import naive_forecast
from models.towt import predict_towt, train_towt

load_dotenv()


def main(site_id: str):
    print(f"--- Entraînement pour {site_id} ---")

    # 1. Chargement
    df = load_gold_hourly(
        site_id=site_id,
        start=os.environ["TRAIN_START"],
        end=os.environ["TEST_END"],
    )
    print(f"{len(df)} lignes chargées, qualité : {dict(df['data_quality'].value_counts())}")

    train, calib, test = split_train_calib_test(
        df,
        os.environ["TRAIN_START"], os.environ["TRAIN_END"],
        os.environ["CALIB_START"], os.environ["CALIB_END"],
        os.environ["TEST_START"], os.environ["TEST_END"],
    )
    print(f"train={len(train)} · calib={len(calib)} · test={len(test)}")

    # 2. Le plancher (naïf)
    df_full = df.set_index("timestamp")
    naive_pred_full = naive_forecast(df, value_col="consumption_kwh")
    naive_on_test = naive_pred_full.loc[test["timestamp"]]

    # 3. Le champion (TOWT)
    towt_model = train_towt(train)
    towt_pred_test = predict_towt(towt_model, test)

    # 4. Le challenger (LightGBM)
    lgbm_model = train_challenger(train)
    lgbm_pred_test = predict_challenger(lgbm_model, test)

    # 5. MASE des deux modèles contre le naïf
    y_true = test.set_index("timestamp")["consumption_kwh"]
    mase_towt = mase(y_true, towt_pred_test, naive_on_test)
    mase_lgbm = mase(y_true, lgbm_pred_test, naive_on_test)
    print(f"MASE TOWT      = {mase_towt:.3f}")
    print(f"MASE LightGBM  = {mase_lgbm:.3f}")

    # 6. Intervalle conforme sur le champion, calibré sur le jeu dédié
    margin = conformal_margin(
        model_predict_fn=lambda d: predict_towt(towt_model, d),
        df_calib=calib,
        alpha=float(os.environ.get("CONFORMAL_ALPHA", "0.10")),
    )
    coverage = empirical_coverage(y_true, towt_pred_test, margin)
    print(f"Marge conforme (90%) = ± {margin:.1f} kWh")
    print(f"Couverture empirique sur test = {coverage:.1%} (cible : 86%–94%)")

    # 7. Journalisation + décision de promotion
    promoted = log_run(
        site_id=site_id,
        train_start=os.environ["TRAIN_START"],
        train_end=os.environ["TRAIN_END"],
        mase_towt=mase_towt,
        mase_lgbm=mase_lgbm,
        conformal_margin_90=margin,
        coverage=coverage,
        towt_model=towt_model,
        lgbm_model=lgbm_model,
    )
    print(f"\n>>> Modèle promu pour {site_id} : {promoted}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", default=os.environ.get("DEFAULT_SITE_ID", "site_02"))
    args = parser.parse_args()
    main(args.site)
