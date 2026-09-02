---
name: etl-new-job
description: Use when adding a new ETL job or scheduled data pipeline task in etl/ (e.g. "ajoute une collecte pour X", "nouveau job etl", "add a job that syncs Y to Postgres/MinIO", "planifie une tâche toutes les Xs"). Scaffolds the job following the existing scheduler/heartbeat pattern and always adds pytest unit tests that fake the external systems — a job is not done until it has one.
---

# Nouveau job ETL (APScheduler + pytest)

Ce skill s'applique à tout nouveau job de collecte/synchronisation ajouté dans `etl/`. Le principe non négociable : **pas de nouveau job sans tests pytest colocalisés qui passent, et sans vraie dépendance réseau/DB/S3 dans ces tests**.

## 1. Structure du module

- Un module par job à la racine de `etl/` (pas de sous-package), ex. [etl/alerts.py](etl/alerts.py), [etl/collect.py](etl/collect.py). Docstring de module en tête : ticket concerné, ce que fait le job, fréquence de planification, sections "Installation" / "Usage".
- Config via variables d'environnement : `os.environ.get("X", "défaut")` pour les valeurs non sensibles, `os.environ["X"]` **sans défaut** pour tout secret (ex. `POSTGRES_PASSWORD`) — une valeur par défaut sur un secret est un piège de sécurité, pas une commodité.
- Réutiliser les helpers déjà partagés plutôt que dupliquer une connexion : [etl/storage.py](etl/storage.py) (`storage.get_s3()`) pour MinIO/S3, le pattern `psycopg2.connect(host=..., port=..., dbname=..., user=..., password=...)` pour Postgres (voir `se_connecter_postgres` dans [alerts.py](etl/alerts.py)).

## 2. Forme attendue du job

Découper en fonctions séparées et testables indépendamment (voir [collect.py](etl/collect.py) et [alerts.py](etl/alerts.py)) :
- une fonction de récupération (`recuperer_x`) qui interroge l'API/la source,
- une fonction d'écriture (`enregistrer_x`/`envoyer_x`) qui upsert/dépose la donnée,
- `cycle_x()` : un cycle complet, appelé par le planificateur — **une erreur sur un item individuel ne doit jamais interrompre le reste du lot** (try/except autour de chaque itération, cf. `cycle_collecte`),
- `marquer_vivant()` : écrit `HEARTBEAT_PATH` pour le HEALTHCHECK Docker,
- `main()` : construit un `BlockingScheduler`, `add_job(cycle_x, "interval", seconds=INTERVALLE_SECONDES, next_run_time=datetime.now(timezone.utc))`, gère `KeyboardInterrupt`/`SystemExit`.

Noms de fonctions/variables en français. Les docstrings expliquent le **pourquoi** d'une règle non évidente (ex. pourquoi une alerte sans site connu est ignorée plutôt que de faire échouer le lot), jamais le quoi. Logs via `print()` — pas de logger dédié dans ce codebase.

## 3. Tests (obligatoire)

- Fichier `etl/tests/test_x.py`. [etl/tests/conftest.py](etl/tests/conftest.py) pré-remplit déjà les variables d'environnement obligatoires (`MINIO_*`, `POSTGRES_PASSWORD`) avant l'import, donc `import x` ne plante pas en test — pas besoin d'y retoucher sauf nouvelle variable obligatoire.
- **Aucun test ne doit toucher un vrai Postgres, MinIO ou réseau.** Remplacer les dépendances externes par un faux objet et vérifier les appels reçus :
  - Postgres/psycopg2 : un faux `curseur`/`connexion` qui enregistre `execute`/`commit`/`close` (voir `FauxCurseur`/`FausseConnexion` dans [test_alerts.py](etl/tests/test_alerts.py)).
  - MinIO/boto3 : un faux client qui enregistre chaque `put_object(**kwargs)` (voir `FauxClientS3` dans [test_collect.py](etl/tests/test_collect.py)), injecté via une fixture `monkeypatch.setattr(module.storage, "get_s3", lambda: faux)`.
  - Utiliser `monkeypatch.setattr(x, "nom_fonction", ...)` pour isoler une fonction du reste du module plutôt que de mocker une librairie entière.
- Couvrir au minimum :
  1. Le cas nominal (écriture correcte, forme exacte des données écrites/upsertées).
  2. Les règles de filtrage/exclusion métier (ex. item ignoré si une contrainte n'est pas satisfaite).
  3. La continuité du cycle après une erreur partielle sur un item (`cycle_x` doit continuer sur les items suivants).
  4. La fermeture propre des ressources (connexion fermée dans le `finally`, cf. `test_cycle_alertes_ferme_la_connexion`).
  5. Le heartbeat (`marquer_vivant` écrit bien un fichier avec un horodatage, via `tmp_path`).

## 4. Documentation à synchroniser

- Nouvelle variable d'environnement lue par le job (surcharge non sensible ou secret) → l'ajouter dans le [.env.example](.env.example) racine, section `ETL (profile "etl")`.
- Si le job a besoin d'un secret propre en prod, l'ajouter à la liste documentée dans [infra/DEPLOYMENT.md](infra/DEPLOYMENT.md) (section *Préparation du serveur* → fichier `etl.env`).
- Nouveau service compose (ex. `etl-x` à côté de `etl-collect`/`etl-alerts`/`etl-sites`) → l'ajouter à la ligne du profil `etl` dans le tableau des profils Compose de [infra/DEPLOYMENT.md](infra/DEPLOYMENT.md).
- Si le job remplace un stub existant (ex. `etl/sites.py`), mettre à jour ou retirer la ligne correspondante dans la section "Pistes connues (non traitées)" du [README.md](README.md) racine, et la ligne du service **api**/**etl** du tableau "Architecture" si sa description mentionnait ce manque.

## 5. Vérification finale

Avant de considérer le job terminé, lancer exactement la commande utilisée par la CI ([.github/workflows/etl-ci.yml](.github/workflows/etl-ci.yml)), depuis le dossier `etl/` :

```bash
cd etl && pytest tests/ -v
```

Tous les tests doivent passer avant de proposer le travail comme terminé.
