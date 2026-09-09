# `infra/` — Infrastructure et exploitation

Tout ce qui n'est pas code applicatif : schéma de base, reverse proxy, observabilité,
sauvegardes, réplication hors site. Les services eux-mêmes sont déclarés dans le
[`compose.yaml`](../compose.yaml) de la racine — ce dossier ne contient que leur
configuration et leurs scripts.

## Où trouver quoi

| Dossier | Contenu | Service Compose |
|---|---|---|
| [`postgres/`](postgres/README.md) | Schéma SQL, rejoué à la création du volume | `postgres` |
| [`minio/`](minio/) | Script de création des buckets au démarrage | `minio-init` |
| [`nginx.conf`](nginx.conf) | Config Nginx du front (sert le bundle, proxifie `/api`) | `front` |
| [`prometheus/`](prometheus/) | Cibles de scraping | `prometheus` (profil `observability`) |
| [`grafana/`](grafana/) | Datasource et dashboard provisionnés | `grafana` (profil `observability`) |
| [`audit-sync/`](audit-sync/) | Réplication chiffrée MinIO → Azure Blob (rclone) | `audit-sync` (profil `audit`) |
| [`backup/`](backup/) | Sauvegarde chiffrée `users` / `sites` vers Azure | **aucun** — cron système |
| [`mlflow/`](mlflow/DEPLOYMENT.md) | Déploiement du serveur MLflow | `mlflow` (profil `mlflow`) |
| [`outline/`](outline/DEPLOYMENT.md) | Wiki interne auto-hébergé + Dex (stack **séparée**) | — |
| [`env/production.env`](env/production.env) | Gabarit du `.env` serveur | — |

## Documents de référence

| Document | Sujet |
|---|---|
| [`DEPLOYMENT.md`](DEPLOYMENT.md) | **Le document principal** : CI/CD, GHCR, déploiement serveur, secrets, TLS, sauvegardes, changements de schéma |
| [`mlflow/DEPLOYMENT.md`](mlflow/DEPLOYMENT.md) | Serveur de tracking et Model Registry |
| [`outline/DEPLOYMENT.md`](outline/DEPLOYMENT.md) | Wiki Outline + fournisseur d'identité Dex |

## Deux exceptions à connaître

- **`backup/` n'est pas un service Compose.** C'est un script lancé par une crontab système
  sur le serveur (sauvegarde quotidienne à 3 h). Il couvre `users` et `sites` — les tables
  qui ne peuvent pas être régénérées depuis MinIO. Détails dans
  [`DEPLOYMENT.md`](DEPLOYMENT.md).
- **`outline/` a son propre `compose.yaml`.** La stack wiki est volontairement isolée de la
  stack applicative : elle a son cycle de vie, ses volumes et son fournisseur d'identité, et
  une panne de l'une ne doit pas emporter l'autre.

## Secrets

Aucun secret n'est versionné. Deux fichiers sont explicitement ignorés par Git parce qu'ils
en contiennent une fois remplis :

- `infra/outline/dex-config.yaml` — hachages de mots de passe (le `.example` est versionné).

Le reste passe par le `.env` de la racine, dont
[`env/production.env`](env/production.env) est le gabarit serveur.
