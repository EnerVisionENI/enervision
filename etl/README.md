# `etl/` — Collecte et qualité de données

Deux services indépendants, tous deux planifiés par APScheduler et démarrés par le profil
Compose `etl` :

| Service | Point d'entrée | Rythme | Rôle |
|---|---|---|---|
| `etl-collect` | [`collect.py`](collect.py) | 60 s + 2 crons | API Mock IoT → bronze → silver → gold |
| `etl-alerts` | [`alerts.py`](alerts.py) | 300 s | API Mock IoT → table `alerts` |

## Structure

```
etl/
├── collect.py         Planificateur : collecte bronze, appelle quality.py
├── quality.py         Le cœur : validation, silver, gold, quarantaine
├── alerts.py          Planificateur : collecte des alertes → PostgreSQL
├── storage.py         Client S3/MinIO partagé (construction paresseuse, mis en cache)
├── postgres_writer.py Réplication silver/gold/quarantine vers PostgreSQL
└── tests/             pytest — MinIO et PostgreSQL mockés
```

## Le modèle en couches

```
API Mock IoT
     │  GET /sites, puis GET /sites/{id}/current
     ▼
  bronze      JSON brut, un objet par mesure et par site       {site}/{date}/{HHMMSS}.json
     │        + copie du SHA-256 dans le bucket audit (WORM)
     ▼        quality.run()
  silver      Parquet validé, append-only                      record_date=…/site_id=…/
     │        les rejets partent en quarantine/
     ▼        quality.run_gold()
  gold        Agrégats daily + hourly                          daily|hourly/record_date=…/
```

**MinIO est la source de vérité.** Les mêmes lignes silver / gold / quarantine sont
répliquées dans PostgreSQL ([`postgres_writer.py`](postgres_writer.py)) pour être
interrogeables en SQL par l'API. Une panne PostgreSQL n'interrompt pas l'écriture MinIO.

### Pourquoi le gold est recalculé à part

Recalculer une partition gold relit **l'intégralité** de son silver. Fait à chaque cycle de
collecte (60 s), le cycle débordait de son intervalle et le silver n'était plus alimenté
qu'une minute sur deux. Chaque partition `(record_date, site_id)` touchée est donc empilée
dans `manifests/gold_pending.json`, et deux passages dédiés la reprennent :

| Planning | Défaut | Ce qu'il fait |
|---|---|---|
| Collecte | toutes les `INTERVALLE_SECONDES` (60 s) | bronze + promotion silver |
| `GOLD_CRON_HORAIRE` | `3 * * * *` | recalcule les partitions en attente |
| `GOLD_CRON_QUOTIDIEN` | `15 0 * * *` | recalcule **toute** la veille — filet de sécurité |

Les trois jobs tournent en `max_instances=1` + `coalesce=True` : si un passage déborde, les
occurrences manquées sont sautées plutôt qu'empilées. Une erreur dans `quality.py` est
interceptée et loguée sans jamais arrêter le planificateur.

> **Collecte 100 % temps réel** : aucun rejeu d'historique. La donnée commence au premier
> cycle après le démarrage.

## Trous de données : ce qui n'est jamais inventé

L'API Mock renvoie par intermittence des relevés « critical » dont les 7 métriques sont
nulles (panne capteur simulée, `null_reasons: ["network_loss"]`). Le pipeline ne les
confond jamais avec des mesures :

- ils **restent en silver** — un trou doit être visible *et daté* — mais avec
  `is_valid = false` et `usable_metrics_count = 0` ;
- le gold les compte à part (`critical_count`, `empty_count`), et les compteurs de qualité
  bouclent sans reste sur `records_count` ;
- les moyennes valent `NULL`, **jamais 0**, quand rien n'a été mesuré —
  `total_consumption_kwh` compris : un 0 factice s'apprendrait comme une consommation nulle
  réelle ;
- le grain horaire est complété à 24 lignes par jour et par site, une heure sans relevé
  ayant `records_count = 0`, pour qu'une série temporelle ne recolle pas deux heures non
  adjacentes ;
- le gold journalier porte `covered_hours` et `completeness_pct`.

Une métrique **présente mais physiquement impossible** (tension à 5000 V, facteur de
puissance à 3) envoie tout l'enregistrement en quarantaine (`error_type = out_of_range`) au
lieu du silver — voir `NUMERIC_BOUNDS` dans [`quality.py`](quality.py).

## Configuration

Depuis le `.env` de la racine. Valeurs par défaut dans `compose.yaml`.

| Variable | Défaut | Rôle |
|---|---|---|
| `API_BASE` | `http://10.105.200.45:8000` | API Mock IoT |
| `INTERVALLE_SECONDES` | `60` | Période de collecte |
| `ALERTS_INTERVALLE_SECONDES` | `300` | Période de collecte des alertes |
| `GOLD_CRON_HORAIRE` | `3 * * * *` | Recalcul des partitions en attente (UTC) |
| `GOLD_CRON_QUOTIDIEN` | `15 0 * * *` | Recalcul complet de la veille (UTC) |
| `ETL_FETCH_WORKERS` | `16` | Téléchargements bronze en parallèle (`1` = séquentiel) |
| `LANCER_QUALITY` / `LANCER_GOLD` | `1` | Coupe-circuits pour déboguer la collecte seule |
| `MINIO_*` | voir `.env.example` | Endpoint, identifiants, noms de buckets |
| `POSTGRES_*` | voir `.env.example` | Base de réplication |

## Lancer

```bash
# Les deux services, dans la stack
docker compose --profile etl up -d --build

# Suivre un cycle
docker compose logs -f etl-collect

# Un passage gold à la demande, sans attendre le cron
docker compose exec etl-collect python quality.py --gold-only
```

Hors Docker (MinIO et PostgreSQL doivent tourner) :

```bash
pip install -r etl/requirements.txt
cd etl && python collect.py
```

## Tests

```bash
pip install -r etl/requirements-dev.txt
cd etl && pytest -q
```

MinIO et PostgreSQL sont mockés : les tests ne touchent aucun stockage réel et ne demandent
aucun service démarré. Pour valider le pipeline contre la vraie stack, voir
[`e2e/`](../e2e/README.md).
