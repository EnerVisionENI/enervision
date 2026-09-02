"""
Prépare les variables d'environnement requises par collect.py avant qu'il soit importé.
Sans ça, `import collect` plante immédiatement avec un KeyError, puisque MINIO_ENDPOINT,
MINIO_ACCESS_KEY et MINIO_SECRET_KEY sont lues avec os.environ[...] sans valeur par défaut.
"""

import os

os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test-access-key")
os.environ.setdefault("MINIO_SECRET_KEY", "test-secret-key")
os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
