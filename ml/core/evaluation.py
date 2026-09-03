"""
Évaluation des modèles.

- MASE : le critère de promotion (NFR-06). Un modèle qui ne bat pas
  le naïf n'est jamais servi aux utilisateurs.
- Intervalle conforme (split conformal) : donne une marge d'erreur
  avec une garantie de couverture, sans supposer une loi de
  probabilité particulière (NFR-07, ADR-04).
"""

import numpy as np
import pandas as pd


def mase(y_true: pd.Series, y_pred: pd.Series, y_naive: pd.Series) -> float:
    """
    Mean Absolute Scaled Error, au sens de Hyndman & Koehler (2006).

    MASE < 1 : le modèle bat le naïf saisonnier.
    MASE >= 1 : le modèle ne sert à rien, le fusible doit se
    déclencher (voir ADR-04 et forecast_mase dans le dossier §8).

    Les trois séries doivent être alignées sur le même index
    (mêmes timestamps) et ne pas contenir de NaN résiduel.
    """
    aligned = pd.concat(
        {"true": y_true, "pred": y_pred, "naive": y_naive}, axis=1
    ).dropna()

    if aligned.empty:
        raise ValueError("Aucune ligne alignée entre y_true, y_pred et y_naive.")

    mae_model = np.mean(np.abs(aligned["true"] - aligned["pred"]))
    mae_naive = np.mean(np.abs(aligned["true"] - aligned["naive"]))

    if mae_naive == 0:
        return np.inf

    return float(mae_model / mae_naive)


def conformal_margin(
    model_predict_fn,
    df_calib: pd.DataFrame,
    value_col: str = "consumption_kwh",
    alpha: float = 0.10,
) -> float:
    """
    Calcule la marge d'erreur à ajouter/soustraire à chaque
    prédiction ponctuelle pour obtenir un intervalle avec une
    couverture empirique visée de (1 - alpha).

    IMPORTANT : df_calib doit être un jeu de données que le modèle
    n'a JAMAIS vu à l'entraînement. C'est ce qui garantit la
    couverture (voir NFR-07 : couverture visée entre 86% et 94%
    pour un intervalle à 90%).

    model_predict_fn : fonction qui prend df_calib et renvoie une
    pd.Series de prédictions alignée sur df_calib['timestamp'].
    """
    preds = model_predict_fn(df_calib)
    preds = preds.reset_index(drop=True)
    truth = df_calib[value_col].reset_index(drop=True)

    residuals = np.abs(truth - preds).dropna()
    if residuals.empty:
        raise ValueError("Pas de résidus calculables sur le jeu de calibration.")

    margin = float(np.quantile(residuals, 1 - alpha))
    return margin


def empirical_coverage(y_true: pd.Series, y_pred: pd.Series, margin: float) -> float:
    """
    Vérifie a posteriori la couverture réelle d'un intervalle
    [y_pred - margin, y_pred + margin] sur un jeu de test.
    Sert à contrôler NFR-07 (cible : entre 0.86 et 0.94 pour alpha=0.10).
    """
    aligned = pd.concat({"true": y_true, "pred": y_pred}, axis=1).dropna()
    lower = aligned["pred"] - margin
    upper = aligned["pred"] + margin
    inside = (aligned["true"] >= lower) & (aligned["true"] <= upper)
    return float(inside.mean())
