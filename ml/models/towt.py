"""
TOWT — Time-Of-Week and Temperature (Mathieu et al., 2011).

Modèle champion : simple, interprétable, entraîné en quelques
secondes, adapté à un historique court (voir ADR-04 dans le dossier
de conception — dix jours d'historique ne suffisent pas à un modèle
profond, mais suffisent à une régression sur des variables connues).
"""

import pandas as pd
import statsmodels.formula.api as smf
import statsmodels.regression.linear_model as sm_model


def prepare_towt_features(df: pd.DataFrame) -> pd.DataFrame:
    """Ajoute les variables categorielles heure-de-la-semaine."""
    df = df.copy()
    df["hour"] = df["timestamp"].dt.hour
    df["dow"] = df["timestamp"].dt.dayofweek  # 0 = lundi
    df["time_of_week"] = df["dow"] * 24 + df["hour"]  # 0..167, un créneau par heure de la semaine
    return df


def train_towt(df_train: pd.DataFrame, value_col: str = "consumption_kwh") -> sm_model.RegressionResultsWrapper:
    """
    Entraîne la régression TOWT. Chaque coefficient est explicable :
    un par créneau horaire de la semaine, plus une réponse
    quadratique à la température (chauffage/clim).
    """
    df_train = prepare_towt_features(df_train)
    formula = f"{value_col} ~ C(time_of_week) + temperature_c + I(temperature_c**2)"
    model = smf.ols(formula, data=df_train).fit()
    return model


def predict_towt(model: sm_model.RegressionResultsWrapper, df_future: pd.DataFrame) -> pd.Series:
    """Prédit sur un nouveau jeu de données (même format que l'entraînement)."""
    df_future = prepare_towt_features(df_future)
    preds = model.predict(df_future)
    preds.index = df_future["timestamp"]
    return preds
