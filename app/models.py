from __future__ import annotations
from typing import Any, Optional

from sqlalchemy import Boolean, Float, Integer, JSON, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.database import get_engine


class Base(DeclarativeBase):
    pass


class IngestedEvent(Base):
    """
    Normalized internal representation of an incoming event.

    We store both:
    - normalized columns used for analytics queries
    - `raw` for debugging / schema evolution
    """

    __tablename__ = "ingested_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Idempotency key (must be globally unique across all stores)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    store_id: Mapped[str] = mapped_column(String(64), index=True)
    camera_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    visitor_id: Mapped[str] = mapped_column(String(64), index=True)

    # Normalized to the PDF-ish set: ENTRY, EXIT, ZONE_ENTER, ZONE_EXIT, BILLING_QUEUE_JOIN, BILLING_QUEUE_ABANDON
    event_type: Mapped[str] = mapped_column(String(64), index=True)

    # ISO-8601 UTC-ish string (we treat naive times as UTC)
    timestamp: Mapped[str] = mapped_column(String(64), index=True)

    zone_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    dwell_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    is_staff: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    event_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)

