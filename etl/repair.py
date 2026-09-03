"""
Reprise des fenêtres d'historique rapatriées à vide.

L'API mock simule des pannes de capteurs VIVANTES (`GET /api/v1/sensors/status`, avec un
`failing_until` par capteur) et les applique aux relevés historiques au moment du fetch :
`GET /api/v1/readings` renvoie `consumption_kw: null` et `data_quality: "critical"` pour
toute la période demandée si le capteur est en panne à l'instant de l'appel. Un backfill
lancé pendant une panne ramène donc des mois de lectures vides — sur un échantillon de
6 330 lignes silver, 6 254 étaient `critical` / `network_loss`, toutes métriques nulles.

Ces lectures ne sont pas perdues pour de bon : l'endpoint est stochastique (trois appels sur
la même fenêtre renvoient trois valeurs différentes) et les capteurs se rétablissent. Il
suffit de rejouer la fenêtre pendant une phase saine.

Ce n'est PAS un job planifié : on le lance à la main, ou en boucle avec --attente, quand on
veut consolider le jeu d'entraînement.

Marche à suivre :
    1. repérer les partitions (record_date, site_id) dont le gold journalier n'a AUCUNE
       consommation moyenne — c'est-à-dire aucun relevé exploitable de la journée ;
    2. n'appeler l'API que pour les sites dont les capteurs `network` et `consumption` sont
       au vert, sinon on ne ferait que réécrire du vide. L'état est relu au fil du parcours
       (voir EtatCapteurs) : les `failing_until` se comptent en dizaines de secondes, un
       site vert au lancement ne l'est plus quelques partitions plus loin ;
    3. ne remplacer une partition QUE si le nouveau tirage rapporte des mesures : à défaut
       on n'y touche pas, une partition vide vaut mieux qu'une partition supprimée ;
    4. purger l'ancien bronze vide et les batches silver de la partition, puis déposer les
       nouvelles lectures. Sans cette purge, les anciennes lignes vides et les nouvelles
       coexisteraient sous des clés différentes et gonfleraient records_count ;
    5. retirer les clés purgées de l'état incrémental et empiler la partition pour le gold.

Le bucket audit (WORM) n'est jamais touché : l'empreinte SHA-256 de ce qui a réellement été
reçu au premier passage reste vérifiable, même après remplacement du bronze.

Installation :
    pip install -r requirements.txt

Usage :
    python repair.py --dry-run                    # ce qui serait rejoué, sans rien écrire
    python repair.py                              # rejoue les capteurs actuellement au vert
    python repair.py --site SITE001 --site SITE002
    python repair.py --debut 2025-08-01 --fin 2025-09-01
    python repair.py --attente 1800               # réessaie 30 min le temps que ça revienne
    python quality.py && python quality.py --gold-only   # ensuite, pour propager
"""

import argparse
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import requests
from botocore.exceptions import BotoCoreError, ClientError

import collect
import history
import quality
import storage

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

# Capteurs dont dépend la présence de `consumption_kw` dans /api/v1/readings. Exiger que
# TOUS les capteurs du site soient au vert serait trop strict — température et humidité
# tombent en panne indépendamment et ne nous empêchent pas de récupérer une consommation.
CAPTEURS_REQUIS = ("network", "consumption")

# Pause entre deux tentatives quand --attente est demandé. Les `failing_until` observés se
# comptent en dizaines de secondes : inutile de marteler l'API.
INTERVALLE_ATTENTE_SECONDES = 30

# Fraîcheur de l'état des capteurs pendant le parcours des partitions. Assez court pour
# suivre le clignotement, assez long pour ne pas ajouter un appel par partition.
TTL_ETAT_CAPTEURS = 15


def capteurs_sains(api_base=API_BASE):
    """Sites dont les capteurs de CAPTEURS_REQUIS sont tous au vert, maintenant.

    Renvoie un ensemble vide si l'API ne répond pas : sans état capteur fiable on préfère ne
    rien rejouer plutôt que réécrire du vide par-dessus du vide."""
    try:
        reponse = requests.get(f"{api_base}/api/v1/sensors/status", timeout=30)
        reponse.raise_for_status()
        etat = reponse.json()
    except (requests.RequestException, ValueError) as erreur:
        print(f"État des capteurs indisponible, aucune reprise possible : {erreur}")
        return set()

    sains = set()
    for site_id, details in (etat or {}).items():
        capteurs = (details or {}).get("sensors") or {}
        if all((capteurs.get(nom) or {}).get("status") == "ok" for nom in CAPTEURS_REQUIS):
            sains.add(str(site_id))
    return sains


