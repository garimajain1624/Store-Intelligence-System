# Store Intelligence API

This repo contains a containerized REST API for “offline store intelligence”: ingest structured behavioural events and expose real-time store metrics (conversion, dwell, queue/abandonment, funnels, heatmap, and operational anomaly hints).

## Quickstart (5 commands)

1. Start the API:
```bash
docker compose up --build
```

2. Convert an event JSONL stream into an ingestable JSON payload:
```bash
python pipeline/emit.py ^
  --input "D:\project\sample_eventsbe42122.jsonl" ^
  --output "./out/events.json"
```

3. Wait for the server to become reachable:
```bash
curl -s http://localhost:8000/health
```

4. Ingest events:
```bash
curl -s -X POST http://localhost:8000/events/ingest ^
  -H "Content-Type: application/json" ^
  -d "@./out/events.json"
```

5. Query metrics:
```bash
curl -s http://localhost:8000/stores/STORE_BLR_002/metrics
```

5. Query more endpoints:
```bash
curl -s http://localhost:8000/stores/STORE_BLR_002/funnel
```

## Detection pipeline integration (how to produce events)

The system includes a video-to-event pipeline to turn raw CCTV clips into schema-compliant events:

1. **Orchestrate the full pipeline** (Detection → Re-ID matching → Event emission with POS correlation):
   ```bash
   bash pipeline/run.sh "data/Store_1/Store 1" "ST1008" "./out/events.json"
   ```

2. **Ingest the generated event array** into the database:
   ```bash
   curl -s -X POST http://localhost:8000/events/ingest \
     -H "Content-Type: application/json" \
     -d "@./out/events.json"
   ```

3. **Check the Live Analytics Dashboard**:
   Open a browser and navigate to:
   * **http://localhost:8000/** or **http://localhost:8000/dashboard**
   The metrics, funnel drops, zone heatmap scores, health signals, and event feed will stream live in real-time as events are ingested.

## Notes

- The database is SQLite and is persisted in the `./data` volume through Docker.
- `POST /events/ingest` is idempotent using `event_id`. If `event_id` is missing, the API derives a deterministic UUID from the payload so repeated calls remain safe.
- Disposing database engines in SQLAlchemy handles Windows-specific file locking safely during test tear-downs.
- Stride optimization is default (stride 15) to speed up video processing by 15x.


