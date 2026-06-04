from __future__ import annotations
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, ConfigDict


class IngestError(BaseModel):
    index: int
    error: str


class IngestResult(BaseModel):
    ingested: int
    skipped: int
    errors: List[IngestError] = Field(default_factory=list)


class MetricResponse(BaseModel):
    store_id: str
    unique_visitors: int
    active_visitors: int = 0
    staff_excluded: int = 0
    customers: int = 0
    staff_count: int = 0
    current_queue: int = 0
    conversion_rate: float
    avg_dwell_per_zone_ms: Dict[str, float]
    queue_depth: int
    abandonment_rate: float

    window_start: str
    window_end: str


class FunnelStage(str):
    pass


class FunnelResponse(BaseModel):
    store_id: str
    stages: Dict[str, int]
    drop_off_percent: Dict[str, float]

    window_start: str
    window_end: str


class HeatmapZone(BaseModel):
    zone_id: str
    visits: int
    avg_dwell_ms: float
    score_0_100: float


class HeatmapResponse(BaseModel):
    store_id: str
    zones: List[HeatmapZone]
    data_confidence: Literal["low", "high"]

    window_start: str
    window_end: str


class AnomalySeverity(str):
    pass


class AnomalyItem(BaseModel):
    anomaly_type: str
    severity: Literal["INFO", "WARN", "CRITICAL"]
    suggested_action: str
    details: Dict[str, Any] = Field(default_factory=dict)


class AnomaliesResponse(BaseModel):
    store_id: str
    anomalies: List[AnomalyItem]

    window_start: str
    window_end: str


class CameraStatus(BaseModel):
    camera_id: str
    role: str  # ENTRY | ZONE | BILLING | UNKNOWN
    active: bool
    last_event_ts: Optional[str] = None


class HealthStoreInfo(BaseModel):
    last_event_timestamp: Optional[str] = None
    stale_feed: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    stores: Dict[str, HealthStoreInfo]
    checked_at: str


def to_uuid_str(value: str) -> str:
    """
    Best-effort uuid string validation.

    If a UUID parse fails, we still return the original input so callers can decide.
    """

    try:
        return str(uuid.UUID(value))
    except Exception:
        return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

