import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple
import cv2
import numpy as np


def compute_hsv_histogram(image: np.ndarray) -> np.ndarray:
    """Compute a normalized 3D HSV histogram of the image (4 Hue bins, 4 Saturation bins, 4 Value bins = 64 bins)."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], None, [4, 4, 4], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist, hist, alpha=1, beta=0, norm_type=cv2.NORM_L2)
    return hist.flatten()


def get_track_crop(video_path: str, frame_idx: int, bbox: Tuple[float, float, float, float]) -> np.ndarray:
    """Extract a crop of the person bounding box from the video frame (fallback)."""
    cap = cv2.VideoCapture(video_path)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        return None

    h, w, _ = frame.shape
    x1, y1, x2, y2 = bbox
    # Clamp coordinates to frame dimensions
    x1 = max(0, int(x1))
    y1 = max(0, int(y1))
    x2 = min(w, int(x2))
    y2 = min(h, int(y2))

    if x2 <= x1 or y2 <= y1:
        return None

    crop = frame[y1:y2, x1:x2]
    return crop


def compute_iou(box1: Tuple[float, float, float, float], box2: Tuple[float, float, float, float]) -> float:
    """Compute Intersection over Union of two bounding boxes."""
    x1_1, y1_1, x2_1, y2_1 = box1
    x1_2, y1_2, x2_2, y2_2 = box2
    
    xi1 = max(x1_1, x1_2)
    yi1 = max(y1_1, y1_2)
    xi2 = min(x2_1, x2_2)
    yi2 = min(y2_1, y2_2)
    
    inter_area = max(0.0, xi2 - xi1) * max(0.0, yi2 - yi1)
    box1_area = (x2_1 - x1_1) * (y2_1 - y1_1)
    box2_area = (x2_2 - x1_2) * (y2_2 - y1_2)
    union_area = box1_area + box2_area - inter_area
    
    return inter_area / union_area if union_area > 0 else 0.0


def track_locally(rows: List[Dict[str, Any]], fps: float) -> List[List[Dict[str, Any]]]:
    """Group rows into spatially and temporally continuous tracks locally per video file."""
    if not rows:
        return []
    
    # Sort chronological
    rows.sort(key=lambda r: r["frame_index"])
    
    # Each entry in active_tracks is: [list_of_rows, last_bbox, last_frame_idx]
    active_tracks: List[Tuple[List[Dict[str, Any]], Tuple[float, float, float, float], int]] = []
    
    # Maximum frame gap (e.g. 5 seconds of occlusion / track loss)
    max_frame_gap = int(fps * 5.0) if fps > 0.0 else 75
    
    for row in rows:
        bbox = tuple(row["bbox_xyxy"])
        frame_idx = row["frame_index"]
        
        best_idx = -1
        best_match_val = 0.0
        
        for i, (track_rows, last_bbox, last_frame) in enumerate(active_tracks):
            frame_gap = frame_idx - last_frame
            if frame_gap > max_frame_gap or frame_gap < 0:
                continue
                
            iou = compute_iou(bbox, last_bbox)
            if iou > 0.15:
                if iou > best_match_val:
                    best_match_val = iou
                    best_idx = i
            else:
                # Fallback: check center distance for fast moving objects (if gap is small, <= 2 seconds)
                if frame_gap <= (fps * 2.0 if fps > 0.0 else 30):
                    c1 = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
                    c2 = ((last_bbox[0] + last_bbox[2]) / 2, (last_bbox[1] + last_bbox[3]) / 2)
                    dist = np.sqrt((c1[0] - c2[0])**2 + (c1[1] - c2[1])**2)
                    # Normalize distance relative to box size (threshold: half box diagonal)
                    box_diag = np.sqrt((bbox[2]-bbox[0])**2 + (bbox[3]-bbox[1])**2)
                    if dist < box_diag * 0.8:
                        pseudo_iou = 0.14 - (dist / (box_diag * 10.0))
                        if pseudo_iou > best_match_val:
                            best_match_val = pseudo_iou
                            best_idx = i
                            
        if best_idx != -1:
            active_tracks[best_idx][0].append(row)
            active_tracks[best_idx] = (active_tracks[best_idx][0], bbox, frame_idx)
        else:
            active_tracks.append(([row], bbox, frame_idx))
            
    return [t[0] for t in active_tracks]


def process_tracks(
    *,
    input_dir: Path,
    tracks_path: Path,
    output_path: Path,
) -> None:
    # 1. Read all track rows grouped by file
    rows_by_file: Dict[str, List[Dict[str, Any]]] = {}
    if not tracks_path.exists():
        raise FileNotFoundError(f"Tracks file not found: {tracks_path}")

    with tracks_path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            rows_by_file.setdefault(row["source_file"], []).append(row)

    # 2. Run local tracking per video file to generate stable local tracks
    local_tracks: List[List[Dict[str, Any]]] = []
    for source_file, file_rows in rows_by_file.items():
        # Search for file inside input_dir or recursively in data
        video_path = input_dir / source_file
        if not video_path.exists():
            for root, dirs, files in os.walk("data"):
                if source_file in files:
                    video_path = Path(root) / source_file
                    break
        cap = cv2.VideoCapture(str(video_path))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        cap.release()
        
        tracks = track_locally(file_rows, fps)
        local_tracks.extend(tracks)

    # 3. Pre-group frame indices per video file to minimize VideoCapture openings
    frames_by_file: Dict[str, set] = {}
    for loc_idx, rows in enumerate(local_tracks):
        source_file = rows[0]["source_file"]
        num_rows = len(rows)
        sample_indices = np.linspace(0, num_rows - 1, min(5, num_rows), dtype=int)
        for idx in sample_indices:
            frames_by_file.setdefault(source_file, set()).add(rows[idx]["frame_index"])

    loaded_frames: Dict[str, Dict[int, np.ndarray]] = {}
    for sf, f_indices in frames_by_file.items():
        video_path = input_dir / sf
        if not video_path.exists():
            for root, dirs, files in os.walk("data"):
                if sf in files:
                    video_path = Path(root) / sf
                    break
        if not video_path or not video_path.exists():
            continue

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            continue
        loaded_frames[sf] = {}
        for f_idx in sorted(f_indices):
            cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
            ret, frame = cap.read()
            if ret and frame is not None:
                loaded_frames[sf][f_idx] = frame.copy()
        cap.release()

    # 4. Extract representative 3D HSV color histograms for each local track
    track_profiles: List[Dict[str, Any]] = []
    print(f"Profiling {len(local_tracks)} locally stable tracks...")

    for loc_idx, rows in enumerate(local_tracks):
        rows.sort(key=lambda r: r["frame_index"])
        source_file = rows[0]["source_file"]
        local_track_id = f"LOC_{loc_idx:04d}"
        
        hists = []
        num_rows = len(rows)
        sample_indices = np.linspace(0, num_rows - 1, min(5, num_rows), dtype=int)
        
        for idx in sample_indices:
            f_idx = rows[idx]["frame_index"]
            frame = loaded_frames.get(source_file, {}).get(f_idx)
            if frame is not None:
                bbox = rows[idx]["bbox_xyxy"]
                h, w, _ = frame.shape
                x1 = max(0, int(bbox[0]))
                y1 = max(0, int(bbox[1]))
                x2 = min(w, int(bbox[2]))
                y2 = min(h, int(bbox[3]))
                if x2 > x1 and y2 > y1:
                    crop = frame[y1:y2, x1:x2]
                    ch, cw, _ = crop.shape
                    # Focus on torso: central 60% horizontally, vertical from 10% to 60%
                    torso = crop[int(ch * 0.1):int(ch * 0.6), int(cw * 0.2):int(cw * 0.8)]
                    if torso.size > 0:
                        hists.append(compute_hsv_histogram(torso))

        if hists:
            avg_hist = np.mean(hists, axis=0)
        else:
            avg_hist = np.zeros(64)

        start_time = rows[0]["timestamp_ms"]
        end_time = rows[-1]["timestamp_ms"]
        camera_id = rows[0]["camera_id"]
        store_id = rows[0]["store_id"]
        duration_ms = end_time - start_time

        is_entry = "entry" in camera_id.lower()
        is_staff_track = False
        if duration_ms > 100000 and not is_entry:
            is_staff_track = True
        elif duration_ms > 140000 and is_entry:
            is_staff_track = True

        track_profiles.append({
            "store_id": store_id,
            "camera_id": camera_id,
            "source_file": source_file,
            "track_id": local_track_id,
            "start_time": start_time,
            "end_time": end_time,
            "duration_ms": duration_ms,
            "hist": avg_hist,
            "rows": rows,
            "is_staff": is_staff_track,
        })

    # Sort profiles by start time
    track_profiles.sort(key=lambda p: p["start_time"])

    # 5. Match tracks across cameras using color similarity & time sequence constraints
    visitor_counter = 0
    assigned_visitor_ids: Dict[Tuple[str, str], str] = {}
    
    # Active visitor sessions
    sessions: List[Dict[str, Any]] = []

    # Configure matching thresholds
    color_gate = 0.25
    score_threshold = 0.35

    for i, profile in enumerate(track_profiles):
        cam_id = profile["camera_id"]
        start_time = profile["start_time"]
        end_time = profile["end_time"]
        hist = profile["hist"]
        is_staff = profile["is_staff"]
        
        best_session_idx = -1
        best_score = -999.0
        
        for idx, s in enumerate(sessions):
            # Strict matching gate: staff matching staff, customer matching customer
            if s["is_staff"] != is_staff:
                continue
                
            # Calculate color similarity
            color_sim = float(np.dot(hist, s["hist"]))
            
            # Gating check
            if color_sim < color_gate:
                continue
                
            time_gap = start_time - s["last_end_time"]
            score = color_sim
            
            if cam_id == s["last_camera"]:
                # Re-appearance in the same camera
                if 0 <= time_gap <= 45000:  # within 45 seconds
                    score += 0.35
                elif time_gap < 0:  # overlapping in same camera
                    continue
                else:
                    score -= 0.30
            else:
                # Transition between different cameras
                is_compatible = False
                if "entry" in s["last_camera"].lower() and "zone" in cam_id.lower():
                    is_compatible = True
                elif "zone" in s["last_camera"].lower() and "billing" in cam_id.lower():
                    is_compatible = True
                elif "zone" in s["last_camera"].lower() and "zone" in cam_id.lower():
                    is_compatible = True
                elif "billing" in s["last_camera"].lower() and "entry" in cam_id.lower():
                    is_compatible = True
                elif "entry" in s["last_camera"].lower() and "billing" in cam_id.lower():
                    is_compatible = True
                    
                if is_compatible:
                    if 0 <= time_gap <= 600000:  # within 10 minutes
                        score += 0.25
                        score -= (time_gap / 600000.0) * 0.15
                    elif time_gap < 0:
                        # Allow small overlap across cameras
                        if abs(time_gap) <= 20000:
                            score += 0.15
                        else:
                            continue
                else:
                    if 0 <= time_gap <= 600000:
                        score -= 0.10
                        
            if score > best_score:
                best_score = score
                best_session_idx = idx

        if best_session_idx != -1 and best_score >= score_threshold:
            s = sessions[best_session_idx]
            vid = s["visitor_id"]
            assigned_visitor_ids[(profile["source_file"], profile["track_id"])] = vid
            
            # Update session details
            s["last_camera"] = cam_id
            s["last_end_time"] = max(s["last_end_time"], end_time)
            s["cameras"].add(cam_id)
            s["duration"] += profile["duration_ms"]
            s["hist"] = s["hist"] * 0.7 + hist * 0.3
            s["tracks"].append(profile)
        else:
            visitor_counter += 1
            vid = f"VIS_{visitor_counter:04d}"
            assigned_visitor_ids[(profile["source_file"], profile["track_id"])] = vid
            
            sessions.append({
                "visitor_id": vid,
                "last_camera": cam_id,
                "last_end_time": end_time,
                "hist": hist,
                "cameras": {cam_id},
                "duration": profile["duration_ms"],
                "tracks": [profile],
                "is_staff": is_staff
            })

    # 6. Determine staff members
    staff_visitor_ids = {s["visitor_id"] for s in sessions if s["is_staff"]}

    # 7. Write the final matched visitor tracks
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for profile in track_profiles:
            key = (profile["source_file"], profile["track_id"])
            vid = assigned_visitor_ids[key]
            is_staff = profile["is_staff"]
            
            for row in profile["rows"]:
                row["visitor_id"] = vid
                row["is_staff"] = is_staff
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    print(f"Tracking complete. Assigned {visitor_counter} unique visitors (Staff: {len(staff_visitor_ids)}). Saved to {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-ID matching and staff detection pipeline.")
    parser.add_argument("--input", required=True, help="Folder containing CCTV clips.")
    parser.add_argument("--tracks", default="out/tracks.jsonl", help="Input tracks JSONL path.")
    parser.add_argument("--output", default="out/visitor_tracks.jsonl", help="Output visitor tracks path.")
    args = parser.parse_args()

    input_dir = Path(args.input).expanduser().resolve()
    tracks_path = Path(args.tracks).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    try:
        process_tracks(input_dir=input_dir, tracks_path=tracks_path, output_path=output_path)
    except Exception as e:
        print(f"Error in tracker: {e}")
        return 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
