from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from statistics import mean
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import Depends

from app.database import get_db
from app.models import IngestedEvent
from app.schemas import (
    AnomalyItem,
    AnomaliesResponse,
    CameraStatus,
    FunnelResponse,
    HeatmapResponse,
    HourlyBucket,
    MetricResponse,
    HeatmapZone,
    PeakHourResponse,
)

router = APIRouter(prefix="/stores", tags=["analytics"])


def _parse_iso(value: str) -> datetime:
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _window_for_store(latest_ts_iso: Optional[str]) -> Tuple[str, str]:
    """
    Define "today" deterministically from the latest ingested event timestamp,
    not from system clock (tests use fixed timestamps).
    """

    now = _parse_iso(latest_ts_iso) if latest_ts_iso else datetime.now(timezone.utc)
    day_start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    return day_start.strftime("%Y-%m-%dT%H:%M:%SZ"), day_end.strftime("%Y-%m-%dT%H:%M:%SZ")


@lru_cache(maxsize=4)
def _load_pos_transactions(pos_csv_path: str) -> Dict[str, List[datetime]]:
    """Return POS order times grouped by store_id."""
    if not pos_csv_path or not os.path.exists(pos_csv_path):
        return {}

    out: Dict[str, List[datetime]] = {}
    with open(pos_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            store_id = row.get("store_id")
            if not store_id:
                continue

            dt = None
            if "timestamp" in row:
                ts = row["timestamp"].strip()
                if ts.endswith("Z"):
                    ts = ts[:-1] + "+00:00"
                try:
                    dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
                except Exception:
                    pass

            if not dt:
                order_date = row.get("order_date")
                order_time = row.get("order_time")
                if order_date and order_time:
                    try:
                        dt = datetime.strptime(f"{order_date} {order_time}", "%d-%m-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                    except Exception:
                        try:
                            dt = datetime.strptime(f"{order_date} {order_time}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                        except Exception:
                            pass

            if dt:
                out.setdefault(store_id, []).append(dt)

    for store_id, times in out.items():
        times.sort()
    return out


def _get_pos_for_store(store_id: str) -> List[datetime]:
    pos_csv_path = os.getenv("POS_CSV_PATH", "")
    transactions = _load_pos_transactions(pos_csv_path)
    return transactions.get(store_id, [])


def _conversion_visitors(store_id: str, events: List[IngestedEvent]) -> set:
    """Backward-compat stub: returns set of visitor IDs with POS-correlated purchases."""
    join_events = [e for e in events if e.event_type == "BILLING_QUEUE_JOIN" and not e.is_staff]
    join_ts_by_visitor: Dict[str, List[datetime]] = {}
    for e in join_events:
        join_ts_by_visitor.setdefault(e.visitor_id, []).append(_parse_iso(e.timestamp))
    for v in join_ts_by_visitor:
        join_ts_by_visitor[v].sort()
    txns = _get_pos_for_store(store_id)
    if not txns:
        return set()
    converted: set = set()
    for txn_ts in txns:
        window_start = txn_ts - timedelta(minutes=5)
        for visitor_id, join_times in join_ts_by_visitor.items():
            for jt in join_times:
                if jt < window_start:
                    continue
                if jt <= txn_ts:
                    converted.add(visitor_id)
                break
    return converted


def _get_store_events(db: Session, store_id: str) -> List[IngestedEvent]:
    rows = db.execute(select(IngestedEvent).where(IngestedEvent.store_id == store_id)).scalars().all()
    return list(rows)


def _get_camera_status(events: List[IngestedEvent], latest_ts_iso: Optional[str]) -> List[CameraStatus]:
    """Build per-camera activity status. Active = seen within 30min of latest event."""
    cam_last: Dict[str, str] = {}
    for e in events:
        if e.camera_id:
            prev = cam_last.get(e.camera_id)
            if prev is None or e.timestamp > prev:
                cam_last[e.camera_id] = e.timestamp

    if not cam_last:
        return []

    latest_dt = _parse_iso(latest_ts_iso) if latest_ts_iso else datetime.now(timezone.utc)
    threshold = timedelta(minutes=30)

    def _role(cam_id: str) -> str:
        c = cam_id.lower()
        if "entry" in c:
            return "ENTRY"
        elif "bill" in c:          # matches CAM_BILLING_01, CAM_BILL_01, etc.
            return "BILLING"
        elif "zone" in c:
            n = ""
            for part in c.split("_"):
                if part.isdigit():
                    n = part
                    break
            return f"ZONE-{n}" if n else "ZONE"
        return "UNKNOWN"

    result: List[CameraStatus] = []
    for cam_id, last_ts in sorted(cam_last.items()):
        last_dt = _parse_iso(last_ts)
        active = (latest_dt - last_dt) <= threshold
        secs = int((latest_dt - last_dt).total_seconds())
        result.append(CameraStatus(
            camera_id=cam_id,
            role=_role(cam_id),
            active=active,
            last_event_ts=last_ts,
            seconds_since_last_event=secs,
        ))
    return result


def _latest_timestamp_iso(events: List[IngestedEvent]) -> Optional[str]:
    if not events:
        return None
    return max((e.timestamp for e in events if e.timestamp), default=None)


def _normalize_zone(zone_id: Optional[str]) -> Optional[str]:
    if not zone_id:
        return None
    z = zone_id.upper().strip()
    # Normalize legacy aliases
    if z in ("ZONE_A", "ZONE_1"):
        return "SKINCARE"
    if z in ("ZONE_B", "ZONE_2"):
        return "MAKEUP"
    if z in ("ZONE_C", "ZONE_3"):
        return "HAIRCARE"
    if z in ("ZONE_D", "ZONE_4"):
        return "FRAGRANCE"
    if z in ("BILLING", "BILLING_COUNTER"):
        return "CHECKOUT"
    if z == "COSMETICS":
        return "MAKEUP"
    return z


def _build_dwell_by_zone(events: List[IngestedEvent]) -> Dict[str, List[int]]:
    """Pair ZONE_ENTER and ZONE_EXIT timestamps per (visitor_id, zone_id)."""
    enters: Dict[Tuple[str, str], datetime] = {}
    durations: Dict[str, List[int]] = {}

    for e in sorted(events, key=lambda x: x.timestamp):
        if not e.zone_id:
            continue
        if e.is_staff:
            continue
        zone_id = _normalize_zone(e.zone_id)
        if e.event_type == "ZONE_ENTER":
            enters[(e.visitor_id, zone_id)] = _parse_iso(e.timestamp)
        elif e.event_type == "ZONE_EXIT":
            key = (e.visitor_id, zone_id)
            if key in enters:
                start = enters.pop(key)
                end = _parse_iso(e.timestamp)
                ms = max(0, int((end - start).total_seconds() * 1000))
                durations.setdefault(zone_id, []).append(ms)
        elif e.event_type in ("BILLING_QUEUE_JOIN", "PURCHASE", "BILLING_QUEUE_ABANDON") and zone_id:
            # Also track dwell at checkout zone from billing events
            dwell = e.dwell_ms or 0
            if dwell > 0:
                durations.setdefault(zone_id, []).append(dwell)

    return durations


def _distinct_visitors(events: List[IngestedEvent]) -> set[str]:
    return {e.visitor_id for e in events if not e.is_staff}


def _calculate_max_queue_depth(store_id: str, events: List[IngestedEvent], txns: List[datetime]) -> int:
    fallback_depths: List[int] = []
    for e in events:
        if e.event_type in {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"} and not e.is_staff:
            meta = e.event_metadata or {}
            q = meta.get("queue_depth")
            if q is None:
                q = meta.get("queue_position_at_join")
            if q is not None:
                try:
                    fallback_depths.append(int(q))
                except Exception:
                    pass

    visitor_events: Dict[str, List[IngestedEvent]] = {}
    for e in events:
        if not e.is_staff:
            visitor_events.setdefault(e.visitor_id, []).append(e)

    queue_sessions = []
    for vid, evts in visitor_events.items():
        evts.sort(key=lambda x: x.timestamp)
        for idx, e in enumerate(evts):
            if e.event_type == "BILLING_QUEUE_JOIN":
                t_join = _parse_iso(e.timestamp)
                t_leave = None

                for next_e in evts[idx + 1:]:
                    if next_e.event_type in {"BILLING_QUEUE_ABANDON", "EXIT", "PURCHASE"}:
                        t_leave = _parse_iso(next_e.timestamp)
                        break

                if not t_leave:
                    for txn_ts in txns:
                        if t_join <= txn_ts <= t_join + timedelta(minutes=5):
                            t_leave = txn_ts
                            break

                if not t_leave:
                    t_leave = _parse_iso(evts[-1].timestamp)
                    if t_leave <= t_join:
                        t_leave = t_join + timedelta(minutes=5)

                queue_sessions.append((t_join, t_leave))

    if not queue_sessions:
        return max(fallback_depths) if fallback_depths else 0

    timeline = []
    for t_join, t_leave in queue_sessions:
        timeline.append((t_join, 1))
        timeline.append((t_leave, -1))
    timeline.sort(key=lambda x: (x[0], x[1]))

    curr = 0
    mx = 0
    for t, delta in timeline:
        curr += delta
        if curr > mx:
            mx = curr

    return max(mx, max(fallback_depths) if fallback_depths else 0)


# ── Purchase dwell threshold (must match emit.py) ─────────────────────────────
PURCHASE_DWELL_THRESHOLD_MS = 30_000


def _sessionize_store_events(store_id: str, events: List[IngestedEvent]) -> List[Dict[str, Any]]:
    visitor_events: Dict[str, List[IngestedEvent]] = {}
    for e in events:
        if not e.is_staff:
            visitor_events.setdefault(e.visitor_id, []).append(e)

    txns = _get_pos_for_store(store_id)
    sessions: List[Dict[str, Any]] = []

    for vid, evts in visitor_events.items():
        evts.sort(key=lambda x: _parse_iso(x.timestamp))

        normalized_evts = []
        for e in evts:
            e_norm = IngestedEvent(
                event_id=e.event_id,
                store_id=e.store_id,
                camera_id=e.camera_id,
                visitor_id=e.visitor_id,
                event_type=e.event_type.upper().strip(),
                timestamp=e.timestamp,
                zone_id=_normalize_zone(e.zone_id),
                dwell_ms=e.dwell_ms,
                is_staff=e.is_staff,
                confidence=e.confidence,
                event_metadata=e.event_metadata,
            )
            normalized_evts.append(e_norm)

        # Debounce duplicate zone entries within 10s
        debounced_evts = []
        last_zone_enter: Dict[str, datetime] = {}

        for e in normalized_evts:
            if e.event_type == "ZONE_ENTER" and e.zone_id:
                t = _parse_iso(e.timestamp)
                if e.zone_id in last_zone_enter and (t - last_zone_enter[e.zone_id]).total_seconds() < 10:
                    continue
                last_zone_enter[e.zone_id] = t
                debounced_evts.append(e)
            else:
                debounced_evts.append(e)

        earliest_t = _parse_iso(normalized_evts[0].timestamp)
        latest_t = _parse_iso(normalized_evts[-1].timestamp)

        has_visited_zone = any(e.event_type in {"ZONE_ENTER", "ZONE_DWELL", "ZONE_EXIT"} for e in debounced_evts)
        has_joined_queue = any(
            e.event_type in {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON", "PURCHASE"}
            for e in debounced_evts
        )

        # ── Purchase detection: 3 levels ──────────────────────────────────────
        # Level 1: explicit PURCHASE event in stream (from emit.py dwell heuristic)
        has_purchased = any(e.event_type == "PURCHASE" for e in debounced_evts)

        # Level 2: POS CSV correlation
        if not has_purchased:
            queue_join_times = [
                _parse_iso(e.timestamp) for e in debounced_evts if e.event_type == "BILLING_QUEUE_JOIN"
            ]
            if queue_join_times:
                first_join = min(queue_join_times)
                for txn_ts in txns:
                    if first_join <= txn_ts <= first_join + timedelta(minutes=5):
                        has_purchased = True
                        break

        # Level 3: Analytics-side dwell heuristic (same 30s threshold)
        if not has_purchased and has_joined_queue:
            has_abandoned = any(e.event_type == "BILLING_QUEUE_ABANDON" for e in debounced_evts)
            if not has_abandoned:
                billing_dwells = [
                    e.dwell_ms for e in debounced_evts
                    if e.event_type in {"BILLING_QUEUE_JOIN", "PURCHASE"} and e.dwell_ms
                ]
                if billing_dwells and max(billing_dwells) >= PURCHASE_DWELL_THRESHOLD_MS:
                    has_purchased = True

        sessions.append({
            "visitor_id": vid,
            "has_entered": True,
            "has_visited_zone": has_visited_zone or has_joined_queue,
            "has_joined_queue": has_joined_queue,
            "has_purchased": has_purchased,
            "entry_time": earliest_t,
            "exit_time": latest_t,
            "events": debounced_evts,
        })

    return sessions


def _hour_label(hour: int) -> str:
    if hour == 0:
        return "12 AM"
    elif hour < 12:
        return f"{hour} AM"
    elif hour == 12:
        return "12 PM"
    else:
        return f"{hour - 12} PM"


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/{id}/metrics", response_model=MetricResponse)
def store_metrics(id: str, request: Request, db: Session = Depends(get_db)) -> MetricResponse:
    events = _get_store_events(db, id)
    latest = _latest_timestamp_iso(events)
    w_start, w_end = _window_for_store(latest)

    start_dt = _parse_iso(w_start)
    end_dt = _parse_iso(w_end)
    window_events = [e for e in events if start_dt <= _parse_iso(e.timestamp) < end_dt]

    request.state.store_id = id
    request.state.event_count = len(window_events)

    sessions = _sessionize_store_events(id, window_events)
    unique_visitors = len(sessions)

    conversion_count = sum(1 for s in sessions if s["has_purchased"])
    conversion_rate = (conversion_count / unique_visitors) if unique_visitors else 0.0

    dwell_by_zone = _build_dwell_by_zone(window_events)
    avg_dwell_per_zone_ms: Dict[str, float] = {}
    for zone_id, durations in dwell_by_zone.items():
        if durations:
            avg_dwell_per_zone_ms[zone_id] = float(mean(durations))

    # Overall average dwell
    all_dwells = [d for zone_d in dwell_by_zone.values() for d in zone_d]
    avg_dwell_ms = float(mean(all_dwells)) if all_dwells else 0.0

    # Most visited zone (by ZONE_ENTER count)
    zone_visit_counts: Dict[str, int] = {}
    for s in sessions:
        for e in s["events"]:
            if e.event_type == "ZONE_ENTER" and e.zone_id:
                zone_visit_counts[e.zone_id] = zone_visit_counts.get(e.zone_id, 0) + 1
    most_visited_zone = max(zone_visit_counts, key=zone_visit_counts.get) if zone_visit_counts else None

    # Repeat visitors = seen in ≥ 2 distinct cameras
    repeat_visitors = 0
    for s in sessions:
        cams = {e.camera_id for e in s["events"] if e.camera_id}
        if len(cams) >= 2:
            repeat_visitors += 1

    txns = _get_pos_for_store(id)
    queue_depth = _calculate_max_queue_depth(id, window_events, txns)

    join_count = sum(1 for s in sessions if s["has_joined_queue"])
    abandon_count = sum(1 for s in sessions if s["has_joined_queue"] and not s["has_purchased"])
    abandonment_rate = (abandon_count / join_count) if join_count else 0.0

    active_visitors = sum(1 for s in sessions if not any(e.event_type == "EXIT" for e in s["events"]))
    staff_excluded = len({e.visitor_id for e in window_events if e.is_staff})

    current_queue = 0
    for s in sessions:
        if s["has_joined_queue"]:
            has_abandoned = any(e.event_type == "BILLING_QUEUE_ABANDON" for e in s["events"])
            has_exited = any(e.event_type == "EXIT" for e in s["events"])
            if not s["has_purchased"] and not has_abandoned and not has_exited:
                current_queue += 1

    return MetricResponse(
        store_id=id,
        unique_visitors=unique_visitors,
        active_visitors=active_visitors,
        staff_excluded=staff_excluded,
        customers=unique_visitors,
        staff_count=staff_excluded,
        current_queue=current_queue,
        conversion_rate=conversion_rate,
        avg_dwell_per_zone_ms=avg_dwell_per_zone_ms,
        avg_dwell_ms=avg_dwell_ms,
        most_visited_zone=most_visited_zone,
        repeat_visitors=repeat_visitors,
        queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
        window_start=w_start,
        window_end=w_end,
    )


@router.get("/{id}/funnel", response_model=FunnelResponse)
def store_funnel(id: str, request: Request, db: Session = Depends(get_db)) -> FunnelResponse:
    events = _get_store_events(db, id)
    latest = _latest_timestamp_iso(events)
    w_start, w_end = _window_for_store(latest)
    start_dt = _parse_iso(w_start)
    end_dt = _parse_iso(w_end)
    window_events = [e for e in events if start_dt <= _parse_iso(e.timestamp) < end_dt]

    request.state.store_id = id
    request.state.event_count = len(window_events)

    sessions = _sessionize_store_events(id, window_events)

    stages = {
        "entry": len(sessions),
        "zone_visit": sum(1 for s in sessions if s["has_visited_zone"]),
        "billing_queue": sum(1 for s in sessions if s["has_joined_queue"]),
        "purchase": sum(1 for s in sessions if s["has_purchased"]),
    }

    stages["zone_visit"] = min(stages["zone_visit"], stages["entry"])
    stages["billing_queue"] = min(stages["billing_queue"], stages["zone_visit"])
    stages["purchase"] = min(stages["purchase"], stages["billing_queue"])

    def _drop(prev: int, nxt: int) -> float:
        return ((prev - nxt) / prev * 100.0) if prev else 0.0

    drop_off_percent = {
        "entry_to_zone_visit":         _drop(stages["entry"],        stages["zone_visit"]),
        "zone_to_billing_queue":        _drop(stages["zone_visit"],   stages["billing_queue"]),
        "billing_queue_to_purchase":    _drop(stages["billing_queue"], stages["purchase"]),
    }

    return FunnelResponse(
        store_id=id,
        stages=stages,
        drop_off_percent=drop_off_percent,
        window_start=w_start,
        window_end=w_end,
    )


@router.get("/{id}/heatmap", response_model=HeatmapResponse)
def store_heatmap(id: str, request: Request, db: Session = Depends(get_db)) -> HeatmapResponse:
    events = _get_store_events(db, id)
    latest = _latest_timestamp_iso(events)
    w_start, w_end = _window_for_store(latest)
    start_dt = _parse_iso(w_start)
    end_dt = _parse_iso(w_end)
    window_events = [e for e in events if start_dt <= _parse_iso(e.timestamp) < end_dt]

    request.state.store_id = id
    request.state.event_count = len(window_events)

    sessions = _sessionize_store_events(id, window_events)
    unique_visitors = len(sessions)

    # Track zone visits and billing visits
    zone_visits: Dict[str, set[str]] = {}
    for s in sessions:
        for e in s["events"]:
            if e.event_type == "ZONE_ENTER" and e.zone_id:
                zone_visits.setdefault(e.zone_id, set()).add(s["visitor_id"])
            # Count billing/checkout visits too
            elif e.event_type == "BILLING_QUEUE_JOIN" and e.zone_id:
                zone_visits.setdefault(e.zone_id, set()).add(s["visitor_id"])

    dwell_by_zone = _build_dwell_by_zone(window_events)

    zones_list: List[HeatmapZone] = []
    for zone_id, visitor_ids in zone_visits.items():
        visits = len(visitor_ids)
        durations = dwell_by_zone.get(zone_id, [])
        avg_dwell_ms = float(mean(durations)) if durations else 0.0
        score = min(100.0, (visits * 10.0) + (avg_dwell_ms / 5000.0))
        zones_list.append(HeatmapZone(
            zone_id=zone_id,
            visits=visits,
            avg_dwell_ms=avg_dwell_ms,
            score_0_100=score,
        ))

    zones_list.sort(key=lambda z: z.score_0_100, reverse=True)
    data_confidence = "low" if unique_visitors < 3 else "high"

    return HeatmapResponse(
        store_id=id,
        zones=zones_list,
        data_confidence=data_confidence,
        window_start=w_start,
        window_end=w_end,
    )


@router.get("/{id}/anomalies", response_model=AnomaliesResponse)
def store_anomalies(id: str, request: Request, db: Session = Depends(get_db)) -> AnomaliesResponse:
    events = _get_store_events(db, id)
    latest = _latest_timestamp_iso(events)
    w_start, w_end = _window_for_store(latest)
    start_dt = _parse_iso(w_start)
    end_dt = _parse_iso(w_end)
    window_events = [e for e in events if start_dt <= _parse_iso(e.timestamp) < end_dt]

    request.state.store_id = id
    request.state.event_count = len(window_events)

    anomalies: List[AnomalyItem] = []
    sessions = _sessionize_store_events(id, window_events)
    unique_visitors = len(sessions)

    txns = _get_pos_for_store(id)
    queue_depth = _calculate_max_queue_depth(id, window_events, txns)

    join_count  = sum(1 for s in sessions if s["has_joined_queue"])
    converted   = sum(1 for s in sessions if s["has_purchased"])
    abandon_count = sum(1 for s in sessions if s["has_joined_queue"] and not s["has_purchased"])
    conversion_rate = (converted / unique_visitors) if unique_visitors else 0.0

    # Current queue size
    current_queue = 0
    for s in sessions:
        if s["has_joined_queue"]:
            has_ab  = any(e.event_type == "BILLING_QUEUE_ABANDON" for e in s["events"])
            has_exit = any(e.event_type == "EXIT"              for e in s["events"])
            has_pur = s["has_purchased"]
            if not has_pur and not has_ab and not has_exit:
                current_queue += 1

    # ── 1. Long billing queue ─────────────────────────────────────────────────
    if current_queue >= 5:
        anomalies.append(AnomalyItem(
            anomaly_type="LONG_BILLING_QUEUE",
            severity="CRITICAL",
            suggested_action=f"Current queue depth {current_queue} exceeds threshold of 5. Open additional billing counter immediately.",
            details={"current_queue": current_queue, "threshold": 5},
        ))
    elif queue_depth >= 3:
        anomalies.append(AnomalyItem(
            anomaly_type="QUEUE_BUILDING",
            severity="WARN",
            suggested_action=f"Peak queue depth reached {queue_depth}. Monitor billing counter throughput.",
            details={"peak_queue_depth": queue_depth, "current_queue": current_queue},
        ))

    # ── 2. High abandonment rate ──────────────────────────────────────────────
    if join_count >= 2:
        aband_rate = abandon_count / join_count
        if aband_rate > 0.6:
            anomalies.append(AnomalyItem(
                anomaly_type="HIGH_ABANDONMENT_RATE",
                severity="CRITICAL",
                suggested_action=f"{aband_rate * 100:.0f}% of billing visitors left without purchasing. Reduce queue wait time.",
                details={"abandonment_rate": round(aband_rate, 2), "abandon_count": abandon_count, "join_count": join_count},
            ))
        elif aband_rate > 0.35:
            anomalies.append(AnomalyItem(
                anomaly_type="ELEVATED_ABANDONMENT",
                severity="WARN",
                suggested_action=f"Abandonment rate {aband_rate * 100:.0f}% is elevated. Consider adding more billing staff.",
                details={"abandonment_rate": round(aband_rate, 2)},
            ))

    # ── 3. Long dwell alert ───────────────────────────────────────────────────
    long_dwell_found: List[Dict[str, Any]] = []
    for s in sessions:
        for e in s["events"]:
            if e.event_type == "ZONE_EXIT" and e.dwell_ms and e.dwell_ms > 600_000:  # > 10 min
                long_dwell_found.append({
                    "visitor_id": s["visitor_id"],
                    "zone_id": e.zone_id or "?",
                    "dwell_min": round(e.dwell_ms / 60000, 1),
                })
    if long_dwell_found:
        ld = long_dwell_found[0]
        anomalies.append(AnomalyItem(
            anomaly_type="LONG_DWELL_ALERT",
            severity="WARN",
            suggested_action=f"Visitor {ld['visitor_id']} spent {ld['dwell_min']} min in {ld['zone_id']}. Possible assistance needed or shelf confusion.",
            details={"instances": len(long_dwell_found), **ld},
        ))

    # ── 4. Ghost visitors (entered, no zone visit) ────────────────────────────
    ghost_count = sum(1 for s in sessions if not s["has_visited_zone"])
    if ghost_count >= 2:
        anomalies.append(AnomalyItem(
            anomaly_type="GHOST_VISITORS",
            severity="WARN",
            suggested_action=f"{ghost_count} visitor(s) entered but browsed no product zones. Review store navigation and signage.",
            details={"ghost_visitor_count": ghost_count, "total_visitors": unique_visitors},
        ))

    # ── 5. Conversion drop ────────────────────────────────────────────────────
    if unique_visitors >= 3 and conversion_rate == 0.0:
        anomalies.append(AnomalyItem(
            anomaly_type="ZERO_CONVERSIONS",
            severity="CRITICAL",
            suggested_action="No purchases detected despite queue activity. Verify billing camera coverage and POS correlation.",
            details={"visitors": unique_visitors, "queue_joins": join_count},
        ))
    elif unique_visitors >= 5 and conversion_rate < 0.1:
        anomalies.append(AnomalyItem(
            anomaly_type="CONVERSION_DROP",
            severity="WARN",
            suggested_action=f"Conversion rate {conversion_rate * 100:.1f}% is critically low. Check product availability and pricing.",
            details={"conversion_rate": round(conversion_rate, 3)},
        ))

    # ── 6. Dead zones (no ENTER activity in last 30 min) ─────────────────────
    if latest:
        now_dt = _parse_iso(latest)
        zone_last_visit: Dict[str, datetime] = {}
        for s in sessions:
            for e in s["events"]:
                if e.event_type == "ZONE_ENTER" and e.zone_id:
                    ts = _parse_iso(e.timestamp)
                    zone_last_visit[e.zone_id] = max(
                        zone_last_visit.get(e.zone_id, datetime.min.replace(tzinfo=timezone.utc)),
                        ts,
                    )
        for zone_id, last_ts in zone_last_visit.items():
            if now_dt - last_ts > timedelta(minutes=30):
                anomalies.append(AnomalyItem(
                    anomaly_type="DEAD_ZONE",
                    severity="WARN",
                    suggested_action=f"No visitors in {zone_id} for 30+ minutes. Consider promotional activity or staff presence.",
                    details={"zone_id": zone_id, "last_visit_ts": last_ts.strftime("%Y-%m-%dT%H:%M:%SZ")},
                ))

    return AnomaliesResponse(
        store_id=id,
        anomalies=anomalies,
        window_start=w_start,
        window_end=w_end,
    )


@router.get("/{id}/peak-hours", response_model=PeakHourResponse)
def store_peak_hours(id: str, request: Request, db: Session = Depends(get_db)) -> PeakHourResponse:
    events = _get_store_events(db, id)
    latest = _latest_timestamp_iso(events)
    w_start, w_end = _window_for_store(latest)
    start_dt = _parse_iso(w_start)
    end_dt = _parse_iso(w_end)
    window_events = [e for e in events if start_dt <= _parse_iso(e.timestamp) < end_dt]

    request.state.store_id = id

    # Count unique customer visitors per hour (using ENTRY events, fallback to any event)
    hourly: Dict[int, set] = {}
    entry_events = [e for e in window_events if e.event_type == "ENTRY" and not e.is_staff]
    source_events = entry_events if entry_events else [e for e in window_events if not e.is_staff]

    for e in source_events:
        hour = _parse_iso(e.timestamp).hour
        hourly.setdefault(hour, set()).add(e.visitor_id)

    buckets = [
        HourlyBucket(hour=h, label=_hour_label(h), visitor_count=len(vids))
        for h, vids in sorted(hourly.items())
    ]

    if buckets:
        peak = max(buckets, key=lambda b: b.visitor_count)
        peak_hour, peak_count = peak.hour, peak.visitor_count
    else:
        peak_hour, peak_count = 12, 0

    return PeakHourResponse(
        store_id=id,
        peak_hour=peak_hour,
        peak_hour_label=_hour_label(peak_hour),
        peak_count=peak_count,
        hourly_buckets=buckets,
        window_start=w_start,
        window_end=w_end,
    )
