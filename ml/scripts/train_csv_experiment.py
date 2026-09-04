"""
Entraîne et compare les modèles candidats (TOW, TOW+Temp, LightGBM) sur le
CSV synthétique versionné dans data/csv/ — notre seule base d'entraînement
(le gold MinIO réel reste trop creux pour entraîner, voir RAPPORT_ENTRAINEMENT.md).

Ce module expose les fonctions de préparation/entraînement/prédiction
réutilisées par train_v1_csv.py (persistance MLflow) : ne pas les dupliquer
ailleurs, importer d'ici.

Sort un unique JSON (report_data.json) consommé par le rapport (md + artifact).
"""

import json
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

warnings.filterwarnings("ignore")

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

import lightgbm as lgb
import numpy as np
import pandas as pd
import statsmodels.formula.api as smf

from core.data import load_gold_hourly
from core.evaluation import conformal_margin, empirical_coverage, mase
from models.naive import naive_forecast

CSV_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "csv")
SITES = ["SITE001", "SITE002", "SITE003", "SITE004", "SITE005", "SITE006", "SITE007"]

TRAIN_END = "2024-09-30"
CALIB_END = "2024-11-15"
# test_end = fin du CSV (2024-12-31)

LGBM_FEATURES = [
    "hour", "day_of_week", "month", "is_weekend", "is_working_hours",
    "temperature_celsius", "humidity_percent", "solar_irradiance_wm2",
]


def prepare_time_of_week(df):
    df = df.copy()
    df["time_of_week"] = df["day_of_week"] * 24 + df["hour"]
    return df


def train_ols(df_train, formula):
    return smf.ols(formula, data=prepare_time_of_week(df_train)).fit()


def predict_ols(model, df):
    df = prepare_time_of_week(df)
    preds = model.predict(df)
    preds.index = df["timestamp"]
    return preds


def train_lgbm(df_train):
    df_train = df_train.copy()
    model = lgb.LGBMRegressor(n_estimators=200, max_depth=5, learning_rate=0.05, verbose=-1)
    model.fit(df_train[LGBM_FEATURES], df_train["consumption_kwh"])
    return model


def predict_lgbm(model, df):
    preds = model.predict(df[LGBM_FEATURES])
    return pd.Series(preds, index=df["timestamp"])


def correlations_for_site(df, site_id, site_type):
    cols = ["consumption_kwh"] + LGBM_FEATURES
    corr = df[cols].corr()["consumption_kwh"].drop("consumption_kwh")
    return {"site_id": site_id, "site_type": site_type, **corr.round(4).to_dict()}


def evaluate_site(site_id):
    path = os.path.join(CSV_DIR, f"{site_id}.csv")
    df = pd.read_csv(path, parse_dates=["timestamp"])
    site_type = df["site_type"].iloc[0]
    df = df.dropna(subset=["consumption_kwh"]).reset_index(drop=True)

    corr_row = correlations_for_site(df, site_id, site_type)

    train = df[df["timestamp"] <= TRAIN_END].reset_index(drop=True)
    calib = df[(df["timestamp"] > TRAIN_END) & (df["timestamp"] <= CALIB_END)].reset_index(drop=True)
    test = df[df["timestamp"] > CALIB_END].reset_index(drop=True)

    df_full = df.set_index("timestamp")
    naive_pred_full = naive_forecast(df, value_col="consumption_kwh")
    naive_on_test = naive_pred_full.loc[test["timestamp"]]
    y_true = test.set_index("timestamp")["consumption_kwh"]

    results = {"site_id": site_id, "site_type": site_type, "n_train": len(train), "n_calib": len(calib), "n_test": len(test)}

    # TOW (sans température) vs TOW+Temp (avec) : on garde les deux pour comparer.
    for name, formula in [
        ("tow", "consumption_kwh ~ C(time_of_week)"),
        ("tow_temp", "consumption_kwh ~ C(time_of_week) + temperature_celsius + I(temperature_celsius**2)"),
    ]:
        model = train_ols(train, formula)
        pred_test = predict_ols(model, test)
        m = mase(y_true, pred_test, naive_on_test)
        results[f"mase_{name}"] = round(m, 4)

    lgbm_model = train_lgbm(train)
    pred_lgbm = predict_lgbm(lgbm_model, test)
    results["mase_lightgbm"] = round(mase(y_true, pred_lgbm, naive_on_test), 4)
    results["lgbm_importance"] = dict(zip(LGBM_FEATURES, [int(v) for v in lgbm_model.feature_importances_]))

    # Champion = meilleur MASE parmi les trois.
    champion_name = min(
        [("tow", results["mase_tow"]), ("tow_temp", results["mase_tow_temp"]), ("lightgbm", results["mase_lightgbm"])],
        key=lambda t: t[1],
    )[0]
    results["champion"] = champion_name
    champion_model, champion_predict = {
        "tow": (train_ols(train, "consumption_kwh ~ C(time_of_week)"), predict_ols),
        "tow_temp": (train_ols(train, "consumption_kwh ~ C(time_of_week) + temperature_celsius + I(temperature_celsius**2)"), predict_ols),
        "lightgbm": (lgbm_model, predict_lgbm),
    }[champion_name]

    margin = conformal_margin(lambda d: champion_predict(champion_model, d), calib, alpha=0.10)
    champion_pred_test = champion_predict(champion_model, test)
    coverage = empirical_coverage(y_true, champion_pred_test, margin)
    results["conformal_margin_90"] = round(margin, 2)
    results["coverage_90"] = round(coverage, 4)

    # --- Comparaison face aux vraies données MinIO (2025-2026, quasi tout NaN) ---
    real_compare = compare_to_real(site_id, champion_name, champion_model, champion_predict)
    results["real_data_compare"] = real_compare

    return corr_row, results