class EtatCapteurs:
    """Vue rafraîchie de l'état des capteurs, consultée partition par partition.

    Les `failing_until` observés se comptent en dizaines de secondes : un site vert au
    lancement ne l'est plus quelques partitions plus loin. Interroger l'état une seule fois
    au départ, puis dérouler des centaines de partitions, revient à tirer presque tout
    pendant des pannes — les garde-fous empêchent d'abîmer quoi que ce soit, mais chaque
    appel est perdu. On relit donc l'état au plus toutes les TTL secondes."""

    def __init__(self, ttl_secondes=None, api_base=API_BASE):
        # Résolue à la construction et non dans la signature : une valeur par défaut est
        # figée à la définition de la fonction, ce qui rendrait la TTL non surchargeable.
        self._ttl = TTL_ETAT_CAPTEURS if ttl_secondes is None else ttl_secondes
        self._api_base = api_base
        self._sains = set()
        self._lu_a = None

    def sains(self):
        if self._lu_a is None or time.monotonic() - self._lu_a >= self._ttl:
            self._sains = capteurs_sains(self._api_base)
            self._lu_a = time.monotonic()
        return self._sains

    def est_sain(self, site_id):
        return site_id in self.sains()


def _partition_depuis_cle(cle):
    """('2025-08-02', 'SITE001') depuis 'daily/record_date=2025-08-02/site_id=SITE001/…'."""
    segments = cle.split("/")
    if len(segments) < 3:
        return None
    if not segments[1].startswith("record_date=") or not segments[2].startswith("site_id="):
        return None
    return segments[1][len("record_date="):], segments[2][len("site_id="):]


def partitions_a_rejouer(s3, gold_bucket, *, sites=None, debut=None, fin=None, workers=16):
    """Partitions (record_date, site_id) du gold journalier sans aucune consommation.

    Le critère est `avg_consumption_kw` nul : une moyenne vide ne peut venir que d'une
    journée dont TOUS les relevés ont une consommation absente. Il vaut aussi bien sur les
    agrégats écrits par les versions précédentes du pipeline, qui n'ont pas de colonne
    `usable_count`.

    Une partition dont le gold n'a jamais été calculé n'est pas vue ici : lancer
    `python quality.py --gold-only` avant, sinon elle sera simplement ignorée."""
    cles = [
        cle for cle in quality.s3_list_keys(s3, gold_bucket, "daily/")
        if cle.endswith(".parquet")
    ]

    candidates = []
    for cle in cles:
        partition = _partition_depuis_cle(cle)
        if partition is None:
            continue
        record_date, site_id = partition
        if sites and site_id not in sites:
            continue
        if debut and record_date < debut:
            continue
        if fin and record_date >= fin:
            continue
        candidates.append((cle, record_date, site_id))

    if not candidates:
        return []

    def est_vide(candidat):
        cle, record_date, site_id = candidat
        try:
            df = quality.s3_read_parquet(s3, gold_bucket, cle)
        except (BotoCoreError, ClientError) as erreur:
            print(f"Agrégat {cle} illisible, partition ignorée : {erreur}")
            return None
        if "avg_consumption_kw" not in df.columns or df["avg_consumption_kw"].notna().any():
            return None
        return (record_date, site_id)

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(candidates)))) as pool:
        resultats = pool.map(est_vide, candidates)

    return sorted({partition for partition in resultats if partition is not None})


def _porte_une_mesure(lecture):
    return any(lecture.get(colonne) is not None for colonne in quality.NUMERIC_COLUMNS)


def recuperer_journee(site_id, record_date, pas_minutes):
    """Relevés d'une journée pour un site, au pas demandé. Liste vide si l'API ne répond pas."""
    try:
        debut = datetime.strptime(record_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"Date de partition illisible, ignorée : {record_date}")
        return []

    points = max(1, int(24 * 60 / pas_minutes))
    try:
        return history.recuperer_historique(
            site_id, debut, debut + timedelta(days=1), points, pas_minutes,
        )
    except requests.RequestException as erreur:
        print(f"{site_id} {record_date} : relecture impossible, {erreur}")
        return []


