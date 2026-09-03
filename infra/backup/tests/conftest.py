"""
Prépare la variable d'environnement obligatoire (sans défaut, car secret) avant que
backup.py soit importé. Sans ça, `import backup` plante avec un KeyError, puisque
POSTGRES_PASSWORD est lue avec os.environ[...].
"""

import os

os.environ.setdefault("POSTGRES_PASSWORD", "test-password")
