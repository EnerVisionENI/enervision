"""Gestion des comptes, réservée aux admins.

Un compte créé ici part toujours avec un mot de passe temporaire (`must_change_password`),
que son titulaire doit remplacer via POST /auth/password avant d'accéder au reste
de l'application.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from api.auth import hash_password, require_role
from api.database import get_db
from api.models import User
from api.schemas import PasswordReset, UserCreate, UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])

# Nommé au niveau module plutôt qu'appelé dans la signature de chaque route :
# convention introduite sur dev pour éviter le B008 de ruff.
require_admin = require_role("admin")


def get_user_or_404(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable")
    return user


@router.get("", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[User]:
    return db.query(User).order_by(User.created_at, User.email).all()


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    if db.query(User).filter(User.email == payload.email).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cet email est déjà utilisé")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        must_change_password=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: uuid.UUID,
    payload: UserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> User:
    user = get_user_or_404(db, user_id)
    # Garde-fou : un admin qui se rétrograde perdrait l'accès à cette page, et
    # avec un seul admin plus personne ne pourrait le rétablir.
    if user.user_id == current_user.user_id and payload.role != current_user.role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas modifier votre propre rôle",
        )

    user.role = payload.role
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/password", response_model=UserOut)
def reset_user_password(
    user_id: uuid.UUID,
    payload: PasswordReset,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> User:
    user = get_user_or_404(db, user_id)
    # Comme la suppression : la page d'administration agit sur les autres comptes,
    # pas sur celui de l'admin connecté.
    if user.user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas réinitialiser votre propre mot de passe",
        )

    user.password_hash = hash_password(payload.password)
    user.must_change_password = True
    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> Response:
    user = get_user_or_404(db, user_id)
    if user.user_id == current_user.user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Vous ne pouvez pas supprimer votre propre compte",
        )

    db.delete(user)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
