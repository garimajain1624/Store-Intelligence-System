# PROMPT: "Write pytest cases for the Anomaly Detection module of a Store Intelligence System: verify that DEAD_ZONE flags when no zone enter exists for >30 minutes, QUEUE_SPIKE flags on depth >= 10, and CONVERSION_DROP flags on low conversions."
# CHANGES MADE: Aligned with the exact anomaly keys and details payload structures.

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
    # Ingested events with queue depth = 12
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
    
    spike_anomalies = [a for a in res.anomalies if a.anomaly_type == "QUEUE_SPIKE"]
    assert len(spike_anomalies) == 1
    assert spike_anomalies[0].severity == "WARN"
    assert spike_anomalies[0].details["queue_depth"] == 12


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
    assert dead_anomalies[0].severity == "CRITICAL"
    assert dead_anomalies[0].details["zone_id"] == "SKINCARE"
