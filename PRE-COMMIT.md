# pre-commit

Un seul outil pour tout : ruff (`api/`, `etl/`) + ESLint (`front/`) + hygiène
générale (fins de ligne, gros fichiers, conflits de merge). Config dans
[`.pre-commit-config.yaml`](.pre-commit-config.yaml).

## Installation (une fois par clone)

```bash
python -m pip install pre-commit
python -m pre-commit install
```

Le hook ESLint réutilise `front/node_modules` — faire `npm install` dans
`front/` au moins une fois avant.

## Commandes

```bash
python -m pre-commit run --all-files   # lance tous les hooks sur tout le repo
python -m pre-commit run ruff          # un seul hook
git commit -m "..."                    # lance automatiquement les hooks concernés par les fichiers modifiés
```

## Tester que ça bloque, en vrai

```bash
echo "import os" >> etl/quality.py
git add etl/quality.py
git commit -m "test"
```

Le commit ne se crée pas :

```
ruff.....................................................................Failed
- hook id: ruff
- exit code: 1

etl/quality.py:1:8: F401 [*] `os` imported but unused
```

Vérifier qu'il n'y a bien aucun nouveau commit :

```bash
git log -1
```

Annuler le sabotage, recommiter normalement :

```bash
git checkout etl/quality.py
```

## Vérifier que le hook est installé

```bash
cat .git/hooks/pre-commit
```

Vide ou absent → `python -m pre-commit install` n'a pas été fait dans ce clone.
