"""
Inférence batch des modèles de prévision : un modèle Staging par site dans le registry MLflow
(persistés par scripts/train_v1_csv.py), une prévision horaire écrite dans la table
predictions_forecast de Postgres (infra/postgres/init/06_predictions.sql).

Ce que ces modèles savent faire, et ce qu'ils ne savent pas :
  - ils sont calendaires — heure de la semaine + météo, aucun retard de consommation en
    entrée. L'horizon ne coûte donc rien (prédire +1 h ou +7 j est le même calcul, et la même
    précision), mais la résolution est verrouillée à l'heure : C(time_of_week) n'a que 168
    créneaux, 10h00 et 10h50 tombent sur le même coefficient. D'où step_minutes = 60 en base.
  - ils ont été entraînés sur le CSV synthétique 2023-2024 (tag 'caveat' des runs) : chaque
    ligne écrite porte data_source et model_version pour que personne ne les prenne pour des
    prévisions validées sur donnée réelle.

Les fonctions de prédiction sont importées de scripts/train_csv_experiment plutôt que
réécrites : le chemin de code doit être exactement celui de l'entraînement, sinon les noms de
colonnes divergent et patsy échoue — ou LightGBM prédit sur des colonnes décalées sans rien
signaler.

Les valeurs prédites ne sont pas bornées à zéro. Une régression peut sortir du négatif en
extrapolation, et l'écraser silencieusement à 0 ferait passer un modèle cassé pour un modèle
prudent : le compte des valeurs négatives est affiché dans le résumé de run à la place.

Usage :
    python predict.py                       # tous les sites, horizon PREDICT_HORIZON_HOURS
    python predict.py --site SITE001 --horizon-hours 168
    python predict.py --dry-run             # calcule et affiche, n'écrit rien
    python predict.py --check --days 14     # backtest de contrôle sur le gold réel
    python predict.py --check --probe-tz    # + compare les fuseaux candidats

Environment :
    MLFLOW_TRACKING_URI          http://mlflow:5000 en interne, http://10.105.200.44:5000 depuis un poste
    MINIO_ENDPOINT               avec ou sans schéma ("http://minio:9000" ou "minio:9000")
    MINIO_ACCESS_KEY / MINIO_SECRET_KEY
    POSTGRES_HOST / PORT / DB / USER / PASSWORD
    PREDICT_HORIZON_HOURS        défaut 24
    PREDICT_CLIMATOLOGY_DAYS     défaut 30, fenêtre de gold pour estimer la météo future
    PREDICT_TZ                   défaut UTC, fuseau de lecture de l'heure de la semaine
    PREDICT_MODEL_STAGE          défaut Production (les 7 modèles y ont été promus)
"""

import argparse
import os
import sys
import warnings

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

warnings.filterwarnings("ignore")

