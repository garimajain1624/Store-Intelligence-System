# Choices

This document lists three intentionally chosen implementation decisions and the reasoning behind them.

## 1) Detection and Re-ID Model Choice

For person detection and initial tracking, we chose **YOLOv8 + ByteTrack** (via the `ultralytics` package). YOLOv8 provides high accuracy on person detection, and ByteTrack handles track persistence under overlap.

For multi-camera Re-ID (linking visitor tracks across cameras) and staff detection:
* **Options considered**: 
  1. A deep learning Re-ID model (e.g., OSNet, ResNet18 feature extractor).
  2. A rule-based spatial-temporal + color histogram heuristic.
* **What AI suggested**: The LLM suggested using torchvision's pre-trained ResNet18 model to extract 512-dimensional visual embedding vectors for each tracked box crop, and then compute cosine similarity.
* **What was chosen and why**: We chose a **2D Hue-Saturation (HSV space) color histogram correlation matcher combined with temporal gating**. 
  * *Why we overrode the AI suggestion*: Downloading deep learning weights from GitHub or PyTorch Hub during automated testing runs is a significant production risk. If the evaluation environment lacks internet access, the pipeline will crash.
  * *Accuracy & Performance*: The 2D HSV color histogram acts as a reliable fingerprint of visitor clothing colors, which is highly distinctive for 20-minute video slots with a small group of visitors. It runs instantly on CPU without requiring PyTorch weight downloads, making the script highly robust.
  * *Staff Exclusion*: We classify visitor tracks as staff at the individual track level. A track is flagged as staff if it spans $> 100\text{ seconds}$ on non-entry cameras (or $> 140\text{ seconds}$ on entry cameras). Staff tracks are segregated in the Re-ID matching stage to prevent session over-propagation. This heuristic is independent of specific uniform colors.

## 2) Event Schema Design Rationale

We designed a unified event table in SQLite with two principles:
1. **Analytics-first canonical fields**: Explicit columns for `store_id`, `visitor_id`, `event_type`, `timestamp`, `zone_id`, `is_staff`, `confidence`, and `dwell_ms`. This allows metrics queries to run instantly using standard SQL indexes.
2. **Raw preservation**: Every event stores the original input object inside a `raw` JSON column. This ensures that any schema changes or custom client payloads do not break the API.

Idempotency is guaranteed by creating a unique database constraint on `event_id`. When a payload doesn't contain a UUID `event_id`, the ingestion router derives a stable UUID using `uuid.uuid5` of the serialized payload, ensuring that repeating the same request never duplicates rows.

## 3) API & Dashboard Architecture Choice

We chose a single-container **FastAPI + SQLAlchemy + SQLite** architecture serving both the API and the UI:
* **FastAPI**: Provides fast, asynchronous routing.
* **SQLite**: Perfect for the offline store requirement, with no external database service overhead. We enabled WAL (Write-Ahead Logging) and busy timeout pragmas to handle high-concurrency event writes without database lockups.
* **FastAPI-Served HTML + SSE Dashboard**: Instead of running a separate frontend container or using Streamlit (which runs on a separate port and requires manual launching), we served a Single Page Application (SPA) dashboard directly from FastAPI's root endpoint `/`. We implemented a Server-Sent Events (SSE) stream at `/dashboard/stream` that queries the database and streams live KPI card metrics, funnel updates, active anomalies, and a scrolling event log in real-time as events are ingested.
