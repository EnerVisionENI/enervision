"""
Challenger : gradient boosting (LightGBM).

Sert uniquement de point de comparaison au champion TOWT (ADR-04).
S'il ne bat pas TOWT de façon nette et durable, on ne le promeut
jamais — le dossier explique pourquoi : un modèle boîte noire n'est
pas vendable à un directeur d'usine si un modèle simple fait aussi
bien.
"""

import lightgbm as lgb
import pandas as pd

FEATURE_COLUMNS = ["hour", "dow", "temperature_c"]


def _features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["hour"] = df["timestamp"].dt.hour
    df["dow"] = df["timestamp"].dt.dayofweek
    return df


def train_challenger(df_train: pd.DataFrame, value_col: str = "consumption_kwh") -> lgb.LGBMRegressor:
    df_train = _features(df_train)
    model = lgb.LGBMRegressor(n_estimators=200, max_depth=5, learning_rate=0.05, verbose=-1)
    model.fit(df_train[FEATURE_COLUMNS], df_train[value_col])
    return model


def predict_challenger(model: lgb.LGBMRegressor, df_future: pd.DataFrame) -> pd.Series:
    df_future = _features(df_future)
    preds = model.predict(df_future[FEATURE_COLUMNS])
    return pd.Series(preds, index=df_future["timestamp"])
