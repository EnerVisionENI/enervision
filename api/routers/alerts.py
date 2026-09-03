from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc
from sqlalchemy.orm import Session

from api.auth import get_active_user
from api.database import get_db
from api.models import Alert, User
from api.schemas import AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(get_active_user),
) -> list[Alert]:
    return db.query(Alert).order_by(desc(Alert.timestamp)).limit(limit).all()
