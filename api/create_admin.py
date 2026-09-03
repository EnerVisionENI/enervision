"""Bootstrap : crée le premier compte admin (aucune route publique de création de
compte n'existe, POST /api/v1/users exige déjà un admin). Usage :
    python -m api.create_admin --email admin@enervision.fr
Le mot de passe est demandé de façon interactive (jamais passé en argument, pour
ne pas le laisser dans l'historique shell ni dans `ps`).
"""

import argparse
import getpass

from api.auth import hash_password
from api.database import SessionLocal
from api.models import User


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--password",
        help="À éviter : préférez la saisie interactive. Utile seulement pour un script non interactif.",
    )
    args = parser.parse_args()

    password = args.password or getpass.getpass("Mot de passe du compte admin : ")
    if len(password) < 8:
        parser.error("le mot de passe doit faire au moins 8 caractères")

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == args.email).first() is not None:
            print(f"Un utilisateur existe déjà pour {args.email}")
            return
        user = User(email=args.email, password_hash=hash_password(password), role="admin")
        db.add(user)
        db.commit()
        print(f"Admin créé : {args.email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