def purger_partition(s3, site_id, record_date, *, bronze_bucket, silver_bucket):
    """Supprime le bronze vide et les batches silver d'une partition. Renvoie les clés
    bronze supprimées, à retirer de l'état incrémental par l'appelant.

    N'est appelée qu'une fois le nouveau tirage en main et vérifié non vide : on ne détruit
    jamais une partition sans avoir de quoi la remplacer."""
    cles_bronze = quality.s3_list_keys(s3, bronze_bucket, f"{site_id}/{record_date}/")
    for cle in cles_bronze:
        s3.delete_object(Bucket=bronze_bucket, Key=cle)

    for cle in quality.s3_list_keys(s3, silver_bucket, f"record_date={record_date}/site_id={site_id}/"):
        s3.delete_object(Bucket=silver_bucket, Key=cle)

    return cles_bronze


def rejouer_partition(s3, site_id, record_date, *, pas_minutes, bronze_bucket, silver_bucket, dry_run=False):
    """Rejoue une partition. Renvoie (mesures_deposees, cles_bronze_purgees).

    (0, []) si le nouveau tirage n'apporte rien : la partition est laissée telle quelle."""
    lectures = [
        lecture for lecture in recuperer_journee(site_id, record_date, pas_minutes)
        if isinstance(lecture, dict) and lecture.get("timestamp") and _porte_une_mesure(lecture)
    ]
    if not lectures:
        return 0, []

    if dry_run:
        return len(lectures), []

    purgees = purger_partition(
        s3, site_id, record_date, bronze_bucket=bronze_bucket, silver_bucket=silver_bucket,
    )

    deposees = 0
    for lecture in lectures:
        lecture.setdefault("site_id", site_id)
        try:
            collect.envoyer_mesure(site_id, lecture)
            deposees += 1
        except (BotoCoreError, ClientError) as erreur:
            print(f"{site_id} {record_date} : lecture {lecture.get('timestamp')} non écrite, {erreur}")

    return deposees, purgees


def build_parser():
    parser = argparse.ArgumentParser(
        description="Rejoue les fenêtres d'historique rapatriées à vide, quand les capteurs sont revenus.",
    )
    parser.add_argument("--bronze-bucket", default=os.environ.get("MINIO_BUCKET_BRONZE", "bronze"))
    parser.add_argument("--silver-bucket", default=os.environ.get("MINIO_BUCKET_SILVER", "silver"))
    parser.add_argument("--gold-bucket", default=os.environ.get("MINIO_BUCKET_GOLD", "gold"))
    parser.add_argument("--manifests-bucket", default=os.environ.get("MINIO_BUCKET_MANIFESTS", "manifests"))
    parser.add_argument("--state-key", default=os.environ.get("ETL_STATE_KEY", "etl_state.json"))
    parser.add_argument(
        "--pending-gold-key",
        default=os.environ.get("ETL_PENDING_GOLD_KEY", "gold_pending.json"),
    )
    parser.add_argument(
        "--site", action="append", dest="sites", default=None,
        help="Restreint la reprise à ce site (répétable). Défaut : tous.",
    )
    parser.add_argument("--debut", default=None, help="Ne rejouer qu'à partir de cette date (YYYY-MM-DD).")
    parser.add_argument(
        "--fin", default=None,
        help="Ne rejouer que strictement avant cette date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--pas-minutes",
        type=int,
        default=int(os.environ.get("HISTORY_PAS_MINUTES", str(history.PAS_MINUTES_DEFAUT))),
        help=f"Intervalle entre deux relevés rejoués (défaut {history.PAS_MINUTES_DEFAUT}).",
    )
    parser.add_argument(
        "--max-partitions", type=int, default=0,
        help="Nombre max de partitions traitées par exécution (0 = pas de limite).",
    )
    parser.add_argument(
        "--attente", type=int, default=0,
        help="Secondes à patienter, au total, que les capteurs reviennent au vert (0 = ne pas attendre).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Liste ce qui serait rejoué sans rien écrire ni supprimer.",
    )
    return parser


