from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


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
    avg_dwell_ms: float = 0.0            # overall avg dwell across all zones
    most_visited_zone: Optional[str] = None
    repeat_visitors: int = 0             # visitors seen in ≥ 2 cameras
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
    role: str                            # ENTRY | ZONE | BILLING | UNKNOWN
    active: bool
    last_event_ts: Optional[str] = None
    seconds_since_last_event: Optional[int] = None


class HourlyBucket(BaseModel):
    hour: int           # 0–23
    label: str          # "12 PM"
    visitor_count: int


class PeakHourResponse(BaseModel):
    store_id: str
    peak_hour: int
    peak_hour_label: str
    peak_count: int
    hourly_buckets: List[HourlyBucket]
    window_start: str
    window_end: str


class HealthStoreInfo(BaseModel):
    last_event_timestamp: Optional[str] = None
    stale_feed: bool = False


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    stores: Dict[str, HealthStoreInfo]
    checked_at: str


def to_uuid_str(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except Exception:
        return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
