# Tests E2E

Contrairement à [`api/tests/`](../api/tests/) (SQLite en mémoire, recréée à chaque
run, rien de mocké dans `api/tests/` ne touche au vrai stockage), ces tests
frappent la **vraie stack** : vrai serveur API, vraie PostgreSQL, vrai MinIO,
alimentés par les vrais services ETL. Ils ne mockent rien.

## Prérequis

La stack doit déjà tourner, **avec le profil `etl`** (sinon aucune alerte n'arrive
jamais et le dernier test tourne à vide) :

```bash
cd <racine du dépôt>
docker compose --profile etl up -d --build
```

## Lancer les tests

```bash
pip install -r e2e/requirements.txt
pytest e2e/ -v
```

Pour sauter le test le plus lent (poll jusqu'à 90s, voir plus bas) :

```bash
pytest e2e/ -v -k "not alerts_pipeline_reaches"
```

## Ce qui est couvert

| Test | Vérifie |
|---|---|
| `test_health` | Le serveur répond |
| `test_login_wrong_password_rejected` | Mauvais mot de passe → 401, pas de contournement |
| `test_admin_login_then_me` | Le token de `/auth/login` est accepté par `/auth/me` |
| `test_alerts_requires_auth` | `/alerts` sans token → 401 |
| `test_full_user_lifecycle` | Parcours complet : admin crée un viewer → le viewer se connecte → consulte les alertes → ne peut pas créer de compte (403) |
| `test_alerts_pipeline_reaches_the_api` | Parcours bout-en-bout : API Mock IoT → `etl-alerts` → PostgreSQL → notre API |

## Pourquoi pas de fixture qui "lance le pipeline" avant les tests

`collect.py` et `alerts.py` sont des `BlockingScheduler` sans mode "run once"
(aucun argparse, aucun flag) : les lancer en sous-processus depuis un fixture
bloquerait le test pour toujours. Ces services tournent déjà en continu dans
Docker (cycle toutes les 60s par défaut) ; le test qui en dépend
(`test_alerts_pipeline_reaches_the_api`) **poll** au lieu de déclencher un cycle
lui-même.

## Base partagée, pas remise à zéro

La base est persistante entre deux runs (contrairement à `api/tests/`). Les
tests qui créent des données suffixent leurs emails d'un uuid pour rester
rejouables sans collision sur l'unicité (`users.email`).

## Dépendance à la table `sites`

`alerts.py` ignore silencieusement toute alerte dont le `site_id` n'existe pas
dans la table `sites` (contrainte de clé étrangère). Cette table est peuplée par
[`infra/postgres/init/04_seed_sites.sql`](../infra/postgres/init/04_seed_sites.sql)
à la première initialisation du volume `pgdata`.

Un timeout sur `test_alerts_pipeline_reaches_the_api` signale donc l'une des deux
causes suivantes, jamais un défaut du test lui-même :

- `etl-alerts` n'est pas démarré (profil `etl` oublié) ;
- le volume PostgreSQL a été initialisé sans ce seed — vérifier avec
  `docker compose exec postgres psql -U ev_admin -d ev_monitoring -c "SELECT count(*) FROM sites;"`.
