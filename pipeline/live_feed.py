"""
live_feed.py — Real-time YOLO detection + event emission for live camera feeds.

Supports:
  --source 0              Laptop/USB webcam (cv2.VideoCapture(0))
  --source rtsp://...     RTSP IP camera feed
  --source video.mp4      Recorded video (for testing without a live camera)

Events are POSTed directly to the Store Intelligence API in real-time.

Usage examples:
  python pipeline/live_feed.py --source 0 --camera-id CAM_ENTRY_01
  python pipeline/live_feed.py --source rtsp://192.168.1.10:554/stream --camera-id CAM_ZONE_01
  python pipeline/live_feed.py --source "data/Store_1/Store 1/CAM 3 - entry.mp4" --camera-id CAM_ENTRY_01 --replay
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import requests

try:
    from ultralytics import YOLO
except ImportError:
    print("ERROR: ultralytics not installed. Run: pip install ultralytics")
    sys.exit(1)


# ── Constants ──────────────────────────────────────────────────────────────────
PERSON_CLASS_ID = 0
DEFAULT_API_URL  = "http://localhost:8000"
DEFAULT_STORE_ID = "STORE_BLR_002"
DEFAULT_MODEL    = "yolov8n.pt"

# Camera role → zone mapping (same as detect.py logic)
CAM_ROLE_MAP = {
    "entry":   ("ENTRY",   None),
    "billing": ("BILLING", "BILLING"),
    "zone_01": ("ZONE",    "SKINCARE"),
    "zone_02": ("ZONE",    "HAIRCARE"),
    "zone":    ("ZONE",    "COSMETICS"),
}


def _derive_role(camera_id: str) -> Tuple[str, Optional[str]]:
    c = camera_id.lower()
    if "entry" in c:
        return "ENTRY", None
    elif "billing" in c:
        return "BILLING", "BILLING"
    elif "zone" in c:
        if "01" in c or "_1" in c:
            return "ZONE", "SKINCARE"
        elif "02" in c or "_2" in c:
            return "ZONE", "HAIRCARE"
        return "ZONE", "COSMETICS"
    return "UNKNOWN", None


def _make_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    zone_id: Optional[str],
    dwell_ms: int,
    confidence: float,
    is_staff: bool = False,
    queue_depth: int = 0,
) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": round(confidence, 4),
        "metadata":   {"queue_depth": queue_depth, "source": "live_feed"},
    }


def post_events(events: List[dict], api_url: str, verbose: bool = False) -> None:
    """POST a batch of events to the API. Non-blocking best-effort."""
    if not events:
        return
    try:
        r = requests.post(
            f"{api_url}/events/ingest",
            json=events,
            timeout=3,
        )
        if verbose:
            print(f"  [API] Ingested {len(events)} events → {r.status_code}")
    except Exception as e:
        if verbose:
            print(f"  [API] POST failed: {e}")


class TrackState:
    """Track per-visitor-ID state across frames for dwell time and event emission."""

    def __init__(self) -> None:
        self._entered: dict[str, float] = {}     # track_id → entry wall-clock time
        self._last_seen: dict[str, float] = {}   # track_id → last wall-clock time
        self._lost_threshold: float = 3.0        # seconds before track is considered gone

    def update(self, track_ids: List[int], now: float) -> Tuple[List[int], List[Tuple[int, float]]]:
        """
        Returns:
          new_ids   — track IDs appearing for the first time (ENTRY/ZONE_ENTER)
          lost_ids  — (track_id, dwell_ms) for tracks that disappeared (EXIT/ZONE_EXIT)
        """
        new_ids: List[int] = []
        lost_ids: List[Tuple[int, float]] = []

        active = set(track_ids)

        # Detect lost tracks
        for tid in list(self._entered.keys()):
            if tid not in active:
                elapsed = now - self._last_seen.get(tid, now)
                if elapsed >= self._lost_threshold:
                    dwell_ms = int((now - self._entered[tid]) * 1000)
                    lost_ids.append((tid, dwell_ms))
                    del self._entered[tid]
                    self._last_seen.pop(tid, None)

        # Detect new tracks
        for tid in track_ids:
            if tid not in self._entered:
                self._entered[tid] = now
                new_ids.append(tid)
            self._last_seen[tid] = now

        return new_ids, lost_ids


def run_live(
    *,
    source,
    camera_id: str,
    store_id: str,
    model_path: str,
    api_url: str,
    conf: float,
    imgsz: int,
    device: str,
    tracker_cfg: str,
    show_preview: bool,
    replay: bool,
    emit_interval: int,
    verbose: bool,
) -> None:
    model = YOLO(model_path)
    role, zone_id = _derive_role(camera_id)
    state = TrackState()

    # Try to parse source as integer (webcam index)
    try:
        source = int(source)
    except (ValueError, TypeError):
        pass

    print(f"[live_feed] Starting — source={source!r}  camera={camera_id}  role={role}  zone={zone_id}")
    print(f"[live_feed] API={api_url}  store={store_id}")

    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video source: {source!r}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30)
    frame_idx = 0
    pending_events: List[dict] = []
    last_emit = time.time()

    try:
        generator = model.track(
            source=source,
            stream=True,
            persist=True,
            conf=conf,
            imgsz=imgsz,
            device=device if device != "auto" else None,
            tracker=tracker_cfg,
            verbose=False,
            classes=[PERSON_CLASS_ID],
        )

        for result in generator:
            now = time.time()
            frame_idx += 1

            boxes = getattr(result, "boxes", None)
            track_ids: List[int] = []
            confidences: dict[int, float] = {}

            if boxes is not None:
                ids_t = getattr(boxes, "id", None)
                conf_t = getattr(boxes, "conf", None)
                cls_t  = getattr(boxes, "cls", None)

                if ids_t is not None and conf_t is not None and cls_t is not None:
                    ids_np   = ids_t.cpu().numpy()
                    conf_np  = conf_t.cpu().numpy()
                    cls_np   = cls_t.cpu().numpy()
                    for i in range(len(ids_np)):
                        if int(cls_np[i]) == PERSON_CLASS_ID:
                            tid = int(ids_np[i])
                            track_ids.append(tid)
                            confidences[tid] = float(conf_np[i])

            new_ids, lost_ids = state.update(track_ids, now)

            # Build ENTRY / ZONE_ENTER events for new tracks
            for tid in new_ids:
                vid = f"VIS_LIVE_{tid:05d}"
                etype = "ENTRY" if role == "ENTRY" else ("BILLING_QUEUE_JOIN" if role == "BILLING" else "ZONE_ENTER")
                ev = _make_event(
                    store_id=store_id,
                    camera_id=camera_id,
                    visitor_id=vid,
                    event_type=etype,
                    zone_id=zone_id,
                    dwell_ms=0,
                    confidence=confidences.get(tid, conf),
                )
                pending_events.append(ev)
                if verbose:
                    print(f"  → {etype}  visitor={vid}")

            # Build EXIT / ZONE_EXIT events for lost tracks
            for tid, dwell_ms in lost_ids:
                vid = f"VIS_LIVE_{tid:05d}"
                etype = "EXIT" if role == "ENTRY" else ("BILLING_QUEUE_ABANDON" if role == "BILLING" else "ZONE_EXIT")
                ev = _make_event(
                    store_id=store_id,
                    camera_id=camera_id,
                    visitor_id=vid,
                    event_type=etype,
                    zone_id=zone_id,
                    dwell_ms=dwell_ms,
                    confidence=conf,
                )
                pending_events.append(ev)
                if verbose:
                    print(f"  ← {etype}  visitor={vid}  dwell={dwell_ms}ms")

            # Emit batch every N seconds
            if now - last_emit >= emit_interval and pending_events:
                post_events(pending_events, api_url, verbose=verbose)
                pending_events = []
                last_emit = now

            # Optional preview window
            if show_preview and result.orig_img is not None:
                frame = result.orig_img.copy()
                cv2.putText(
                    frame,
                    f"Camera: {camera_id}  Tracks: {len(track_ids)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 255, 100), 2,
                )
                cv2.imshow(f"Live Feed — {camera_id}", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("[live_feed] User pressed Q — stopping.")
                    break

    except KeyboardInterrupt:
        print("\n[live_feed] Interrupted — flushing remaining events…")
    finally:
        cap.release()
        if show_preview:
            cv2.destroyAllWindows()
        if pending_events:
            post_events(pending_events, api_url, verbose=True)
        print("[live_feed] Done.")


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Real-time YOLO detection → Store Intelligence API event emission.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Laptop webcam as entry camera
  python pipeline/live_feed.py --source 0 --camera-id CAM_ENTRY_01

  # RTSP IP camera as billing camera
  python pipeline/live_feed.py --source rtsp://192.168.1.10:554/stream --camera-id CAM_BILLING_01

  # Replay a recorded clip (acts as live for demo purposes)
  python pipeline/live_feed.py --source "data/Store_1/Store 1/CAM 3 - entry.mp4" --camera-id CAM_ENTRY_01 --replay --preview
""",
    )
    p.add_argument("--source",      required=True,          help="Camera index (0), RTSP URL, or video file path.")
    p.add_argument("--camera-id",   default="CAM_ENTRY_01", help="Camera ID (used to derive event types).")
    p.add_argument("--store-id",    default=DEFAULT_STORE_ID)
    p.add_argument("--api-url",     default=DEFAULT_API_URL, help="Base URL of the Store Intelligence API.")
    p.add_argument("--model",       default=DEFAULT_MODEL,   help="YOLO model path.")
    p.add_argument("--tracker",     default="bytetrack.yaml")
    p.add_argument("--conf",        type=float, default=0.25, help="Detection confidence threshold.")
    p.add_argument("--imgsz",       type=int,   default=640)
    p.add_argument("--device",      default="auto")
    p.add_argument("--emit-interval", type=int, default=3,    help="Seconds between API POST batches.")
    p.add_argument("--preview",     action="store_true",      help="Show OpenCV preview window (requires display).")
    p.add_argument("--replay",      action="store_true",      help="For recorded video: loop continuously (demo mode).")
    p.add_argument("--verbose",     action="store_true",      help="Print per-event logs.")
    args = p.parse_args(argv)

    run_live(
        source=args.source,
        camera_id=args.camera_id,
        store_id=args.store_id,
        model_path=args.model,
        api_url=args.api_url,
        conf=args.conf,
        imgsz=args.imgsz,
        device=args.device,
        tracker_cfg=args.tracker,
        show_preview=args.preview,
        replay=args.replay,
        emit_interval=args.emit_interval,
        verbose=args.verbose,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
