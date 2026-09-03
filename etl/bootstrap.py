"""
Bootstrap ETL : au premier démarrage sur un serveur, rejoue l'historique puis
draine la couche bronze, avant que etl-collect ne passe en collecte temps réel.

L'état vit dans la table Postgres `etl_status` (une seule ligne, id=1). Il rend
le script idempotent : si `phase == 'live'`, on ne fait rien et on sort en 0, donc
un redémarrage du conteneur ne relance pas le rattrapage. Pour reforcer un
rattrapage complet, remettre `phase = 'pending'` à la main.

Phases : pending -> history -> draining -> gold -> live   (ou -> error sur échec)
  - à la reprise après un plantage, `history` et `error` refont l'historique
    (history.py est idempotent : mêmes clés bronze réécrites) ; `draining`
    reprend directement le drainage là où l'état incrémental de quality.py
    s'était arrêté ; `gold` refait la consolidation (le drainage, déjà fini,
    ne coûte alors qu'un listing).
  - `gold` est une phase à part parce que le drainage n'agrège plus au fil de
    l'eau : quality.run() empile les partitions touchées, et elles sont toutes
    recalculées en une fois à la fin. Recalculer une partition à chaque tranche
    revenait à relire le même silver des dizaines de fois.

Ce n'est PAS un job planifié : conteneur one-shot (`restart: "no"`), dont
etl-collect dépend via `condition: service_completed_successfully`.

Installation :
    pip install -r requirements.txt

Usage :
    python bootstrap.py
"""

import os
import sys
import traceback

import history
import psycopg2
import quality

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.environ.get("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.environ.get("POSTGRES_DB", "ev_monitoring")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "ev_admin")
POSTGRES_PASSWORD = os.environ["POSTGRES_PASSWORD"]

# Rattrapage : repris tels quels par history.py (qui lit aussi ces variables).
HISTORY_MOIS = int(os.environ.get("HISTORY_MOIS", "13"))
HISTORY_PAS_MINUTES = int(os.environ.get("HISTORY_PAS_MINUTES", "60"))

# Drainage : une tranche de bronze par itération, pour borner la mémoire de quality.py.
DRAIN_CHUNK = int(os.environ.get("BOOTSTRAP_DRAIN_CHUNK", "20000"))
DRAIN_FETCH_WORKERS = int(os.environ.get("ETL_FETCH_WORKERS", "32"))

# Phases d'où il faut (re)faire l'historique. 'draining' en est absent : on reprend
# alors directement le drainage.
PHASES_AVEC_HISTORIQUE = (None, "pending", "history", "error")

CREATE_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS etl_status (
        id            SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
        phase         VARCHAR(20) NOT NULL DEFAULT 'pending',
        bronze_total  INTEGER NOT NULL DEFAULT 0,
        bronze_done   INTEGER NOT NULL DEFAULT 0,
        message       TEXT,
        started_at    TIMESTAMP,
        updated_at    TIMESTAMP DEFAULT now()
    )
"""


def se_connecter_postgres():
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )


def assurer_table(conn):
    """La table est créée par infra/postgres/init.sql sur une base neuve ; ce
    garde-fou couvre les bases déjà en place où init.sql n'est pas rejoué."""
    with conn.cursor() as cur:
        cur.execute(CREATE_TABLE_SQL)
        cur.execute("INSERT INTO etl_status (id, phase) VALUES (1, 'pending') ON CONFLICT (id) DO NOTHING")
    conn.commit()


def lire_phase(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT phase FROM etl_status WHERE id = 1")
        ligne = cur.fetchone()
    return ligne[0] if ligne else None


def maj_statut(conn, *, phase=None, bronze_total=None, bronze_done=None, message=None, demarrage=False):
    """Met à jour la ligne unique d'état. Seuls les champs fournis sont touchés.
    `phase`, quand il est passé, est toujours le premier paramètre SQL."""
    champs = ["updated_at = now()"]
    valeurs = []
    if phase is not None:
        champs.append("phase = %s")
        valeurs.append(phase)
    if bronze_total is not None:
        champs.append("bronze_total = %s")
        valeurs.append(bronze_total)
    if bronze_done is not None:
        champs.append("bronze_done = %s")
        valeurs.append(bronze_done)
    if message is not None:
        champs.append("message = %s")
        valeurs.append(message)
    if demarrage:
        champs.append("started_at = now()")
    with conn.cursor() as cur:
        cur.execute(f"UPDATE etl_status SET {', '.join(champs)} WHERE id = 1", valeurs)
    conn.commit()


def rejouer_historique():
    code = history.main(["--mois", str(HISTORY_MOIS), "--pas-minutes", str(HISTORY_PAS_MINUTES)])
    if code != 0:
        raise RuntimeError(f"history.py a échoué (code {code})")


def drainer_bronze(conn):
    """Boucle quality.run() par tranches jusqu'à ce qu'aucun nouvel objet bronze
    ne reste. Met à jour bronze_total / bronze_done à chaque passage.
    N'agrège pas : le gold est empilé puis traité par consolider_gold()."""
    while True:
        resultat = quality.run(
            ["--max-objects", str(DRAIN_CHUNK), "--fetch-workers", str(DRAIN_FETCH_WORKERS)]
        )
        if not resultat.ok:
            raise RuntimeError(resultat.message)

        maj_statut(conn, bronze_total=resultat.objets_bronze, bronze_done=resultat.processed_total)
        print(
            f"drain | bronze={resultat.objets_bronze} | traités={resultat.processed_total} | "
            f"nouveaux ce passage={resultat.nouveaux}"
        )
        if resultat.nouveaux == 0:
            return


def consolider_gold():
    """Recalcule en un seul passage le gold de toutes les partitions empilées pendant
    le drainage. Un échec partiel ne met pas le bootstrap en erreur : les partitions
    concernées restent en file et le recalcul horaire de collect.py les reprendra,
    alors qu'un phase=error empêcherait etl-collect de démarrer pour rien."""
    resultat = quality.run_gold([])
    if not resultat.ok:
        raise RuntimeError(resultat.message)
    print(
        f"gold | partitions={resultat.partitions} | "
        f"recalculées={resultat.recalculees} | échecs={resultat.echecs}"
    )
    return resultat


def main():
    conn = se_connecter_postgres()
    try:
        assurer_table(conn)

        phase = lire_phase(conn)
        if phase == "live":
            print("Bootstrap déjà terminé (phase=live), rien à faire.")
            return 0

        print(f"Bootstrap : démarrage (phase précédente = {phase})")

        if phase in PHASES_AVEC_HISTORIQUE:
            maj_statut(conn, phase="history", message=None, demarrage=True)
            print(f"Bootstrap : rejeu de l'historique ({HISTORY_MOIS} mois, pas {HISTORY_PAS_MINUTES} min)…")
            rejouer_historique()

        maj_statut(conn, phase="draining")
        print("Bootstrap : drainage bronze -> silver…")
        drainer_bronze(conn)

        maj_statut(conn, phase="gold")
        print("Bootstrap : consolidation des agrégats gold…")
        gold = consolider_gold()

        message = "Rattrapage terminé"
        if gold.echecs:
            message = f"Rattrapage terminé, {gold.echecs} partition(s) gold en attente de reprise"
        maj_statut(conn, phase="live", message=message)
        print("Bootstrap : terminé, phase=live.")
        return 0
    except Exception as erreur:  # phase=error -> etl-collect ne démarrera pas
        traceback.print_exc()
        try:
            maj_statut(conn, phase="error", message=str(erreur))
        except Exception:
            pass
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
