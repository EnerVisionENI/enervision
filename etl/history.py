"""
Rejeu d'historique : remonte un nombre de mois défini de lectures depuis l'API
(`GET /api/v1/readings`) et les dépose dans la couche bronze sur MinIO, au même
format et avec la même disposition de clés que collect.py.

Ce n'est PAS un job planifié : pas d'APScheduler, pas de heartbeat. On le lance à
la main quand on a besoin d'un jeu d'entraînement (modèle TOWT, vérification GBM
lite), il traite la période demandée puis rend la main.

L'écriture réutilise collect.envoyer_mesure : on obtient gratuitement la copie
d'audit SHA-256 (bucket WORM) et un format bronze strictement identique à la
collecte temps réel, donc quality.py reprend ces objets au prochain passage sans
la moindre modification (il liste les clés bronze, saute celles déjà dans
etl_state.json, et partitionne silver/gold sur le timestamp *contenu* dans chaque
enregistrement : les dates passées atterrissent dans des partitions passées).

Particularité de l'endpoint : il renvoie exactement `limit` relevés RÉPARTIS
UNIFORMÉMENT sur [start_time, end_time]. La résolution est donc pilotée par la
largeur de la fenêtre et par `limit`, pas par l'API. On vise un pas régulier
(`--pas-minutes`, 60 par défaut) : chaque fenêtre est dimensionnée à
`limit * pas_minutes` minutes au plus, et on réclame un point par pas.

Installation :
    pip install -r requirements.txt

Usage :
    python history.py                       # 12 mois, pas horaire, tous les sites
    python history.py --pas-minutes 15      # un relevé toutes les 15 min
    python history.py --mois 6 --site SITE002
    python history.py --debut 2024-01-01T00:00:00 --fin 2024-12-31T23:59:59
    python quality.py                       # ensuite, pour propager bronze -> silver/gold
"""

import argparse
import calendar
import os
from datetime import UTC, datetime, timedelta

import collect
import requests
from botocore.exceptions import BotoCoreError, ClientError

API_BASE = os.environ.get("API_BASE", "http://localhost:8000")

FORMAT_ISO = "%Y-%m-%dT%H:%M:%S"

# Pas d'échantillonnage par défaut, en minutes (60 = ~1 relevé/heure).
PAS_MINUTES_DEFAUT = 60

# Plafond de lignes par appel imposé par l'API.
LIMIT_MAX = 1000

# Clés de premier niveau sous lesquelles l'API peut ranger la liste des lectures
# si elle enveloppe la réponse dans un objet au lieu de renvoyer un tableau nu.
CLES_LISTE = ("readings", "lectures", "data", "results", "items")


def reculer_de_mois(instant, mois):
    """Recule de `mois` mois calendaires. Si le jour n'existe pas dans le mois
    cible (31 mars - 1 mois), on le ramène au dernier jour de ce mois, plutôt que
    de laisser un ValueError casser un backfill à cause d'une date de bordure."""
    total = instant.year * 12 + (instant.month - 1) - mois
    annee, index_mois = divmod(total, 12)
    mois_cible = index_mois + 1
    dernier_jour = calendar.monthrange(annee, mois_cible)[1]
    return instant.replace(year=annee, month=mois_cible, day=min(instant.day, dernier_jour))


def _lire_horodatage(valeur):
    """Parse un timestamp ISO 8601 en datetime aware UTC. Renvoie None si illisible.
    Un timestamp sans fuseau est supposé UTC : l'API mock les émet nus (cf. exemple
    de la doc, `2024-01-15T08:00:00`)."""
    if not valeur:
        return None
    texte = str(valeur).replace("Z", "+00:00")
    try:
        instant = datetime.fromisoformat(texte)
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant


def _extraire_lectures(charge_utile):
    """Normalise la réponse de l'API en liste de lectures, qu'elle renvoie un
    tableau nu ou un objet {"readings": [...]}."""
    if isinstance(charge_utile, list):
        return charge_utile
    if isinstance(charge_utile, dict):
        for cle in CLES_LISTE:
            valeur = charge_utile.get(cle)
            if isinstance(valeur, list):
                return valeur
    return []


def _demander_page(site_id, debut, fin, limit):
    """Un appel GET /api/v1/readings pour une fenêtre donnée."""
    reponse = requests.get(
        f"{API_BASE}/api/v1/readings",
        params={
            "site_id": site_id,
            "start_time": debut.strftime(FORMAT_ISO),
            "end_time": fin.strftime(FORMAT_ISO),
            "limit": limit,
        },
    )
    reponse.raise_for_status()
    return _extraire_lectures(reponse.json())


def _fenetres(debut, fin, largeur):
    """Découpe [debut, fin[ en tranches successives de `largeur` (timedelta), la
    dernière plus courte. Les tranches sont jointives : la fin de l'une est le
    début de la suivante."""
    borne = debut
    while borne < fin:
        prochaine = min(borne + largeur, fin)
        yield borne, prochaine
        borne = prochaine


