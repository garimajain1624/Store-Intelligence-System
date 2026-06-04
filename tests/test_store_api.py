# PROMPT: "Create minimal pytest coverage for a FastAPI Store Intelligence API: validate idempotent ingest by event_id and that /metrics, /funnel, /heatmap, /anomalies, and /health return JSON with expected keys."
# CHANGES MADE: Implemented a deterministic in-test SQLite + POS CSV, then used a small synthetic event batch aligned to the internal normalized event types.

import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import create_app


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_ingest_idempotent_and_endpoints_return_json():
    with tempfile.TemporaryDirectory() as td:
        db_path = os.path.join(td, "app.db")
        pos_path = os.path.join(td, "pos.csv")

        # Route analytics conversion-rate computation.
        os.environ["SQLITE_PATH"] = db_path
        os.environ["POS_CSV_PATH"] = pos_path

        # POS CSV format: order_id,order_date,order_time,store_id,product_id,brand_name,total_amount
        # Use a single transaction at 12:00:00 for store ST1.
        txn_dt = datetime(2026, 3, 8, 12, 0, 0, tzinfo=timezone.utc)
        with open(pos_path, "w", encoding="utf-8") as f:
            f.write(
                "order_id,order_date,order_time,store_id,product_id,brand_name,total_amount\n"
                f"1,08-03-2026,12:00:00,ST1,P1,Brand,100.00\n"
            )

        app = create_app()
        with TestClient(app) as client:
            store_id = "ST1"
            visitor_id = "VIS_1"
            zone_id = "ZONE_A"
            camera_id = "CAM_ENTRY_01"

            base = datetime(2026, 3, 8, 11, 59, 30, tzinfo=timezone.utc)
            events = [
                {
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": camera_id,
                    "visitor_id": visitor_id,
                    "event_type": "ENTRY",
                    "timestamp": _iso(base),
                    "zone_id": None,
                    "dwell_ms": 0,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                    "raw": {},
                },
                {
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": "CAM_ZONE_01",
                    "visitor_id": visitor_id,
                    "event_type": "ZONE_ENTER",
                    "timestamp": _iso(datetime(2026, 3, 8, 11, 58, 0, tzinfo=timezone.utc)),
                    "zone_id": zone_id,
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                    "raw": {},
                },
                {
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": "CAM_ZONE_01",
                    "visitor_id": visitor_id,
                    "event_type": "ZONE_EXIT",
                    "timestamp": _iso(datetime(2026, 3, 8, 11, 58, 10, tzinfo=timezone.utc)),
                    "zone_id": zone_id,
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.9,
                    "metadata": {},
                    "raw": {},
                },
                # Billing queue join within 5 minutes before POS txn.
                {
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": "CAM_BILL_01",
                    "visitor_id": visitor_id,
                    "event_type": "BILLING_QUEUE_JOIN",
                    "timestamp": _iso(base),
                    "zone_id": "BILLING_1",
                    "dwell_ms": None,
                    "is_staff": False,
                    "confidence": 0.8,
                    "queue_position_at_join": 2,
                    "metadata": {},
                    "raw": {},
                },
            ]

            r1 = client.post("/events/ingest", json=events)
            assert r1.status_code == 200
            body1 = r1.json()
            assert body1["ingested"] == len(events)

            # Second ingest should be a no-op (idempotent by event_id).
            r2 = client.post("/events/ingest", json=events)
            assert r2.status_code == 200
            body2 = r2.json()
            assert body2["ingested"] == 0

            metrics = client.get(f"/stores/{store_id}/metrics").json()
            assert metrics["store_id"] == store_id
            assert "conversion_rate" in metrics
            assert "queue_depth" in metrics

            funnel = client.get(f"/stores/{store_id}/funnel").json()
            assert funnel["store_id"] == store_id
            assert "stages" in funnel

            heatmap = client.get(f"/stores/{store_id}/heatmap").json()
            assert heatmap["store_id"] == store_id
            assert "zones" in heatmap

            anomalies = client.get(f"/stores/{store_id}/anomalies").json()
            assert anomalies["store_id"] == store_id
            assert "anomalies" in anomalies

            health = client.get("/health").json()
            assert health["status"] in ("ok", "degraded")

            # Dispose connection pool to release SQLite file lock on Windows
            from app.database import get_engine
            get_engine().dispose()
