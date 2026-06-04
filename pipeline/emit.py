import argparse
import csv
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


def load_jsonl(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
        
    if content.startswith("["):
        # It's a regular JSON array
        return json.loads(content)
        
    events: List[Dict[str, Any]] = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError("Each JSONL line must be a JSON object")
        events.append(obj)
    return events


def load_pos_transactions(pos_path: Path) -> List[Dict[str, Any]]:
    if not pos_path.exists():
        return []
    txns = []
    with pos_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            txns.append(row)
    return txns


def parse_pos_time(row: Dict[str, Any]) -> datetime:
    # Handle multiple formats
    if "timestamp" in row:
        ts = row["timestamp"].strip()
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts).astimezone(timezone.utc)
    else:
        date_str = row.get("order_date", "")
        time_str = row.get("order_time", "")
        try:
            return datetime.strptime(f"{date_str} {time_str}", "%d-%m-%Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def generate_events_from_tracks(
    tracks: List[Dict[str, Any]],
    pos_txns: List[Dict[str, Any]],
    base_time: datetime
) -> List[Dict[str, Any]]:
    # Group track rows by visitor_id
    visitor_tracks: Dict[str, List[Dict[str, Any]]] = {}
    for row in tracks:
        vid = row["visitor_id"]
        visitor_tracks.setdefault(vid, []).append(row)

    events: List[Dict[str, Any]] = []

    for vid, rows in visitor_tracks.items():
        # Sort rows by timestamp_ms
        rows.sort(key=lambda r: r["timestamp_ms"])
        is_staff = rows[0].get("is_staff", False)
        store_id = rows[0]["store_id"]
        confidence = float(np_mean([r["confidence"] for r in rows])) if rows else 0.9

        # Group by camera_id
        cam_groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            cam_groups.setdefault(r["camera_id"], []).append(r)

        session_seq = 0

        # Sort cameras by the first appearance of the visitor in them
        sorted_cams = sorted(cam_groups.keys(), key=lambda c: cam_groups[c][0]["timestamp_ms"])

        # Track prior exits for REENTRY check
        has_exited = False

        for cam_id in sorted_cams:
            cam_rows = cam_groups[cam_id]
            start_ms = cam_rows[0]["timestamp_ms"]
            end_ms = cam_rows[-1]["timestamp_ms"]
            duration_ms = end_ms - start_ms

            start_iso = (base_time + timedelta(milliseconds=start_ms)).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_iso = (base_time + timedelta(milliseconds=end_ms)).strftime("%Y-%m-%dT%H:%M:%SZ")
            start_dt = base_time + timedelta(milliseconds=start_ms)
            end_dt = base_time + timedelta(milliseconds=end_ms)

            # Determine event types based on camera roles
            if "entry" in cam_id.lower():
                session_seq += 1
                # ENTRY or REENTRY
                etype = "REENTRY" if has_exited else "ENTRY"
                events.append({
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": cam_id,
                    "visitor_id": vid,
                    "event_type": etype,
                    "timestamp": start_iso,
                    "zone_id": None,
                    "dwell_ms": 0,
                    "is_staff": is_staff,
                    "confidence": confidence,
                    "metadata": {
                        "queue_depth": None,
                        "sku_zone": None,
                        "session_seq": session_seq
                    }
                })

                # EXIT
                session_seq += 1
                events.append({
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": cam_id,
                    "visitor_id": vid,
                    "event_type": "EXIT",
                    "timestamp": end_iso,
                    "zone_id": None,
                    "dwell_ms": duration_ms,
                    "is_staff": is_staff,
                    "confidence": confidence,
                    "metadata": {
                        "queue_depth": None,
                        "sku_zone": None,
                        "session_seq": session_seq
                    }
                })
                has_exited = True

            elif "zone" in cam_id.lower():
                # Map camera to zone_id and sku_zone
                zone_id, sku_zone = get_zone_mapping(cam_id)

                # ZONE_ENTER
                session_seq += 1
                events.append({
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": cam_id,
                    "visitor_id": vid,
                    "event_type": "ZONE_ENTER",
                    "timestamp": start_iso,
                    "zone_id": zone_id,
                    "dwell_ms": 0,
                    "is_staff": is_staff,
                    "confidence": confidence,
                    "metadata": {
                        "queue_depth": None,
                        "sku_zone": sku_zone,
                        "session_seq": session_seq
                    }
                })

                # ZONE_DWELL: every 30 seconds
                dwells_count = int(duration_ms // 30000)
                for d in range(1, dwells_count + 1):
                    session_seq += 1
                    dwell_time_iso = (start_dt + timedelta(seconds=d*30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    events.append({
                        "event_id": str(uuid.uuid4()),
                        "store_id": store_id,
                        "camera_id": cam_id,
                        "visitor_id": vid,
                        "event_type": "ZONE_DWELL",
                        "timestamp": dwell_time_iso,
                        "zone_id": zone_id,
                        "dwell_ms": d * 30000,
                        "is_staff": is_staff,
                        "confidence": confidence,
                        "metadata": {
                            "queue_depth": None,
                            "sku_zone": sku_zone,
                            "session_seq": session_seq
                        }
                    })

                # ZONE_EXIT
                session_seq += 1
                events.append({
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": cam_id,
                    "visitor_id": vid,
                    "event_type": "ZONE_EXIT",
                    "timestamp": end_iso,
                    "zone_id": zone_id,
                    "dwell_ms": duration_ms,
                    "is_staff": is_staff,
                    "confidence": confidence,
                    "metadata": {
                        "queue_depth": None,
                        "sku_zone": sku_zone,
                        "session_seq": session_seq
                    }
                })

            elif "bill" in cam_id.lower():
                # Count current occupants in the billing zone to get queue depth
                other_active = 0
                for other_rows in visitor_tracks.values():
                    if other_rows[0]["visitor_id"] == vid:
                        continue
                    for orw in other_rows:
                        if orw["camera_id"] == cam_id:
                            if orw["timestamp_ms"] <= start_ms <= orw["timestamp_ms"] + 5000:
                                other_active += 1
                                break

                # BILLING_QUEUE_JOIN
                session_seq += 1
                events.append({
                    "event_id": str(uuid.uuid4()),
                    "store_id": store_id,
                    "camera_id": cam_id,
                    "visitor_id": vid,
                    "event_type": "BILLING_QUEUE_JOIN",
                    "timestamp": start_iso,
                    "zone_id": "CHECKOUT",
                    "dwell_ms": 0,
                    "is_staff": is_staff,
                    "confidence": confidence,
                    "metadata": {
                        "queue_depth": other_active,
                        "sku_zone": None,
                        "session_seq": session_seq
                    }
                })

                # ── Purchase detection ────────────────────────────────────────
                # Priority 1: POS CSV correlation
                is_converted = False
                matched_txn_dt = None
                for txn in pos_txns:
                    if txn["store_id"] == store_id:
                        txn_dt = parse_pos_time(txn)
                        if start_dt <= txn_dt <= start_dt + timedelta(minutes=5):
                            is_converted = True
                            matched_txn_dt = txn_dt
                            break

                # Priority 2: Dwell heuristic — stayed ≥ 30s at billing = purchase
                # (realistic: quick browse-and-leave < 30s = abandon; staying = purchase intent)
                PURCHASE_DWELL_THRESHOLD_MS = 30_000
                if not is_converted and not is_staff:
                    if duration_ms >= PURCHASE_DWELL_THRESHOLD_MS:
                        is_converted = True

                if is_converted and not is_staff:
                    # Emit PURCHASE event
                    session_seq += 1
                    purchase_iso = matched_txn_dt.strftime("%Y-%m-%dT%H:%M:%SZ") if matched_txn_dt else end_iso
                    events.append({
                        "event_id": str(uuid.uuid4()),
                        "store_id": store_id,
                        "camera_id": cam_id,
                        "visitor_id": vid,
                        "event_type": "PURCHASE",
                        "timestamp": purchase_iso,
                        "zone_id": "CHECKOUT",
                        "dwell_ms": duration_ms,
                        "is_staff": is_staff,
                        "confidence": confidence,
                        "metadata": {
                            "queue_depth": other_active,
                            "sku_zone": None,
                            "session_seq": session_seq,
                            "purchase_method": "pos_correlation" if matched_txn_dt else "dwell_heuristic"
                        }
                    })
                elif not is_staff:
                    # Short dwell at billing = abandoned queue
                    session_seq += 1
                    events.append({
                        "event_id": str(uuid.uuid4()),
                        "store_id": store_id,
                        "camera_id": cam_id,
                        "visitor_id": vid,
                        "event_type": "BILLING_QUEUE_ABANDON",
                        "timestamp": end_iso,
                        "zone_id": "CHECKOUT",
                        "dwell_ms": duration_ms,
                        "is_staff": is_staff,
                        "confidence": confidence,
                        "metadata": {
                            "queue_depth": other_active,
                            "sku_zone": None,
                            "session_seq": session_seq
                        }
                    })

    # Sort events by timestamp
    events.sort(key=lambda e: e["timestamp"])
    return events


def np_mean(vals: List[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def get_zone_mapping(cam_id: str) -> Tuple[str, str]:
    """Map camera ID to (zone_name, sku_category) for rich multi-zone heatmap."""
    c = cam_id.lower()
    if "zone_01" in c or "zone 01" in c or "cam 1" in c:
        return "SKINCARE", "Moisturisers & Serums"
    elif "zone_02" in c or "zone 02" in c or "cam 2" in c:
        return "MAKEUP", "Foundation & Lipstick"
    elif "zone_03" in c or "zone 03" in c or "cam 3" in c:
        return "HAIRCARE", "Shampoo & Conditioner"
    elif "zone_04" in c or "zone 04" in c or "cam 4" in c:
        return "FRAGRANCE", "Perfumes & Deodorants"
    elif "bill" in c:
        return "CHECKOUT", "Billing Counter"
    return "FRAGRANCE", "Perfumes & Deodorants"


def normalize_existing_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Tolerant normalization mapping (copied from router logic)
    normalized: List[Dict[str, Any]] = []
    
    # Store-level mapping
    def norm_store(e):
        if e.get("store_id"): return str(e["store_id"])
        store_code = e.get("store_code")
        if isinstance(store_code, str) and store_code.startswith("store_"):
            return "ST" + store_code.replace("store_", "", 1)
        return "STORE_BLR_002"

    def norm_visitor(e):
        if e.get("visitor_id"): return str(e["visitor_id"])
        if e.get("track_id") is not None: return f"VIS_{e['track_id']}"
        if e.get("id_token") is not None: return str(e["id_token"])
        return "VIS_unknown"

    def norm_type(e):
        et = e.get("event_type", "").upper()
        mapping = {
            "ENTRY": "ENTRY", "EXIT": "EXIT",
            "ZONE_ENTERED": "ZONE_ENTER", "ZONE_EXITED": "ZONE_EXIT",
            "ZONE_ENTER": "ZONE_ENTER", "ZONE_EXIT": "ZONE_EXIT",
            "QUEUE_COMPLETED": "BILLING_QUEUE_JOIN",
            "QUEUE_ABANDONED": "BILLING_QUEUE_ABANDON",
            "BILLING_QUEUE_JOIN": "BILLING_QUEUE_JOIN",
            "BILLING_QUEUE_ABANDON": "BILLING_QUEUE_ABANDON"
        }
        return mapping.get(et, et or "ENTRY")

    for e in events:
        store_id = norm_store(e)
        visitor_id = norm_visitor(e)
        event_type = norm_type(e)
        
        # Extract timestamp
        ts = e.get("timestamp") or e.get("event_timestamp") or e.get("event_time") or e.get("queue_join_ts") or "2026-03-08T18:10:00Z"
        
        # Ensure event_id is uuid
        eid = e.get("event_id") or e.get("queue_event_id") or str(uuid.uuid4())
        
        normalized.append({
            "event_id": eid,
            "store_id": store_id,
            "camera_id": e.get("camera_id", "CAM_ENTRY_01"),
            "visitor_id": visitor_id,
            "event_type": event_type,
            "timestamp": ts,
            "zone_id": e.get("zone_id"),
            "dwell_ms": e.get("dwell_ms", 0),
            "is_staff": bool(e.get("is_staff", False)),
            "confidence": float(e.get("confidence", 0.9)),
            "metadata": e.get("metadata", {})
        })
    return normalized


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Convert/generate event logs to JSON for API ingestion.")
    p.add_argument("--input", required=True, help="Input JSONL file (events or visitor tracks).")
    p.add_argument("--output", required=True, help="Output JSON file.")
    p.add_argument("--pos-csv", default="D:/project/POS - sample transactionsb1e826f.csv", help="POS CSV path.")
    p.add_argument("--wrap", action="store_true", help="Wrap as {\"events\": [...]} instead of a bare array.")
    args = p.parse_args(argv)

    input_path = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    pos_path = Path(args.pos_csv).expanduser().resolve()

    raw_data = load_jsonl(input_path)
    
    if not raw_data:
        output_obj = [] if not args.wrap else {"events": []}
        output_path.write_text(json.dumps(output_obj, ensure_ascii=False, indent=2))
        return 0

    # Distinguish input type: visitor tracks vs event logs
    if "bbox_xyxy" in raw_data[0] or "visitor_id" in raw_data[0] and "event_type" not in raw_data[0]:
        # Generate behavioral events from visitor tracks
        pos_txns = load_pos_transactions(pos_path)
        
        # Base timeline start on the first POS transaction date, or standard default
        base_time = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
        if pos_txns:
            try:
                base_time = parse_pos_time(pos_txns[0]) - timedelta(minutes=2)
            except Exception:
                pass
                
        events = generate_events_from_tracks(raw_data, pos_txns, base_time)
    else:
        # Input is already events, normalize it
        events = normalize_existing_events(raw_data)

    output_obj: Any = events if not args.wrap else {"events": events}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    if output_path.suffix.lower() == ".jsonl":
        with output_path.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
    else:
        output_path.write_text(json.dumps(output_obj, ensure_ascii=False, indent=2), encoding="utf-8")
        
    print(json.dumps({"ok": True, "events": len(events), "output": str(output_path)}))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main())
