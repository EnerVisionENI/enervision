from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from api.auth import create_access_token, get_current_user, hash_password, require_role, verify_password
from api.database import get_db
from api.models import User
from api.schemas import Token, UserCreate, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# Nommé au niveau module plutôt qu'appelé inline dans la signature de la route :
# évite le B008 de ruff (require_role n'est pas dans extend-immutable-calls), et
# réutilisable tel quel si une autre route admin-only s'ajoute.
require_admin = require_role("admin")


@router.post("/login", response_model=Token)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> Token:
    user = db.query(User).filter(User.email == form_data.username).first()
    if user is None or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return Token(access_token=create_access_token(user))


@router.get("/me", response_model=UserOut)
def read_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> User:
    if db.query(User).filter(User.email == payload.email).first() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cet email est déjà utilisé")

    user = User(email=payload.email, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
