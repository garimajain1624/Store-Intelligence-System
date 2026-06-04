# PROMPT: "Write pytest cases to test the Metrics and Funnel logic of a Store Intelligence System: verify Unique Visitors excludes is_staff=True events, conversion rate calculates properly using mock POS CSV matching, and avg dwell calculations pair ZONE_ENTER/EXIT correctly."
# CHANGES MADE: Aligned with the database schema and created deterministic test scenarios.

import os
from datetime import datetime, timezone
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, IngestedEvent
from app.routers.analytics import _distinct_visitors, _build_dwell_by_zone, _conversion_visitors


def test_distinct_visitors_excludes_staff():
    events = [
        IngestedEvent(visitor_id="VIS_1", is_staff=False),
        IngestedEvent(visitor_id="VIS_2", is_staff=False),
        IngestedEvent(visitor_id="VIS_3", is_staff=True), # Staff
    ]
    visitors = _distinct_visitors(events)
    assert len(visitors) == 2
    assert "VIS_1" in visitors
    assert "VIS_2" in visitors
    assert "VIS_3" not in visitors


def test_build_dwell_by_zone():
    events = [
        IngestedEvent(visitor_id="VIS_1", zone_id="SKINCARE", event_type="ZONE_ENTER", timestamp="2026-03-08T12:00:00Z", is_staff=False),
        IngestedEvent(visitor_id="VIS_1", zone_id="SKINCARE", event_type="ZONE_EXIT", timestamp="2026-03-08T12:05:00Z", is_staff=False), # 5 min dwell
        IngestedEvent(visitor_id="VIS_2", zone_id="SKINCARE", event_type="ZONE_ENTER", timestamp="2026-03-08T12:10:00Z", is_staff=True), # Staff ignored
        IngestedEvent(visitor_id="VIS_2", zone_id="SKINCARE", event_type="ZONE_EXIT", timestamp="2026-03-08T12:12:00Z", is_staff=True),
    ]
    dwells = _build_dwell_by_zone(events)
    assert "SKINCARE" in dwells
    assert len(dwells["SKINCARE"]) == 1
    assert dwells["SKINCARE"][0] == 300000 # 5 * 60 * 1000 ms
