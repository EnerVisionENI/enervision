---
name: api-new-endpoint
description: Use when adding a new FastAPI endpoint/router or otherwise modifying the api/ backend (e.g. "ajoute un endpoint", "nouvelle route API", "add an endpoint for X", "expose /api/v1/..."). Scaffolds the router following existing conventions (schema, auth dependency, SQLAlchemy model) and always adds pytest tests covering auth and the business rules — an endpoint is not done until it has one.
---

# Nouvel endpoint API (FastAPI + pytest)

Ce skill s'applique à toute nouvelle route ou ressource ajoutée dans `api/`. Le principe non négociable : **pas de nouvel endpoint sans tests pytest colocalisés qui passent**.

## 1. Modèle (si nouvelle table)

- SQLAlchemy 2.0 style dans [api/models.py](api/models.py) : `Mapped[...]` + `mapped_column(...)`, hérite de `Base` (`api/database.py`).
- La table Postgres elle-même est créée par `infra/postgres/init.sql`, pas par SQLAlchemy — un modèle en lecture seule (alimenté par un job `etl/`) ne redéclare pas les clés étrangères vers des tables gérées ailleurs (voir le commentaire sur `Alert.site_id` dans [models.py](api/models.py)).

## 2. Schéma Pydantic

- Dans [api/schemas.py](api/schemas.py) : `XOut` pour la sortie (`model_config = ConfigDict(from_attributes=True)` pour mapper directement un objet ORM), `XCreate`/`XUpdate` pour les entrées avec validation (`Field(min_length=...)`, etc.).

## 3. Router

- Un fichier par ressource dans `api/routers/`, `router = APIRouter(prefix="/x", tags=["x"])` (voir [alerts.py](api/routers/alerts.py) pour une lecture simple, [auth.py](api/routers/auth.py) pour un cas avec création + contrôle de rôle).
- Dépendances systématiques : `db: Session = Depends(get_db)` pour toute requête DB, et une dépendance d'auth — `_: User = Depends(get_current_user)` si la route demande juste d'être connecté, `_: User = Depends(require_role("admin"))` ou `require_min_role("operator")` (défini dans [api/auth.py](api/auth.py)) si un rôle minimum est requis.
- Toujours déclarer `response_model=...` et retourner l'objet ORM directement (pas de conversion manuelle en dict) — la conversion se fait via `from_attributes=True` sur le schéma.
- Erreurs métier via `HTTPException(status_code=..., detail="message en français")` (409 conflit, 404 introuvable, 403 rôle insuffisant — le 401 est déjà géré par la dépendance d'auth).

## 4. Enregistrement

- Importer et monter le router dans [api/main.py](api/main.py) : `app.include_router(x.router, prefix=API_V1_PREFIX)`.

## 5. Tests (obligatoire)

- Fichier `api/tests/test_x.py`, en réutilisant les fixtures de [api/tests/conftest.py](api/tests/conftest.py) : `client` (TestClient avec DB SQLite en mémoire, isolée par test), `db_session` (accès direct pour préparer des données), `make_user(db_session, email, password, role)`, `auth_headers(client, email, password)`.
- Ne jamais démarrer un vrai Postgres — la fixture `client` override déjà `get_db` vers la base SQLite en mémoire.
- Couvrir au minimum (voir [test_alerts.py](api/tests/test_alerts.py) et [test_auth.py](api/tests/test_auth.py) comme gabarits) :
  1. 401 si la route est protégée et appelée sans token.
  2. 200/201 cas nominal, en vérifiant la forme exacte de la réponse (`response.json()`).
  3. Les règles métier propres à l'endpoint (tri, `limit`/pagination, filtres).
  4. 403 si un rôle insuffisant appelle une route restreinte par `require_role`/`require_min_role`.
  5. 404/409 selon les erreurs métier gérées par le router.

## 6. Documentation à synchroniser

- Nouvelle variable d'environnement/secret lu par le router ou sa config → l'ajouter dans le [.env.example](.env.example) racine, dans la bonne section (`API : JWT / auth`, etc.) avec la même convention que l'existant (`change-me-in-prod` pour un secret).
- Si ce secret doit être présent en prod, l'ajouter aussi à la liste documentée dans [infra/DEPLOYMENT.md](infra/DEPLOYMENT.md) (section *Préparation du serveur* → fichier `api.env`).
- Mettre à jour la ligne **api** du tableau "Architecture" dans le [README.md](README.md) racine si le rôle du service change (ex. `"API REST (auth, alertes ; à venir : sites)"` → retirer la mention "à venir" une fois l'endpoint `sites` livré).
- Si l'endpoint ferme un point de la section "Pistes connues (non traitées)" du README (ex. "Endpoints `sites`"), mettre à jour ou retirer cette ligne.

## 7. Vérification finale

Avant de considérer l'endpoint terminé, lancer exactement la commande utilisée par la CI ([.github/workflows/deploy-dev.yml](.github/workflows/deploy-dev.yml)), depuis la racine du repo :

```bash
pytest api/tests -q
```

Tous les tests doivent passer avant de proposer le travail comme terminé.
