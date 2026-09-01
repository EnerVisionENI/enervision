"""Bootstrap : crée le premier compte admin (aucune route publique de création de
compte n'existe, POST /auth/users exige déjà un admin). Usage :
    python -m api.create_admin --email admin@enervision.fr --password ********
"""

import argparse

from api.auth import hash_password
from api.database import SessionLocal
from api.models import User


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == args.email).first() is not None:
            print(f"Un utilisateur existe déjà pour {args.email}")
            return
        user = User(email=args.email, password_hash=hash_password(args.password), role="admin")
        db.add(user)
        db.commit()
        print(f"Admin créé : {args.email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
