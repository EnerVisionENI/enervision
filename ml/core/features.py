"""
Construction du jeu de features d'inférence : les modèles V1 sont calendaires (heure de la
semaine + météo, aucun retard de consommation), donc tout ce dont ils ont besoin pour un
instant futur se dérive d'un timestamp et d'une estimation météo.

Les noms de colonnes reproduisent exactement ceux du CSV d'entraînement
(scripts/train_csv_experiment.LGBM_FEATURES et les formules de train_v1_csv.FORMULAS) : c'est
la seule contrainte dure ici. Un nom qui diverge fait échouer patsy, ou pire, fait prédire
LightGBM sur des colonnes décalées sans lever d'erreur.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

CALENDAR_COLUMNS = ["hour", "day_of_week", "month", "is_weekend", "is_working_hours"]
WEATHER_COLUMNS = ["temperature_celsius", "humidity_percent"]

# Le gold ne porte aucune colonne d'irradiance solaire (ni aggregates_gold_hourly, ni
# measurements_silver) : les sites dont le champion est LightGBM ne sont pas inférables tant
# qu'ils n'ont pas été réentraînés sans cette feature, ou que l'ETL ne la collecte pas.
UNAVAILABLE_IN_GOLD = {"solar_irradiance_wm2"}

# Définition supposée des heures ouvrées : le générateur du CSV synthétique n'est pas versionné
# (ml/data/csv/ est hors git), donc cette borne est une reconstruction. Elle ne concerne que
# les modèles LightGBM — les champions tow_temp n'utilisent que time_of_week et la température.
# À revalider par `predict.py --check` le jour où un modèle LightGBM redevient inférable.
WORKING_HOURS = (8, 18)

# Même repli que core/data.TEMPERATURE_FALLBACK_C : un repère pour ne pas planter, pas une
# donnée. Une prévision assise dessus est signalée par temperature_source.
TEMPERATURE_FALLBACK_C = 20.0


def calendar_features(index: pd.DatetimeIndex, tz: str) -> pd.DataFrame:
    """
    Dérive les variables calendaires d'un index horaire UTC, lues dans le fuseau `tz`.

    Le fuseau compte : les 168 coefficients de C(time_of_week) ont été appris sur les
    timestamps du CSV, et si celui-ci était en heure locale, lire l'index en UTC décale toute
    la semaine d'une à deux heures. C'est exactement ce que `predict.py --check --probe-tz`
    permet de trancher sur données réelles.
    """
    local = index.tz_convert(tz)
    df = pd.DataFrame({"timestamp": index})
    df["hour"] = local.hour.astype(int)
    df["day_of_week"] = local.dayofweek.astype(int)  # lundi = 0, comme pandas à l'entraînement
    df["month"] = local.month.astype(int)
    df["is_weekend"] = (df["day_of_week"] >= 5).astype(int)
    df["is_working_hours"] = (
        (df["hour"] >= WORKING_HOURS[0]) & (df["hour"] < WORKING_HOURS[1]) & (df["is_weekend"] == 0)
    ).astype(int)
    return df


def attach_weather(
    df: pd.DataFrame,
    slots: pd.DataFrame,
    means: dict[str, float | None],
    fallback_temperature: float = TEMPERATURE_FALLBACK_C,
) -> pd.DataFrame:
    """
    Ajoute la météo estimée à un frame calendaire, du plus précis au plus grossier :
    climatologie du créneau (jour de semaine, heure), puis moyenne du site, puis constante.

    La provenance retenue est renvoyée dans `temperature_source` et finit en base : sans elle,
    impossible de distinguer après coup une prévision assise sur du climat mesuré d'une
    prévision assise sur 20 °C arbitraires.
    """
    df = df.copy()
    keys = pd.MultiIndex.from_arrays([df["day_of_week"], df["hour"]])

    for column in WEATHER_COLUMNS:
        if slots.empty or column not in slots.columns:
            df[column] = pd.Series(np.nan, index=df.index, dtype="float64")
        else:
            df[column] = pd.Series(slots[column].reindex(keys).to_numpy(), index=df.index, dtype="float64")

    # Provenance de la seule variable qu'un champion V1 utilise vraiment. L'humidité n'est
    # tracée nulle part : elle ne sert qu'aux modèles LightGBM, aujourd'hui non inférables.
    source = pd.Series("climatology_gold", index=df.index)
    source[df["temperature_celsius"].isna()] = "site_mean_gold"
    site_mean = means.get("temperature_celsius")
    if site_mean is not None:
        df["temperature_celsius"] = df["temperature_celsius"].fillna(site_mean)
    source[df["temperature_celsius"].isna()] = "constant_fallback"
    df["temperature_celsius"] = df["temperature_celsius"].fillna(fallback_temperature)
    df["temperature_source"] = source

    humidity_mean = means.get("humidity_percent")
    if humidity_mean is not None:
        df["humidity_percent"] = df["humidity_percent"].fillna(humidity_mean)

    return df


def build_future_frame(
    start: pd.Timestamp,
    end: pd.Timestamp,
    tz: str,
    slots: pd.DataFrame,
    means: dict[str, float | None],
    fallback_temperature: float = TEMPERATURE_FALLBACK_C,
) -> pd.DataFrame:
    """Un point par heure sur [start, end[, prêt à passer aux fonctions de prédiction."""
    index = pd.date_range(start=start, end=end, freq="1h", tz="UTC", inclusive="left")
    return attach_weather(calendar_features(index, tz), slots, means, fallback_temperature)


def missing_features(df: pd.DataFrame, required: list[str]) -> list[str]:
    """Features attendues par un modèle qu'on ne sait pas construire — vérifié avant l'appel
    au modèle, pour rendre un site non inférable explicite plutôt qu'une trace patsy."""
    return [feature for feature in required if feature not in df.columns or df[feature].isna().all()]
