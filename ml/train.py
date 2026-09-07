"""
Point d'entrée : entraîne le naïf, TOWT et le challenger sur un ou plusieurs
sites, calcule le MASE et l'intervalle conforme, journalise dans MLflow.

Fenêtres de dates (train/calib/test) : si TRAIN_START/TRAIN_END/CALIB_START/
CALIB_END/TEST_START/TEST_END sont TOUTES les six définies dans l'environnement,
elles sont utilisées telles quelles (essai manuel sur une fenêtre précise).
Sinon, calculées automatiquement par rapport à maintenant (UTC) via des
fenêtres glissantes — nécessaire pour qu'un cron mensuel réentraîne sur des
données fraîches à chaque fois plutôt que sur la même fenêtre figée. Tailles
réglables via TRAIN_WINDOW_DAYS (défaut 90) / CALIB_WINDOW_DAYS (défaut 7) /
TEST_WINDOW_DAYS (défaut 7).

Sites : --site un seul, sinon tous ceux détectés dans le gold (pas de liste
codée en dur, cf. core.data.list_available_sites). Un site en échec n'empêche
pas les autres de s'entraîner.

Usage :
    python train.py                  # tous les sites détectés, fenêtres glissantes
    python train.py --site site_04   # un seul site
"""

import argparse
import os
from datetime import UTC, datetime, timedelta

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from core.data import list_available_sites, load_gold_hourly, split_train_calib_test
from core.evaluation import conformal_margin, empirical_coverage, mase
from core.mlflow_tracking import log_run
from models.challenger import predict_challenger, train_challenger
from models.naive import naive_forecast
from models.towt import predict_towt, train_towt

DATE_ENV_VARS = ("TRAIN_START", "TRAIN_END", "CALIB_START", "CALIB_END", "TEST_START", "TEST_END")


def fenetres_glissantes(now: datetime | None = None) -> dict[str, str]:
    """Calcule les 6 bornes train/calib/test par rapport à `now` (UTC, défaut :
    l'instant présent) : test = les TEST_WINDOW_DAYS derniers jours, calib les
    CALIB_WINDOW_DAYS juste avant, train les TRAIN_WINDOW_DAYS avant ça.
    Glisse automatiquement d'un run à l'autre — sans ça, un cron mensuel
    réentraînerait indéfiniment sur la même fenêtre figée dans .env."""
    now = now or datetime.now(UTC)
    train_window = int(os.environ.get("TRAIN_WINDOW_DAYS", "90"))
    calib_window = int(os.environ.get("CALIB_WINDOW_DAYS", "7"))
    test_window = int(os.environ.get("TEST_WINDOW_DAYS", "7"))

    test_end = now
    test_start = test_end - timedelta(days=test_window)
    calib_end = test_start
    calib_start = calib_end - timedelta(days=calib_window)
    train_end = calib_start
    train_start = train_end - timedelta(days=train_window)

    bornes = {
        "TRAIN_START": train_start,
        "TRAIN_END": train_end,
        "CALIB_START": calib_start,
        "CALIB_END": calib_end,
        "TEST_START": test_start,
        "TEST_END": test_end,
    }
    # Sans tzinfo dans la chaîne (pas de +00:00) : cohérent avec le format
    # attendu par pd.Timestamp(..., tz="UTC") dans core/data.py, qui refuse un
    # offset explicite combiné à tz= explicite.
    return {cle: valeur.strftime("%Y-%m-%dT%H:%M:%S") for cle, valeur in bornes.items()}


def resoudre_fenetres() -> dict[str, str]:
    """Utilise les 6 variables d'environnement si TOUTES sont explicitement
    définies (essai manuel sur une fenêtre précise) ; sinon calcule des
    fenêtres glissantes par rapport à maintenant (usage normal, cron mensuel)."""
    if all(os.environ.get(var) for var in DATE_ENV_VARS):
        return {var: os.environ[var] for var in DATE_ENV_VARS}
    return fenetres_glissantes()


def main(site_id: str, fenetres: dict[str, str]) -> str:
    print(f"--- Entraînement pour {site_id} ---")

    # 1. Chargement
    df = load_gold_hourly(
        site_id=site_id,
        start=fenetres["TRAIN_START"],
        end=fenetres["TEST_END"],
    )
    print(f"{len(df)} lignes chargées, qualité : {dict(df['data_quality'].value_counts())}")

    train, calib, test = split_train_calib_test(
        df,
        fenetres["TRAIN_START"], fenetres["TRAIN_END"],
        fenetres["CALIB_START"], fenetres["CALIB_END"],
        fenetres["TEST_START"], fenetres["TEST_END"],
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
        train_start=fenetres["TRAIN_START"],
        train_end=fenetres["TRAIN_END"],
        mase_towt=mase_towt,
        mase_lgbm=mase_lgbm,
        conformal_margin_90=margin,
        coverage=coverage,
        towt_model=towt_model,
        lgbm_model=lgbm_model,
    )
    print(f"\n>>> Modèle promu pour {site_id} : {promoted}")
    return promoted


def main_tous_les_sites(fenetres: dict[str, str]) -> dict[str, str]:
    """Boucle sur tous les sites détectés dans le gold vers la fin de la
    fenêtre de test (là où on est sûr d'avoir de la donnée récente) — pas de
    liste codée en dur. Un site en échec (ex. gold trop creux ce mois-ci) est
    loggé mais n'empêche pas les autres de s'entraîner."""
    sites = list_available_sites(reference_date=fenetres["TEST_END"][:10])
    if not sites:
        raise SystemExit("Aucun site détecté dans le gold sur la fenêtre de test — rien à réentraîner.")
    print(f"{len(sites)} site(s) détecté(s) : {', '.join(sites)}")

    resultats = {}
    for site_id in sites:
        try:
            resultats[site_id] = main(site_id, fenetres)
        except Exception as erreur:  # un site en échec ne doit jamais bloquer les autres
            print(f"⚠️  {site_id} : échec du réentraînement — {erreur}")
            resultats[site_id] = "erreur"
    return resultats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--site",
        default=os.environ.get("DEFAULT_SITE_ID"),
        help="Un seul site. Omis (et DEFAULT_SITE_ID non défini) : tous les sites détectés dans le gold.",
    )
    args = parser.parse_args()

    fenetres = resoudre_fenetres()
    print(
        f"Fenêtres : train [{fenetres['TRAIN_START']} -> {fenetres['TRAIN_END']}] "
        f"· calib [{fenetres['CALIB_START']} -> {fenetres['CALIB_END']}] "
        f"· test [{fenetres['TEST_START']} -> {fenetres['TEST_END']}]"
    )

    if args.site:
        main(args.site, fenetres)
    else:
        main_tous_les_sites(fenetres)
