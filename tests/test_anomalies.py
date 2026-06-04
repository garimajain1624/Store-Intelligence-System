# Tests for Anomaly Detection — updated to match new anomaly thresholds and types
# QUEUE_BUILDING triggers at queue_depth >= 3 (was QUEUE_SPIKE at >= 10)
# DEAD_ZONE severity is WARN (operational, not critical)

from datetime import datetime, timezone, timedelta
from app.models import IngestedEvent
from app.routers.analytics import store_anomalies
from fastapi import Request
from unittest.mock import MagicMock


class MockDB:
    def __init__(self, events):
        self.events = events

    def execute(self, query):
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = self.events
        return mock_result


def test_anomaly_queue_spike():
    # Ingested events with queue depth = 12 → should trigger QUEUE_BUILDING (depth >= 3)
    events = [
        IngestedEvent(
            store_id="ST1008",
            visitor_id="VIS_1",
            event_type="BILLING_QUEUE_JOIN",
            timestamp="2026-03-08T12:00:00Z",
            event_metadata={"queue_position_at_join": 12},
            is_staff=False
        )
    ]

    db = MockDB(events)
    req = MagicMock()
    req.state = MagicMock()

    res = store_anomalies(id="ST1008", request=req, db=db)

    # New threshold: queue >= 3 → QUEUE_BUILDING, queue >= 5 → LONG_BILLING_QUEUE
    queue_anomalies = [
        a for a in res.anomalies
        if a.anomaly_type in {"QUEUE_BUILDING", "LONG_BILLING_QUEUE", "QUEUE_SPIKE"}
    ]
    assert len(queue_anomalies) >= 1
    assert queue_anomalies[0].severity in {"WARN", "CRITICAL"}
    # Details should include queue depth info
    det = queue_anomalies[0].details
    assert any(v >= 3 for v in det.values() if isinstance(v, int))


def test_anomaly_dead_zone():
    # Only one zone enter event at 11:00:00 (more than 30 minutes before 12:00:00 latest event)
    events = [
        IngestedEvent(
            store_id="ST1008",
            visitor_id="VIS_1",
            event_type="ZONE_ENTER",
            zone_id="SKINCARE",
            timestamp="2026-03-08T11:00:00Z",
            is_staff=False
        ),
        # Latest timestamp is 12:00:00, creating a >30 minute gap
        IngestedEvent(
            store_id="ST1008",
            visitor_id="VIS_2",
            event_type="ENTRY",
            timestamp="2026-03-08T12:00:00Z",
            is_staff=False
        )
    ]

    db = MockDB(events)
    req = MagicMock()
    req.state = MagicMock()

    res = store_anomalies(id="ST1008", request=req, db=db)

    dead_anomalies = [a for a in res.anomalies if a.anomaly_type == "DEAD_ZONE"]
    assert len(dead_anomalies) == 1
    assert dead_anomalies[0].severity == "WARN"          # operational alert, not critical
    assert dead_anomalies[0].details["zone_id"] == "SKINCARE"