def compare_to_real(site_id, champion_name, champion_model, champion_predict):
    try:
        real_df = load_gold_hourly(site_id, "2025-08-02", "2026-09-03")
    except Exception as e:
        return {"error": str(e)}

    real_df = real_df.copy()
    real_df["hour"] = real_df["timestamp"].dt.hour
    real_df["day_of_week"] = real_df["timestamp"].dt.dayofweek
    real_df["month"] = real_df["timestamp"].dt.month
    real_df["is_weekend"] = (real_df["day_of_week"] >= 5).astype(int)
    real_df["is_working_hours"] = ((real_df["hour"] >= 8) & (real_df["hour"] <= 18) & (~real_df["is_weekend"].astype(bool))).astype(int)

    real_valid = real_df[real_df["avg_consumption_kw"].notna()].copy()
    real_valid["consumption_kwh"] = real_valid["avg_consumption_kw"]

    n_total = len(real_df)
    n_valid = len(real_valid)
    out = {
        "real_hours_total": n_total,
        "real_hours_with_consumption": n_valid,
        "real_consumption_coverage_pct": round(100 * n_valid / n_total, 2) if n_total else None,
    }
    if n_valid < 5:
        out["note"] = "Pas assez de points réels valides pour évaluer le modèle CSV dessus."
        return out

    if champion_name == "lightgbm":
        # Pas de temp/humidité/solaire au grain gold : imputées à 0 (signalé, pas silencieux).
        real_valid["temperature_celsius"] = 0.0
        real_valid["humidity_percent"] = 0.0
        real_valid["solar_irradiance_wm2"] = 0.0
        preds = champion_predict(champion_model, real_valid)
    else:
        real_valid["temperature_celsius"] = 20.0  # pas de mesure réelle disponible à cette granularité gold
        preds = champion_predict(champion_model, real_valid)

    y_real = real_valid.set_index("timestamp")["consumption_kwh"]
    aligned = pd.concat({"true": y_real, "pred": preds}, axis=1).dropna()
    mae = float(np.mean(np.abs(aligned["true"] - aligned["pred"])))
    mape = float(np.mean(np.abs((aligned["true"] - aligned["pred"]) / aligned["true"].replace(0, np.nan))))
    out["mae_on_real_valid_points"] = round(mae, 2)
    out["mape_on_real_valid_points_pct"] = round(100 * mape, 1) if not np.isnan(mape) else None
    out["n_points_compared"] = len(aligned)
    out["real_mean_consumption_kw"] = round(float(y_real.mean()), 2)
    return out


def main():
    corr_rows, results_rows = [], []
    for site in SITES:
        print(f"--- {site} ---")
        corr_row, res = evaluate_site(site)
        corr_rows.append(corr_row)
        results_rows.append(res)
        print(json.dumps(res, indent=2, ensure_ascii=False, default=str))

    out = {"correlations": corr_rows, "results": results_rows}
    report_path = os.path.join(os.path.dirname(__file__), "..", "reports", "report_data.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n>>> Écrit dans {report_path}")


if __name__ == "__main__":
    main()