REQUIRED_ENV = ("MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "POSTGRES_PASSWORD")
_absent_env = [name for name in REQUIRED_ENV if not os.environ.get(name)]
if _absent_env:
    sys.exit(
        f"Variables d'environnement manquantes : {', '.join(_absent_env)}.\n"
        "Les credentials MinIO sont obligatoires : le client MLflow télécharge les artifacts "
        "directement depuis le bucket, le serveur ne fait pas relai."
    )


def _minio_endpoint_url() -> str:
    """MINIO_ENDPOINT est tantôt porté avec schéma (.env racine), tantôt sans (services etl du
    compose). MLFLOW_S3_ENDPOINT_URL en exige un, faute de quoi boto3 part en https par défaut
    et le download d'artifact échoue sur un timeout illisible."""
    endpoint = os.environ["MINIO_ENDPOINT"]
    if "://" in endpoint:
        return endpoint
    scheme = "https" if os.environ.get("MINIO_USE_SSL", "false").lower() == "true" else "http"
    return f"{scheme}://{endpoint}"


os.environ.setdefault("AWS_ACCESS_KEY_ID", os.environ["MINIO_ACCESS_KEY"])
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", os.environ["MINIO_SECRET_KEY"])
os.environ.setdefault("MLFLOW_S3_ENDPOINT_URL", _minio_endpoint_url())

import mlflow  # noqa: E402
import mlflow.lightgbm  # noqa: E402
import mlflow.statsmodels  # noqa: E402
import pandas as pd  # noqa: E402
from apscheduler.schedulers.blocking import BlockingScheduler  # noqa: E402
from apscheduler.triggers.cron import CronTrigger  # noqa: E402
from core.features import (  # noqa: E402
    UNAVAILABLE_IN_GOLD,
    build_future_frame,
    calendar_features,
    missing_features,
)
from core.postgres_store import (  # noqa: E402
    load_gold_actuals,
    load_weather_climatology,
    make_pg_connection,
    write_predictions,
)
from mlflow.exceptions import MlflowException  # noqa: E402
from mlflow.tracking import MlflowClient  # noqa: E402
from scripts.train_csv_experiment import LGBM_FEATURES, SITES, predict_lgbm, predict_ols  # noqa: E402

MLFLOW_TRACKING_URI = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
MODEL_STAGE = os.environ.get("PREDICT_MODEL_STAGE", "Production")
HORIZON_HOURS = int(os.environ.get("PREDICT_HORIZON_HOURS", "24"))
CLIMATOLOGY_DAYS = int(os.environ.get("PREDICT_CLIMATOLOGY_DAYS", "30"))
PREDICT_TZ = os.environ.get("PREDICT_TZ", "UTC")
# Calé après le recalcul gold horaire (GOLD_CRON_HORAIRE, H+3) : la prévision doit voir les
# agrégats de l'heure écoulée, pas ceux d'avant.
PREDICT_CRON = os.environ.get("PREDICT_CRON", "20 * * * *")
HEARTBEAT_PATH = os.environ.get("HEARTBEAT_PATH", "/tmp/heartbeat")

# Les modèles V1 sont horaires par construction (voir le docstring) : ce n'est pas un réglage.
STEP_MINUTES = 60

# Candidats testés par --probe-tz. Le CSV d'entraînement n'étant pas versionné, on ne sait pas
# si ses timestamps étaient en UTC ou en heure locale ; le backtest tranche sur donnée réelle.
PROBE_TIMEZONES = ("UTC", "Europe/Paris")


class FeatureGap(Exception):
    """Le modèle réclame une entrée que le gold ne sait pas fournir."""


def model_name_for(site_id: str) -> str:
    return f"enervision-forecast-{site_id.lower()}"


def load_champion(client: MlflowClient, site_id: str, stage: str):
    """Charge le modèle promu du site avec le flavor qui l'a produit, plus ce qu'il faut de son
    run pour tracer la ligne en base (version, marge conforme, provenance des données)."""
    name = model_name_for(site_id)
    # Les erreurs de registry sont ramenées sur LookupError : un modèle absent ou un run
    # supprimé n'est pas une panne du job (un site nouvellement créé n'a pas encore de modèle),
    # et l'appelant n'a ainsi que deux causes de mise à l'écart d'un site à connaître. Les
    # échecs de load_model, eux, restent bruyants : un artifact qui ne se télécharge pas est un
    # problème de déploiement, et la trace boto3 est le diagnostic utile.
    try:
        versions = client.get_latest_versions(name, stages=[stage])
        if not versions:
            raise LookupError(f"aucune version en stage '{stage}' pour le modèle '{name}'")
        version = versions[0]
        run = client.get_run(version.run_id)
    except MlflowException as erreur:
        raise LookupError(f"registry MLflow : {erreur.message}") from erreur

    champion = run.data.params.get("champion", "")

    # Flavor natif et non pyfunc : on veut retrouver l'objet statsmodels/LightGBM d'origine
    # pour le passer aux fonctions de prédiction de l'entraînement, sans wrapper intermédiaire
    # susceptible de réordonner ou renommer les colonnes.
    flavor = mlflow.lightgbm if champion == "lightgbm" else mlflow.statsmodels
    model = flavor.load_model(f"models:/{name}/{version.version}")

    meta = {
        "model_name": name,
        "model_version": str(version.version),
        "model_stage": version.current_stage,
        "champion": champion,
        "run_id": version.run_id,
        "data_source": run.data.params.get("data_source"),
        "conformal_margin_90": run.data.metrics.get("conformal_margin_90"),
    }
    return model, meta


def required_features(model, champion: str) -> list[str]:
    """Colonnes que le modèle exigera. Pour LightGBM on interroge le modèle lui-même
    (feature_name_) au lieu de la constante LGBM_FEATURES : un modèle réentraîné sans
    irradiance redeviendra ainsi inférable sans toucher à ce script."""
    if champion != "lightgbm":
        # patsy dérive le reste de la formule ; time_of_week est construit par predict_ols.
        return ["temperature_celsius"]
    names = getattr(model, "feature_name_", None)
    return list(names) if names else list(LGBM_FEATURES)


def check_features(frame: pd.DataFrame, model, champion: str) -> None:
    absent = missing_features(frame, required_features(model, champion))
    if not absent:
        return
    hors_gold = [feature for feature in absent if feature in UNAVAILABLE_IN_GOLD]
    reste = [feature for feature in absent if feature not in UNAVAILABLE_IN_GOLD]
    morceaux = []
    if hors_gold:
        morceaux.append(f"{', '.join(hors_gold)} (aucune table du gold ne la porte)")
    if reste:
        morceaux.append(f"{', '.join(reste)} (absente du frame d'inférence)")
    raise FeatureGap("features manquantes : " + " ; ".join(morceaux))


def predict_frame(model, champion: str, frame: pd.DataFrame) -> pd.Series:
    return predict_lgbm(model, frame) if champion == "lightgbm" else predict_ols(model, frame)


def forecast_site(conn, client: MlflowClient, site_id: str, horizon_hours: int, tz: str, stage: str):
    """Prévision d'un site sur [prochaine heure pleine, +horizon_hours[."""
    model, meta = load_champion(client, site_id, stage)
    slots, means = load_weather_climatology(conn, site_id, CLIMATOLOGY_DAYS, tz)

    start = pd.Timestamp.now(tz="UTC").floor("h") + pd.Timedelta(hours=1)
    end = start + pd.Timedelta(hours=horizon_hours)
    frame = build_future_frame(start, end, tz, slots, means)
    check_features(frame, model, meta["champion"])

    predictions = predict_frame(model, meta["champion"], frame)
    margin = meta["conformal_margin_90"]

    metadata_columns = ("model_name", "model_version", "model_stage", "champion", "run_id", "data_source")
    rows = pd.DataFrame(
        {
            "site_id": site_id,
            "target_ts": frame["timestamp"].to_numpy(),
            "step_minutes": STEP_MINUTES,
            "predicted_kwh": predictions.to_numpy(),
            "temperature_celsius": frame["temperature_celsius"].to_numpy(),
            "temperature_source": frame["temperature_source"].to_numpy(),
            "horizon_h": horizon_hours,
            **{key: meta[key] for key in metadata_columns},
        }
    )
    rows["lower_90"] = None if margin is None else rows["predicted_kwh"] - margin
    rows["upper_90"] = None if margin is None else rows["predicted_kwh"] + margin
    return rows, start, meta


def backtest_site(conn, client: MlflowClient, site_id: str, days: int, tz: str, stage: str):
    """
    Rejoue le modèle sur les `days` derniers jours de gold, avec la température réellement
    mesurée, et compare au réalisé.

    C'est le seul garde-fou contre les hypothèses invérifiables autrement : convention de
    day_of_week, fuseau des timestamps d'entraînement, échelle des unités. Un MAPE absurde ici
    signale une de ces hypothèses fausse, pas forcément un mauvais modèle.
    """
    model, meta = load_champion(client, site_id, stage)
    end = pd.Timestamp.now(tz="UTC").floor("h")
    start = end - pd.Timedelta(days=days)

    actuals = load_gold_actuals(conn, site_id, start, end)
    if actuals.empty:
        return None

    frame = calendar_features(pd.DatetimeIndex(actuals["timestamp"]), tz)
    frame["temperature_celsius"] = actuals["temperature_celsius"].to_numpy()
    frame["humidity_percent"] = actuals["humidity_percent"].to_numpy()
    # Heures sans température mesurée : comblées par la médiane de la fenêtre, sinon patsy
    # renvoie NaN sur ces lignes et le MAPE devient incalculable au lieu d'être dégradé.
    temp_imputee = int(frame["temperature_celsius"].isna().sum())
    if temp_imputee:
        frame["temperature_celsius"] = frame["temperature_celsius"].fillna(frame["temperature_celsius"].median())
    check_features(frame, model, meta["champion"])

    predicted = predict_frame(model, meta["champion"], frame).to_numpy()
    observed = actuals["consumption_kwh"].to_numpy()
    error = predicted - observed
    non_zero = observed != 0
    mape = float((abs(error[non_zero]) / abs(observed[non_zero])).mean() * 100) if non_zero.any() else float("nan")

    return {
        "site_id": site_id,
        "champion": meta["champion"],
        "n": len(observed),
        "mae": float(abs(error).mean()),
        "mape": mape,
        "bias": float(error.mean()),
        "temp_imputee": temp_imputee,
    }


def _resume_site(site_id: str, rows: pd.DataFrame, meta: dict) -> str:
    margin = meta["conformal_margin_90"]
    marge = f"marge ±{margin:.1f}" if margin is not None else "marge absente du run"
    resume = (
        f"[{site_id}] {meta['champion']} v{meta['model_version']} · {len(rows)} h · "
        f"moyenne {rows['predicted_kwh'].mean():.1f} kWh · {marge}"
    )
    negatives = int((rows["predicted_kwh"] < 0).sum())
    if negatives:
        resume += f" · /!\\ {negatives} valeurs négatives"
    repli = int((rows["temperature_source"] != "climatology_gold").sum())
    if repli:
        resume += f" · {repli} h de température en repli"
    return resume


def run_forecasts(conn, client: MlflowClient, sites: list[str], args) -> int:
    ecrites, ignores = 0, []
    for site_id in sites:
        try:
            rows, start, meta = forecast_site(conn, client, site_id, args.horizon_hours, args.tz, args.stage)
        except (FeatureGap, LookupError) as erreur:
            ignores.append((site_id, str(erreur)))
            print(f"[{site_id}] ignoré — {erreur}")
            continue

        print(_resume_site(site_id, rows, meta))
        if args.dry_run:
            apercu = rows[["target_ts", "predicted_kwh", "lower_90", "upper_90", "temperature_celsius"]]
            print(apercu.head(6).to_string(index=False))
            continue

        ecrites += write_predictions(conn, rows, site_id, STEP_MINUTES, start)

    action = "calculées (dry-run, rien écrit)" if args.dry_run else "écrites dans predictions_forecast"
    print(f"\n>>> {ecrites} lignes {action} · {len(sites) - len(ignores)}/{len(sites)} sites")
    if ignores:
        print(">>> Sites non inférables :")
        for site_id, raison in ignores:
            print(f"      {site_id} : {raison}")
    # Sortie en erreur seulement si aucun site n'a abouti : un site non inférable est un état
    # connu (irradiance absente du gold), pas une panne du job.
    return 0 if len(ignores) < len(sites) else 1


def run_backtests(conn, client: MlflowClient, sites: list[str], args) -> int:
    timezones = list(PROBE_TIMEZONES) if args.probe_tz else [args.tz]
    for tz in timezones:
        print(f"\n=== Backtest sur {args.days} j de gold réel · fuseau {tz} ===")
        for site_id in sites:
            try:
                resultat = backtest_site(conn, client, site_id, args.days, tz, args.stage)
            except (FeatureGap, LookupError) as erreur:
                print(f"  {site_id} ignoré — {erreur}")
                continue
            if resultat is None:
                print(f"  {site_id} : aucune heure de gold sur la fenêtre")
                continue
            imputee = f"  ({resultat['temp_imputee']} h de temp. imputée)" if resultat["temp_imputee"] else ""
            print(
                f"  {resultat['site_id']}  {resultat['champion']:<9} n={resultat['n']:<5} "
                f"MAE={resultat['mae']:>8.1f}  MAPE={resultat['mape']:>6.1f}%  "
                f"biais={resultat['bias']:>+8.1f}{imputee}"
            )
    if args.probe_tz:
        print("\n>>> Retenir le fuseau au MAPE le plus bas et le fixer dans PREDICT_TZ.")
    print(">>> Backtest : aucune écriture en base.")
    return 0


def _marquer_vivant() -> None:
    """Touche un fichier pour le HEALTHCHECK Docker (même mécanique que etl/collect.py).
    Un job qui ne tourne qu'une fois par heure n'a rien d'autre à montrer entre deux runs."""
    with open(HEARTBEAT_PATH, "w", encoding="utf-8") as fichier:
        fichier.write(pd.Timestamp.now(tz="UTC").isoformat())


def cycle_prevision(sites: list[str], args) -> None:
    """
    Un cycle du planificateur. Aucune exception ne tue le scheduler : un MLflow indisponible
    ou un Postgres qui redémarre doit se rattraper au déclenchement suivant, pas arrêter le
    service jusqu'à intervention.

    La connexion Postgres est ouverte par cycle et refermée aussitôt : gardée ouverte 59
    minutes entre deux runs, elle finirait coupée par le serveur ou un pare-feu, et l'erreur
    n'apparaîtrait qu'au cycle suivant.

    Le heartbeat n'est touché que si le cycle a produit quelque chose : « en bonne santé »
    doit vouloir dire « écrit des prévisions », pas « processus encore vivant ».
    """
    try:
        conn = make_pg_connection()
        try:
            if run_forecasts(conn, MlflowClient(), sites, args) == 0:
                _marquer_vivant()
        finally:
            conn.close()
    except Exception as erreur:
        print(f"Cycle de prévision en échec, nouvelle tentative au prochain déclenchement : {erreur}")


def run_scheduled(sites: list[str], args) -> int:
    """Mode service : un premier cycle une minute après le démarrage, puis à chaque
    déclenchement de PREDICT_CRON.

    Ce cycle d'amorçage évite une table vide pendant toute l'heure qui suit un déploiement. Il
    est décalé d'une minute et non immédiat parce que depends_on ne garantit que le démarrage
    du conteneur mlflow, pas que son serveur écoute déjà — l'image officielle installe encore
    psycopg2 à ce moment-là."""
    print(f"Planificateur : horizon {args.horizon_hours} h, cron '{PREDICT_CRON}' (UTC)")
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        cycle_prevision,
        CronTrigger.from_crontab(PREDICT_CRON, timezone="UTC"),
        args=[sites, args],
        next_run_time=(pd.Timestamp.now(tz="UTC") + pd.Timedelta(seconds=60)).to_pydatetime(),
        # max_instances=1 + coalesce : si un cycle déborde, on saute les occurrences manquées
        # au lieu de les empiler.
        max_instances=1,
        coalesce=True,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Planificateur arrêté")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inférence batch des modèles de prévision par site.")
    parser.add_argument("--site", help="un seul site (défaut : tous)")
    parser.add_argument("--horizon-hours", type=int, default=HORIZON_HOURS)
    parser.add_argument("--tz", default=PREDICT_TZ)
    parser.add_argument("--stage", default=MODEL_STAGE)
    parser.add_argument("--dry-run", action="store_true", help="calcule et affiche sans écrire")
    parser.add_argument("--check", action="store_true", help="backtest sur le gold réel, n'écrit rien")
    parser.add_argument("--days", type=int, default=14, help="fenêtre du backtest (--check)")
    parser.add_argument("--probe-tz", action="store_true", help="compare les fuseaux candidats (--check)")
    parser.add_argument("--schedule", action="store_true", help="mode service : rejoue selon PREDICT_CRON")
    args = parser.parse_args()

    sites = [args.site] if args.site else list(SITES)
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    client = MlflowClient()
    print(f"MLflow : {MLFLOW_TRACKING_URI} · stage {args.stage} · {len(sites)} site(s)")

    if args.schedule:
        # Le planificateur ouvre et referme sa connexion à chaque cycle (voir cycle_prevision).
        return run_scheduled(sites, args)

    conn = make_pg_connection()
    try:
        if args.check:
            return run_backtests(conn, client, sites, args)
        return run_forecasts(conn, client, sites, args)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
