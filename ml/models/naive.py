"""
Prévision naïve saisonnière : "la même heure, la semaine dernière".

Ce n'est pas un modèle qu'on cherche à améliorer, c'est le plancher
de référence (ADR-04). Aucun modèle "intelligent" n'est promu s'il
ne bat pas ça de façon durable (MASE < 1 sur 7 jours glissants).
"""

import pandas as pd


def naive_forecast(df: pd.DataFrame, value_col: str = "consumption_kwh") -> pd.Series:
    """
    Pour chaque timestamp, prédit la valeur observée au même
    moment 7 jours plus tôt.

    df doit être trié par timestamp et avoir une colonne 'timestamp'
    régulière (une ligne par heure, sans trou majeur).
    """
    df = df.set_index("timestamp").sort_index()
    naive_pred = df[value_col].shift(freq="7D")
    # réaligne sur l'index d'origine (les 7 premiers jours n'ont pas de prédiction)
    return naive_pred.reindex(df.index)
