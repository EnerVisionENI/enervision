# Contribuer à EnerVision

Tout ce qu'il faut savoir avant le premier commit : mise en place de l'environnement,
outillage qualité, tests, conventions.

## 1. Mise en place (une fois par clone)

```bash
git clone https://github.com/EnerVisionENI/enervision.git
cd enervision
cp .env.example .env          # valeurs de dev utilisables telles quelles

# Dépendances front (nécessaires aussi au hook ESLint, voir §2)
cd front && npm install && cd ..

# Hooks de qualité
pip install pre-commit
pre-commit install
```

> **Vérifiez qu'aucun `core.hooksPath` ne détourne les hooks :**
>
> ```bash
> git config core.hooksPath      # doit ne rien afficher
> ```
>
> S'il affiche quelque chose (par ex. `.husky/_`), Git ignore `.git/hooks/pre-commit` et
> **aucun hook ne tourne**, silencieusement. Corriger avec :
>
> ```bash
> git config --unset core.hooksPath
> pre-commit install
> ```

## 2. Qualité de code — `pre-commit`

Un seul outil pour tout, configuré dans [`.pre-commit-config.yaml`](.pre-commit-config.yaml) :

| Hook | Portée | Rôle |
|---|---|---|
| `ruff` | tout le Python : `api/`, `etl/`, `ml/`, `e2e/`, `infra/backup/` | lint (+ `--fix`) |
| `ruff-format` | idem | formatage |
| `eslint (front)` | `front/**/*.{js,vue}` | lint du front |
| `trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending` | tout | hygiène |
| `check-yaml`, `check-added-large-files`, `check-merge-conflict` | tout | garde-fous |

Les règles ruff vivent dans [`pyproject.toml`](pyproject.toml) à la racine, **pas** dans la
config pre-commit : `ruff check .` en local donne donc exactement le même verdict que le
hook et que la CI.

> `ruff format` reformate aussi les **blocs de code Python contenus dans les fichiers
> Markdown**. Un exemple de code dans un README est donc soumis aux mêmes règles que le
> reste — c'est voulu, mais ça surprend la première fois.

```bash
pre-commit run --all-files    # tout le dépôt
pre-commit run ruff           # un seul hook
git commit -m "..."           # automatique, sur les fichiers modifiés
```

Le hook ESLint réutilise `front/node_modules` plutôt que de faire gérer un environnement
Node séparé par pre-commit — d'où le `npm install` du §1.

### Vérifier que le blocage fonctionne vraiment

```bash
echo "import os" >> etl/quality.py
git add etl/quality.py
git commit -m "test"
```

Le commit ne doit pas se créer :

```
ruff.....................................................................Failed
- hook id: ruff
- exit code: 1
etl/quality.py:1:8: F401 [*] `os` imported but unused
```

Confirmez avec `git log -1`, puis annulez : `git checkout etl/quality.py`.

### Le cas de `ml/` et des imports en milieu de fichier

Les points d'entrée de `ml/` (`train.py`, `predict.py`, `scripts/*.py`) amorcent leur
`sys.path` et chargent le `.env` de la racine **avant** d'importer `core/` et `models/`,
qui lisent des variables d'environnement au moment de l'import. Leurs imports ne peuvent
donc pas remonter en tête de fichier : `E402` y est neutralisé via
`[tool.ruff.lint.per-file-ignores]` dans `pyproject.toml`, une fois pour toutes, plutôt
qu'avec des `# noqa` dispersés.

## 3. Tests

Chaque service a ses dépendances de test dans un `requirements-dev.txt` distinct du
runtime : les images Docker n'embarquent que le runtime.

```bash
# API — SQLite en mémoire, recréée à chaque run, aucune dépendance externe
pip install -r api/requirements-dev.txt
pytest api/tests -q

# ETL — MinIO et PostgreSQL mockés
pip install -r etl/requirements-dev.txt
cd etl && pytest -q

# Script de sauvegarde — pg_dump et rclone mockés
pip install -r infra/backup/requirements-dev.txt
cd infra/backup && pytest -q

# Front — Vitest + jsdom
cd front && npm test
```

Les tests **e2e** frappent la vraie stack et demandent qu'elle tourne :
voir [`e2e/README.md`](e2e/README.md).

## 4. Intégration continue

[`.github/workflows/ci-cd.yml`](.github/workflows/ci-cd.yml), sur chaque push et PR vers
`dev` / `main` :

```
lint-python ┐
test-api    │
test-etl    ├─→ build (build → Trivy → push GHCR) ─→ test-e2e ─→ deploy
test-backup │
build-front ┘
```

`lint-python` fait tourner `ruff check .` **et** `ruff format --check .` sur tout le dépôt :
un `pre-commit run --all-files` vert garantit ce job. La version de ruff est épinglée des
deux côtés (CI et `rev` de `.pre-commit-config.yaml`) et doit rester alignée — une nouvelle
release peut activer des règles et casser la CI sans qu'aucun code n'ait bougé.

Les images ne sont construites **qu'une fois**, taguées par SHA de commit : l'artefact
scanné par Trivy, celui testé en e2e et celui déployé sont le même digest. Détails dans
[`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md).

## 5. Conventions

- **Langue** : code, commentaires et documentation en français. Les identifiants
  techniques qui reflètent un schéma externe (colonnes SQL, champs de l'API Mock IoT,
  noms de buckets) restent en anglais.
- **Commentaires** : expliquer *pourquoi*, pas *quoi*. Les commentaires les plus utiles du
  dépôt documentent une décision et son contexte (par ex. pourquoi le gold est recalculé
  hors du cycle de collecte, ou pourquoi le healthcheck nginx force `127.0.0.1`).
- **Branches** : partir de `dev`, PR vers `dev`. `main` reçoit les livraisons validées.
- **Schéma de base** : il fait foi dans [`infra/postgres/init/`](infra/postgres/init/), pas
  dans les modèles SQLAlchemy. Ces scripts n'étant rejoués que sur un volume vide, tout
  changement sur une base existante se fait à la main — voir *Changements de schéma sur une
  base existante* dans [`infra/DEPLOYMENT.md`](infra/DEPLOYMENT.md).
- **Secrets** : jamais dans le dépôt. Tout passe par `.env` (ignoré par Git) ; seul
  `.env.example` est versionné, avec des valeurs de dev.
