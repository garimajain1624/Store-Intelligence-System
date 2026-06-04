from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import DBUnavailableError, get_db
from app.models import IngestedEvent
from app.schemas import HealthResponse, HealthStoreInfo

router = APIRouter(tags=["health"])


def _parse_iso(value: str) -> datetime:
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@router.get("/health", response_model=HealthResponse)
def health(request: Request, db: Session = Depends(get_db)) -> HealthResponse:
    try:
        now = datetime.now(timezone.utc)
        rows = db.execute(select(IngestedEvent.store_id, func.max(IngestedEvent.timestamp)).group_by(IngestedEvent.store_id)).all()

        stores: Dict[str, HealthStoreInfo] = {}
        stale_any = False
        for store_id, max_ts in rows:
            if not max_ts:
                continue
            last_dt = _parse_iso(max_ts)
            stale = (now - last_dt) > timedelta(minutes=10)
            stale_any = stale_any or stale
            stores[str(store_id)] = HealthStoreInfo(
                last_event_timestamp=max_ts,
                stale_feed=stale,
            )

        request.state.store_id = None
        request.state.event_count = len(stores)
        return HealthResponse(
            status="degraded" if stale_any else "ok",
            stores=stores,
            checked_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    except DBUnavailableError:
        raise

