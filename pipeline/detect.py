import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import cv2
from ultralytics import YOLO


@dataclass(frozen=True)
class TrackRow:
    store_id: str
    camera_id: str
    source_file: str
    frame_index: int
    timestamp_ms: int
    track_id: str
    bbox_xyxy: Tuple[float, float, float, float]
    confidence: float


def _iter_video_files(input_dir: Path) -> List[Path]:
    """Recursively find all video files under input_dir (handles nested store folders)."""
    exts = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
    files = [p for p in input_dir.rglob("*") if p.is_file() and p.suffix.lower() in exts]
    files.sort()
    return files


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _safe_track_id(track_id: Optional[int]) -> str:
    if track_id is None:
        return "TRK_unknown"
    return f"TRK_{int(track_id):06d}"


def _get_fps(video_path: str) -> float:
    cap = cv2.VideoCapture(video_path)
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    finally:
        cap.release()
    return fps if fps > 0 else 0.0


def _results_to_rows(
    *,
    results: Any,
    store_id: str,
    camera_id: str,
    source_file: str,
    frame_index: int,
    timestamp_ms: int,
) -> Iterator[TrackRow]:
    boxes = getattr(results, "boxes", None)
    if boxes is None:
        return

    # Ultralytics: boxes.xyxy (N,4), boxes.conf (N,), boxes.cls (N,), boxes.id (N,) for tracking.
    # We keep low-confidence detections; downstream can filter but we preserve confidence.
    xyxy = boxes.xyxy
    conf = boxes.conf
    cls = boxes.cls
    ids = getattr(boxes, "id", None)

    if xyxy is None or conf is None or cls is None:
        return

    xyxy_np = xyxy.cpu().numpy()
    conf_np = conf.cpu().numpy()
    cls_np = cls.cpu().numpy()
    ids_np = ids.cpu().numpy() if ids is not None else None

    for i in range(xyxy_np.shape[0]):
        cls_i = int(cls_np[i])
        # YOLO class 0 is person for COCO models, but allow custom models by also
        # respecting --class-id override (handled upstream).
        if cls_i != 0:
            continue
        tid = int(ids_np[i]) if ids_np is not None else None
        x1, y1, x2, y2 = (float(v) for v in xyxy_np[i].tolist())
        yield TrackRow(
            store_id=store_id,
            camera_id=camera_id,
            source_file=source_file,
            frame_index=frame_index,
            timestamp_ms=timestamp_ms,
            track_id=_safe_track_id(tid),
            bbox_xyxy=(x1, y1, x2, y2),
            confidence=float(conf_np[i]),
        )


def _derive_camera_id(filename: str, default_camera_id: str) -> str:
    fn = filename.lower()
    if "entry" in fn:
        if "entry 2" in fn or "entry_2" in fn:
            return "CAM_ENTRY_02"
        return "CAM_ENTRY_01"
    elif "billing" in fn:
        return "CAM_BILLING_01"
    elif "zone" in fn:
        if "cam 1" in fn or "cam_1" in fn:
            return "CAM_ZONE_01"
        elif "cam 2" in fn or "cam_2" in fn:
            return "CAM_ZONE_02"
        return "CAM_ZONE_01"
    return default_camera_id


def run_detection(
    *,
    input_dir: Path,
    output_path: Path,
    store_id: str,
    camera_id: str,
    model_name: str,
    conf: float,
    imgsz: int,
    device: str,
    tracker_cfg: str,
    class_id: int,
    max_videos: Optional[int],
    stride: int = 15,
) -> Dict[str, Any]:
    videos = _iter_video_files(input_dir)
    if max_videos is not None:
        videos = videos[: max(0, int(max_videos))]

    if not videos:
        raise FileNotFoundError(f"No video files found under: {input_dir}")

    _ensure_dir(output_path.parent)

    model = YOLO(model_name)

    total_rows = 0
    per_video: Dict[str, Any] = {}

    with output_path.open("w", encoding="utf-8") as f:
        for video_path in videos:
            src = str(video_path)
            fps = _get_fps(src)
            cam_id = _derive_camera_id(video_path.name, camera_id)

            # Note: Ultralytics returns per-frame Results when stream=True.
            # We prefer using the model's internal video loader for correctness across codecs.
            frame_idx = -1
            start = time.time()

            generator = model.track(
                source=src,
                stream=True,
                persist=True,
                conf=conf,
                imgsz=imgsz,
                device=device if device != "auto" else None,
                tracker=tracker_cfg,
                verbose=False,
                classes=[class_id] if class_id is not None else None,
                vid_stride=stride,
            )

            for frame_idx, res in enumerate(generator):
                actual_frame_idx = frame_idx * stride
                if fps > 0:
                    ts_ms = int((actual_frame_idx / fps) * 1000.0)
                else:
                    # Fallback if FPS missing: assume ~30 FPS
                    ts_ms = int((actual_frame_idx / 30.0) * 1000.0)

                for row in _results_to_rows(
                    results=res,
                    store_id=store_id,
                    camera_id=cam_id,
                    source_file=video_path.name,
                    frame_index=actual_frame_idx,
                    timestamp_ms=ts_ms,
                ):
                    f.write(json.dumps(asdict(row), ensure_ascii=False) + "\n")
                    total_rows += 1

            elapsed = time.time() - start
            per_video[video_path.name] = {
                "frames": max(frame_idx + 1, 0),
                "fps": fps,
                "elapsed_s": round(elapsed, 3),
            }

    return {
        "store_id": store_id,
        "camera_id": camera_id,
        "input_dir": str(input_dir),
        "output_path": str(output_path),
        "videos": per_video,
        "rows": total_rows,
    }


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="YOLOv8 + ByteTrack detection/tracking over clip folders.")
    p.add_argument("--input", required=True, help="Folder containing CCTV clips (mp4/mov/mkv/avi/webm).")
    p.add_argument("--output", default="out/tracks.jsonl", help="JSONL output path.")
    p.add_argument("--store-id", default="STORE_BLR_002")
    p.add_argument("--camera-id", default="CAM_ENTRY_01")

    p.add_argument("--model", default="yolov8n.pt", help="Ultralytics model name or path.")
    p.add_argument("--tracker", default="bytetrack.yaml", help="Ultralytics tracker config name/path.")
    p.add_argument("--conf", type=float, default=0.10, help="Detection confidence threshold (keep low for occlusion).")
    p.add_argument("--imgsz", type=int, default=640)
    p.add_argument("--device", default="auto", help="auto|cpu|0|0,1 (passed to ultralytics).")
    p.add_argument("--class-id", type=int, default=0, help="Person class id (COCO=0).")
    p.add_argument("--max-videos", type=int, default=None, help="Limit number of videos for quick runs.")
    p.add_argument("--stride", type=int, default=15, help="Frame stride for skipping (e.g. 15 = 1fps).")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    input_dir = Path(args.input).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    try:
        summary = run_detection(
            input_dir=input_dir,
            output_path=output_path,
            store_id=args.store_id,
            camera_id=args.camera_id,
            model_name=args.model,
            conf=float(args.conf),
            imgsz=int(args.imgsz),
            device=str(args.device),
            tracker_cfg=str(args.tracker),
            class_id=int(args.class_id) if args.class_id is not None else 0,
            max_videos=args.max_videos,
            stride=int(args.stride),
        )
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 2

    print(json.dumps({"ok": True, "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