def attendre_capteurs(etat, sites_voulus, attente_secondes):
    """Patiente jusqu'à ce qu'au moins un site visé soit au vert, dans la limite du budget.
    Renvoie l'ensemble des sites sains (vide si le budget est épuisé sans rétablissement)."""
    echeance = time.monotonic() + max(0, attente_secondes)
    while True:
        sains = set(etat.sains())
        if sites_voulus:
            sains &= set(sites_voulus)
        if sains or time.monotonic() >= echeance:
            return sains
        restant = int(echeance - time.monotonic())
        print(
            f"Capteurs tous en panne, nouvelle tentative dans "
            f"{INTERVALLE_ATTENTE_SECONDES}s ({restant}s restantes)"
        )
        time.sleep(min(INTERVALLE_ATTENTE_SECONDES, max(1, restant)))


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.pas_minutes < 1:
        print(f"--pas-minutes doit valoir au moins 1 : {args.pas_minutes}")
        return 1

    try:
        s3 = storage.get_s3()
    except KeyError as erreur:
        print(f"Variable d'environnement MinIO manquante : {erreur}")
        return 1

    etat = EtatCapteurs()
    sains = attendre_capteurs(etat, args.sites, args.attente)
    if not sains:
        print("Aucun site dont les capteurs network + consumption soient au vert : rien à rejouer.")
        return 0
    print(f"Capteurs au vert au démarrage : {', '.join(sorted(sains))}")

    # Le listing porte sur les sites DEMANDÉS, pas sur ceux verts à l'instant du listing :
    # l'état est revérifié partition par partition, et un site rouge maintenant sera peut-être
    # vert dans dix secondes, une fois quelques partitions parcourues.
    try:
        partitions = partitions_a_rejouer(
            s3, args.gold_bucket,
            sites=set(args.sites) if args.sites else None,
            debut=args.debut, fin=args.fin,
        )
    except (BotoCoreError, ClientError) as erreur:
        print(f"Impossible de lister le gold '{args.gold_bucket}' : {erreur}")
        return 1

    if not partitions:
        print("Aucune partition sans consommation à rejouer.")
        return 0

    if args.max_partitions > 0:
        partitions = partitions[: args.max_partitions]

    print(f"{len(partitions)} partition(s) sans consommation à rejouer, pas={args.pas_minutes} min")
    if args.dry_run:
        for record_date, site_id in partitions:
            lectures, _ = rejouer_partition(
                s3, site_id, record_date,
                pas_minutes=args.pas_minutes,
                bronze_bucket=args.bronze_bucket,
                silver_bucket=args.silver_bucket,
                dry_run=True,
            )
            print(f"  {record_date} {site_id} : {lectures} mesure(s) récupérable(s)")
        return 0

    total_deposees = 0
    reprises = set()
    purgees_totales = set()
    ignorees = 0
    for record_date, site_id in partitions:
        # Relu au fil de l'eau : un site vert au démarrage ne l'est souvent plus quelques
        # partitions plus loin, et tirer pendant sa panne ne ramènerait que du vide.
        if not etat.est_sain(site_id):
            ignorees += 1
            continue
        deposees, purgees = rejouer_partition(
            s3, site_id, record_date,
            pas_minutes=args.pas_minutes,
            bronze_bucket=args.bronze_bucket,
            silver_bucket=args.silver_bucket,
        )
        if not deposees:
            continue
        total_deposees += deposees
        reprises.add((record_date, site_id))
        purgees_totales.update(purgees)
        print(
            f"  {record_date} {site_id} : {deposees} mesure(s) rejouée(s), "
            f"{len(purgees)} objet(s) purgé(s)"
        )

    if ignorees:
        print(f"{ignorees} partition(s) sautée(s), capteur en panne au moment du passage.")

    if not reprises:
        print("Aucune partition n'a pu être améliorée : les capteurs sont retombés en panne.")
        return 0

    # Les clés purgées doivent quitter l'état incrémental, sinon quality.py les considère
    # traitées et ne reprend jamais leur remplacement.
    etat = quality.load_state(s3, args.manifests_bucket, args.state_key)
    quality.save_state(s3, args.manifests_bucket, args.state_key, etat - purgees_totales)
    quality.add_pending_gold(s3, args.manifests_bucket, args.pending_gold_key, reprises)

    print(
        f"Reprise terminée | partitions={len(reprises)} | mesures={total_deposees} | "
        f"objets purgés={len(purgees_totales)}"
    )
    print("Lancez maintenant `python quality.py` puis `python quality.py --gold-only`.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