def recuperer_historique(site_id, debut, fin, limit, pas_minutes):
    """Récupère les lectures d'un site sur [debut, fin[, une requête par fenêtre.

    L'endpoint répartit `limit` points sur toute la fenêtre demandée : on borne
    donc chaque fenêtre à `limit * pas_minutes` minutes et on réclame un point par
    tranche de `pas_minutes`, pour obtenir un relevé toutes les `pas_minutes`.
    Les fenêtres étant jointives, l'instant de bordure est renvoyé deux fois : on
    dédoublonne sur l'horodatage."""
    largeur = timedelta(minutes=limit * pas_minutes)
    lectures = []
    horodatages_vus = set()

    for borne_debut, borne_fin in _fenetres(debut, fin, largeur):
        minutes = (borne_fin - borne_debut).total_seconds() / 60
        points = max(1, round(minutes / pas_minutes))
        page = _demander_page(site_id, borne_debut, borne_fin, min(limit, points))
        for lecture in page:
            cle = lecture.get("timestamp") if isinstance(lecture, dict) else None
            if cle is not None and cle in horodatages_vus:
                continue
            if cle is not None:
                horodatages_vus.add(cle)
            lectures.append(lecture)

    return lectures


def rejouer_site(site_id, debut, fin, limit, pas_minutes):
    """Récupère l'historique d'un site et dépose chaque lecture en bronze.
    Une lecture illisible ou une écriture MinIO en échec n'interrompt pas le
    reste du site. Renvoie (déposées, ignorées)."""
    lectures = recuperer_historique(site_id, debut, fin, limit, pas_minutes)
    deposees = 0
    ignorees = 0

    for lecture in lectures:
        if not isinstance(lecture, dict) or not lecture.get("timestamp"):
            ignorees += 1
            continue
        # L'API filtrée par site_id peut ne pas répéter le site_id dans chaque
        # ligne ; quality.py en a besoin comme champ obligatoire.
        lecture.setdefault("site_id", site_id)
        try:
            collect.envoyer_mesure(site_id, lecture)
            deposees += 1
        except (BotoCoreError, ClientError) as erreur:
            ignorees += 1
            print(f"{site_id} : lecture {lecture.get('timestamp')} non écrite, {erreur}")

    return deposees, ignorees


def build_parser():
    parser = argparse.ArgumentParser(
        description="Rejoue un historique de lectures depuis l'API vers le bucket bronze (à lancer à la demande).",
    )
    parser.add_argument(
        "--mois",
        type=int,
        default=int(os.environ.get("HISTORY_MOIS", "12")),
        help="Nombre de mois d'historique à remonter depuis --fin (défaut 12).",
    )
    parser.add_argument("--debut", default=None, help="Début explicite ISO 8601 (prioritaire sur --mois).")
    parser.add_argument("--fin", default=None, help="Fin de la période ISO 8601 (défaut : maintenant, UTC).")
    parser.add_argument(
        "--site",
        action="append",
        dest="sites",
        default=None,
        help="Site à rejouer (répétable). Défaut : tous les sites renvoyés par l'API.",
    )
    parser.add_argument(
        "--pas-minutes",
        type=int,
        default=int(os.environ.get("HISTORY_PAS_MINUTES", str(PAS_MINUTES_DEFAUT))),
        help=f"Intervalle entre deux relevés, en minutes (défaut {PAS_MINUTES_DEFAUT}).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=LIMIT_MAX,
        help=f"Taille de page demandée à l'API (bornée à 1..{LIMIT_MAX}).",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    limit = max(1, min(LIMIT_MAX, args.limit))
    pas_minutes = args.pas_minutes
    if pas_minutes < 1:
        print(f"--pas-minutes doit valoir au moins 1 : {args.pas_minutes}")
        return 1

    fin = _lire_horodatage(args.fin) if args.fin else datetime.now(UTC)
    if fin is None:
        print(f"--fin invalide : {args.fin}")
        return 1

    if args.debut:
        debut = _lire_horodatage(args.debut)
        if debut is None:
            print(f"--debut invalide : {args.debut}")
            return 1
    else:
        debut = reculer_de_mois(fin, max(1, args.mois))

    if debut >= fin:
        print(f"Période vide : debut={debut.isoformat()} >= fin={fin.isoformat()}")
        return 1

    try:
        sites = args.sites or collect.recuperer_liste_sites()
    except requests.RequestException as erreur:
        print(f"Impossible de récupérer la liste des sites : {erreur}")
        return 1

    if not sites:
        print("Aucun site à traiter.")
        return 0

    print(
        f"Backfill {debut:%Y-%m-%d %H:%M} -> {fin:%Y-%m-%d %H:%M} UTC | "
        f"{len(sites)} site(s) | pas={pas_minutes} min | page={limit}"
    )

    total_deposees = 0
    total_ignorees = 0
    for site_id in sites:
        try:
            deposees, ignorees = rejouer_site(site_id, debut, fin, limit, pas_minutes)
        except requests.RequestException as erreur:
            print(f"{site_id} : historique indisponible, {erreur}")
            continue
        total_deposees += deposees
        total_ignorees += ignorees
        print(f"{site_id} : {deposees} lecture(s) déposée(s), {ignorees} ignorée(s)")

    print(f"Backfill terminé | déposées={total_deposees} | ignorées={total_ignorees}")
    print("Lancez maintenant `python quality.py` pour propager bronze -> silver/gold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
