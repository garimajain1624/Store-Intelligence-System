import asyncio
import json
from datetime import datetime, timezone
from typing import Generator
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IngestedEvent
from app.routers.analytics import store_metrics, store_funnel, store_heatmap, store_anomalies
from app.routers.health import health

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Apex Retail - Store Intelligence Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 25, 40, 0.65);
            --card-border: rgba(255, 255, 255, 0.08);
            --primary: #6366f1;
            --primary-glow: rgba(99, 102, 241, 0.15);
            --secondary: #ec4899;
            --success: #10b981;
            --warning: #f59e0b;
            --critical: #ef4444;
            --text: #f3f4f6;
            --text-muted: #9ca3af;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
            font-family: 'Outfit', sans-serif;
        }

        body {
            background-color: var(--bg-color);
            color: var(--text);
            overflow-x: hidden;
            background-image: 
                radial-gradient(at 10% 20%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
                radial-gradient(at 90% 80%, rgba(236, 72, 153, 0.1) 0px, transparent 50%);
            background-attachment: fixed;
            min-height: 100vh;
        }

        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 2rem;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 2rem;
            padding-bottom: 1rem;
            border-bottom: 1px solid var(--card-border);
        }

        .logo-section h1 {
            font-weight: 800;
            font-size: 2rem;
            background: linear-gradient(to right, #a5b4fc, #f472b6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            letter-spacing: -0.5px;
        }

        .logo-section p {
            color: var(--text-muted);
            font-size: 0.9rem;
            margin-top: 0.25rem;
        }

        .status-badge {
            display: flex;
            align-items: center;
            gap: 0.5rem;
            background: rgba(16, 185, 129, 0.1);
            border: 1px solid rgba(16, 185, 129, 0.2);
            padding: 0.5rem 1rem;
            border-radius: 9999px;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--success);
            box-shadow: 0 0 15px rgba(16, 185, 129, 0.1);
        }

        .status-dot {
            width: 8px;
            height: 8px;
            background-color: var(--success);
            border-radius: 50%;
            animation: pulse 1.5s infinite;
        }

        .status-badge.degraded {
            background: rgba(245, 158, 11, 0.1);
            border: 1px solid rgba(245, 158, 11, 0.2);
            color: var(--warning);
        }
        .status-badge.degraded .status-dot {
            background-color: var(--warning);
        }

        @keyframes pulse {
            0% { transform: scale(0.9); opacity: 0.6; }
            50% { transform: scale(1.2); opacity: 1; }
            100% { transform: scale(0.9); opacity: 0.6; }
        }

        /* Dashboard Grid */
        .dashboard-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 16px;
            padding: 1.5rem;
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
            transition: transform 0.3s ease, border-color 0.3s ease;
        }

        .card:hover {
            transform: translateY(-5px);
            border-color: rgba(255, 255, 255, 0.15);
        }

        /* Metric Cards */
        .metric-card {
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .metric-title {
            color: var(--text-muted);
            font-size: 0.9rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 1rem;
        }

        .metric-value {
            font-size: 2.5rem;
            font-weight: 800;
            margin-bottom: 0.5rem;
            background: linear-gradient(135deg, #ffffff 0%, #e5e7eb 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .metric-footer {
            font-size: 0.8rem;
            color: var(--text-muted);
        }

        .sub-metric {
            font-weight: 400;
        }

        .sub-metric strong {
            color: var(--text);
            font-weight: 600;
        }

        .sub-divider {
            margin: 0 0.3rem;
            color: rgba(255, 255, 255, 0.15);
        }

        /* Large Sections Grid */
        .main-grid {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 1.5rem;
            margin-bottom: 2rem;
        }

        .section-title {
            font-size: 1.25rem;
            font-weight: 600;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.5rem;
        }

        /* Funnel chart */
        .funnel-container {
            display: flex;
            flex-direction: column;
            gap: 1rem;
            margin-top: 1rem;
        }

        .funnel-stage {
            display: flex;
            align-items: center;
            position: relative;
        }

        .funnel-label {
            width: 140px;
            font-size: 0.9rem;
            font-weight: 600;
        }

        .funnel-bar-wrapper {
            flex-grow: 1;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 8px;
            height: 32px;
            overflow: hidden;
            position: relative;
            border: 1px solid rgba(255, 255, 255, 0.05);
        }

        .funnel-bar {
            height: 100%;
            background: linear-gradient(90deg, var(--primary) 0%, var(--secondary) 100%);
            border-radius: 8px;
            transition: width 1s ease-in-out;
            width: 0%;
        }

        .funnel-value {
            position: absolute;
            right: 12px;
            top: 50%;
            transform: translateY(-50%);
            font-size: 0.85rem;
            font-weight: 800;
        }

        .dropoff-badge {
            background: rgba(239, 68, 68, 0.1);
            border: 1px solid rgba(239, 68, 68, 0.2);
            color: var(--critical);
            padding: 0.2rem 0.5rem;
            border-radius: 4px;
            font-size: 0.75rem;
            font-weight: 600;
            margin-left: 1rem;
            width: 80px;
            text-align: center;
        }

        /* Heatmap / Zone Dwell List */
        .zone-dwell-list {
            display: flex;
            flex-direction: column;
            gap: 1rem;
        }

        .zone-item {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 0.75rem;
            border-radius: 12px;
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.03);
        }

        .zone-info h4 {
            font-weight: 600;
            font-size: 0.95rem;
        }

        .zone-info p {
            color: var(--text-muted);
            font-size: 0.8rem;
        }

        .zone-score {
            font-size: 1.1rem;
            font-weight: 800;
            color: var(--primary);
        }

        /* Scrolling events log */
        .log-container {
            height: 300px;
            overflow-y: auto;
            padding-right: 0.5rem;
            font-family: monospace;
            font-size: 0.85rem;
            display: flex;
            flex-direction: column;
            gap: 0.5rem;
        }

        .log-container::-webkit-scrollbar {
            width: 4px;
        }

        .log-container::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 2px;
        }

        .log-row {
            padding: 0.5rem 0.75rem;
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.01);
            border: 1px solid rgba(255, 255, 255, 0.03);
            display: flex;
            justify-content: space-between;
            align-items: center;
            animation: slideIn 0.3s ease-out;
        }

        @keyframes slideIn {
            from { transform: translateX(10px); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }

        .log-time {
            color: var(--text-muted);
        }

        .log-badge {
            padding: 0.15rem 0.4rem;
            border-radius: 4px;
            font-weight: bold;
            font-size: 0.75rem;
        }

        .badge-entry { background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-exit { background: rgba(239, 68, 68, 0.15); color: var(--critical); border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-zone_enter { background: rgba(99, 102, 241, 0.15); color: var(--primary); border: 1px solid rgba(99, 102, 241, 0.3); }
        .badge-zone_exit { background: rgba(236, 72, 153, 0.15); color: var(--secondary); border: 1px solid rgba(236, 72, 153, 0.3); }
        .badge-billing_queue_join { background: rgba(245, 158, 11, 0.15); color: var(--warning); border: 1px solid rgba(245, 158, 11, 0.3); }
        .badge-billing_queue_abandon { background: rgba(239, 68, 68, 0.15); color: var(--critical); border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-reentry { background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); }

        /* Anomalies Panel */
        .anomaly-list {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
        }

        .anomaly-card {
            padding: 1rem;
            border-radius: 12px;
            background: rgba(239, 68, 68, 0.05);
            border: 1px solid rgba(239, 68, 68, 0.15);
            display: flex;
            flex-direction: column;
            gap: 0.25rem;
        }

        .anomaly-card.warn {
            background: rgba(245, 158, 11, 0.05);
            border: 1px solid rgba(245, 158, 11, 0.15);
        }

        .anomaly-card.info {
            background: rgba(99, 102, 241, 0.05);
            border: 1px solid rgba(99, 102, 241, 0.15);
        }

        .anomaly-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .anomaly-type {
            font-weight: bold;
            font-size: 0.9rem;
        }

        .anomaly-severity {
            font-size: 0.7rem;
            font-weight: 800;
            padding: 0.1rem 0.3rem;
            border-radius: 4px;
        }
        .severity-CRITICAL { background: var(--critical); color: white; }
        .severity-WARN { background: var(--warning); color: black; }
        .severity-INFO { background: var(--primary); color: white; }

        .anomaly-action {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 0.25rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-section">
                <h1>Store Intelligence Center</h1>
                <p>Real-time computer vision store analytics dashboard</p>
            </div>
            <div id="health-badge" class="status-badge">
                <div class="status-dot"></div>
                <span id="health-status">SYSTEM OK</span>
            </div>
        </header>

        <!-- KPI Metrics Grid -->
        <div class="dashboard-grid">
            <div class="card metric-card">
                <div class="metric-title">Unique Visitors</div>
                <div class="metric-value" id="unique-visitors">0</div>
                <div class="metric-footer">
                    <span class="sub-metric">Active Inside: <strong id="active-visitors">0</strong></span>
                    <span class="sub-divider">•</span>
                    <span class="sub-metric">Staff Excluded: <strong id="staff-excluded">0</strong></span>
                </div>
            </div>
            <div class="card metric-card">
                <div class="metric-title">Conversion Rate</div>
                <div class="metric-value" id="conversion-rate">0.0%</div>
                <div class="metric-footer">Queue to POS correlation</div>
            </div>
            <div class="card metric-card">
                <div class="metric-title">Max Queue Depth</div>
                <div class="metric-value" id="queue-depth">0</div>
                <div class="metric-footer">
                    <span class="sub-metric">Current Queue: <strong id="current-queue">0</strong></span>
                </div>
            </div>
            <div class="card metric-card">
                <div class="metric-title">Abandonment Rate</div>
                <div class="metric-value" id="abandonment-rate">0.0%</div>
                <div class="metric-footer">Billing queue exits</div>
            </div>
        </div>

        <div class="main-grid">
            <!-- Left Panel: Funnel and Live Feed -->
            <div style="display: flex; flex-direction: column; gap: 1.5rem;">
                <div class="card">
                    <div class="section-title">
                        <span>Customer Conversion Funnel</span>
                    </div>
                    <div class="funnel-container">
                        <div class="funnel-stage">
                            <div class="funnel-label">Store Entry</div>
                            <div class="funnel-bar-wrapper">
                                <div class="funnel-bar" id="funnel-entry-bar"></div>
                                <div class="funnel-value" id="funnel-entry-val">0</div>
                            </div>
                            <div class="dropoff-badge" style="visibility: hidden;">-</div>
                        </div>
                        <div class="funnel-stage">
                            <div class="funnel-label">Zone Visit</div>
                            <div class="funnel-bar-wrapper">
                                <div class="funnel-bar" id="funnel-zone-bar"></div>
                                <div class="funnel-value" id="funnel-zone-val">0</div>
                            </div>
                            <div class="dropoff-badge" id="dropoff-zone">-0%</div>
                        </div>
                        <div class="funnel-stage">
                            <div class="funnel-label">Billing Queue</div>
                            <div class="funnel-bar-wrapper">
                                <div class="funnel-bar" id="funnel-billing-bar"></div>
                                <div class="funnel-value" id="funnel-billing-val">0</div>
                            </div>
                            <div class="dropoff-badge" id="dropoff-billing">-0%</div>
                        </div>
                        <div class="funnel-stage">
                            <div class="funnel-label">Purchase</div>
                            <div class="funnel-bar-wrapper">
                                <div class="funnel-bar" id="funnel-purchase-bar"></div>
                                <div class="funnel-value" id="funnel-purchase-val">0</div>
                            </div>
                            <div class="dropoff-badge" id="dropoff-purchase">-0%</div>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <div class="section-title">Live Event Stream</div>
                    <div class="log-container" id="events-log">
                        <div style="color: var(--text-muted); text-align: center; margin-top: 4rem;">Waiting for events...</div>
                    </div>
                </div>
            </div>

            <!-- Right Panel: Heatmap and Operational Anomalies -->
            <div style="display: flex; flex-direction: column; gap: 1.5rem;">
                <div class="card">
                    <div class="section-title">Zone Heatmap Frequency</div>
                    <div class="zone-dwell-list" id="zones-list">
                        <div style="color: var(--text-muted); text-align: center; padding: 2rem;">No zone visits recorded</div>
                    </div>
                </div>

                <div class="card">
                    <div class="section-title">Operational Anomalies</div>
                    <div class="anomaly-list" id="anomalies-list">
                        <div style="color: var(--text-muted); text-align: center; padding: 2rem;">No active anomalies detected</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const eventSource = new EventSource('/dashboard/stream');
        const eventsLog = document.getElementById('events-log');
        let isFirstLog = true;

        eventSource.onmessage = function(event) {
            const data = JSON.parse(event.data);

            // Update Health Status
            const healthBadge = document.getElementById('health-badge');
            const healthStatus = document.getElementById('health-status');
            if (data.health.status === 'ok') {
                healthBadge.className = 'status-badge';
                healthStatus.innerText = 'SYSTEM ONLINE';
            } else {
                healthBadge.className = 'status-badge degraded';
                healthStatus.innerText = 'FEED DELAYED';
            }

            // Update KPI Cards
            if (data.metrics) {
                document.getElementById('unique-visitors').innerText = data.metrics.unique_visitors;
                document.getElementById('active-visitors').innerText = data.metrics.active_visitors || 0;
                document.getElementById('staff-excluded').innerText = data.metrics.staff_excluded || 0;
                document.getElementById('conversion-rate').innerText = (data.metrics.conversion_rate * 100).toFixed(1) + '%';
                document.getElementById('queue-depth').innerText = data.metrics.queue_depth;
                document.getElementById('current-queue').innerText = data.metrics.current_queue || 0;
                document.getElementById('abandonment-rate').innerText = (data.metrics.abandonment_rate * 100).toFixed(1) + '%';
            }

            // Update Funnel
            if (data.funnel) {
                const stages = data.funnel.stages;
                const dropoffs = data.funnel.drop_off_percent;
                const maxVal = Math.max(stages.entry, 1);

                document.getElementById('funnel-entry-val').innerText = stages.entry;
                document.getElementById('funnel-entry-bar').style.width = '100%';

                document.getElementById('funnel-zone-val').innerText = stages.zone_visit;
                document.getElementById('funnel-zone-bar').style.width = (stages.zone_visit / maxVal * 100) + '%';
                document.getElementById('dropoff-zone').innerText = '-' + dropoffs.entry_to_zone_visit.toFixed(0) + '%';

                document.getElementById('funnel-billing-val').innerText = stages.billing_queue;
                document.getElementById('funnel-billing-bar').style.width = (stages.billing_queue / maxVal * 100) + '%';
                document.getElementById('dropoff-billing').innerText = '-' + dropoffs.zone_to_billing_queue.toFixed(0) + '%';

                document.getElementById('funnel-purchase-val').innerText = stages.purchase;
                document.getElementById('funnel-purchase-bar').style.width = (stages.purchase / maxVal * 100) + '%';
                document.getElementById('dropoff-purchase').innerText = '-' + dropoffs.billing_queue_to_purchase.toFixed(0) + '%';
            }

            // Update Zones list
            if (data.heatmap && data.heatmap.zones) {
                const list = document.getElementById('zones-list');
                if (data.heatmap.zones.length === 0) {
                    list.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem;">No zone visits recorded</div>';
                } else {
                    list.innerHTML = data.heatmap.zones.map(z => `
                        <div class="zone-item">
                            <div class="zone-info">
                                <h4>${z.zone_id}</h4>
                                <p>${z.visits} visits • ${(z.avg_dwell_ms / 1000).toFixed(1)}s avg dwell</p>
                            </div>
                            <div class="zone-score">${z.score_0_100.toFixed(0)}</div>
                        </div>
                    `).join('');
                }
            }

            // Update Anomalies
            if (data.anomalies && data.anomalies.anomalies) {
                const alist = document.getElementById('anomalies-list');
                if (data.anomalies.anomalies.length === 0) {
                    alist.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 2rem;">No active anomalies detected</div>';
                } else {
                    alist.innerHTML = data.anomalies.anomalies.map(a => `
                        <div class="anomaly-card ${a.severity.toLowerCase()}">
                            <div class="anomaly-header">
                                <span class="anomaly-type">${a.anomaly_type.replace('_', ' ')}</span>
                                <span class="anomaly-severity severity-${a.severity}">${a.severity}</span>
                            </div>
                            <div class="anomaly-action">${a.suggested_action}</div>
                        </div>
                    `).join('');
                }
            }

            // Log Live Events
            if (data.live_events && data.live_events.length > 0) {
                if (isFirstLog) {
                    eventsLog.innerHTML = '';
                    isFirstLog = false;
                }
                data.live_events.forEach(e => {
                    const row = document.createElement('div');
                    row.className = 'log-row';
                    const timeStr = e.timestamp.split('T')[1].substring(0, 8);
                    row.innerHTML = `
                        <span class="log-time">[${timeStr}]</span>
                        <span>Visitor <strong>${e.visitor_id}</strong></span>
                        <span class="log-badge badge-${e.event_type.toLowerCase()}">${e.event_type}</span>
                        <span style="color: var(--text-muted)">${e.zone_id || ''}</span>
                    `;
                    eventsLog.insertBefore(row, eventsLog.firstChild);
                    if (eventsLog.childNodes.length > 30) {
                        eventsLog.removeChild(eventsLog.lastChild);
                    }
                });
            }
        };

        eventSource.onerror = function(err) {
            console.error("EventSource failed:", err);
        };
    </script>
</body>
</html>
"""

@router.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    return DASHBOARD_HTML

@router.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard_alias():
    return DASHBOARD_HTML

@router.get("/dashboard/stream")
async def stream_dashboard(request: Request, db: Session = Depends(get_db)):
    async def event_generator() -> Generator[str, None, None]:
        last_event_count = 0
        
        while True:
            if await request.is_disconnected():
                break

            # Find active stores
            store_ids = db.execute(select(IngestedEvent.store_id).distinct()).scalars().all()
            
            if not store_ids:
                # Mock or empty data if no events ingested yet
                empty_data = {
                    "health": {"status": "ok"},
                    "metrics": {"unique_visitors": 0, "conversion_rate": 0, "queue_depth": 0, "abandonment_rate": 0},
                    "funnel": {"stages": {"entry": 0, "zone_visit": 0, "billing_queue": 0, "purchase": 0}, "drop_off_percent": {"entry_to_zone_visit": 0, "zone_to_billing_queue": 0, "billing_queue_to_purchase": 0}},
                    "heatmap": {"zones": []},
                    "anomalies": {"anomalies": []},
                    "live_events": []
                }
                yield f"data: {json.dumps(empty_data)}\n\n"
                await asyncio.sleep(2)
                continue

            # We display details for the first store by default
            store_id = store_ids[0]
            
            try:
                # Query metrics from existing endpoints functions directly
                metrics_res = store_metrics(store_id, request, db)
                funnel_res = store_funnel(store_id, request, db)
                heatmap_res = store_heatmap(store_id, request, db)
                anomalies_res = store_anomalies(store_id, request, db)
                health_res = health(request, db)
                
                # Fetch new events to show in the streaming log
                total_events = db.execute(select(IngestedEvent)).scalars().all()
                total_event_count = len(total_events)
                
                new_events_list = []
                if total_event_count > last_event_count:
                    # Sort by id and get newest
                    newest = sorted(total_events, key=lambda x: x.id)[last_event_count:]
                    new_events_list = [
                        {
                            "timestamp": e.timestamp,
                            "visitor_id": e.visitor_id,
                            "event_type": e.event_type,
                            "zone_id": e.zone_id
                        } for e in newest
                    ]
                    last_event_count = total_event_count

                stream_data = {
                    "health": health_res.model_dump(),
                    "metrics": metrics_res.model_dump(),
                    "funnel": funnel_res.model_dump(),
                    "heatmap": heatmap_res.model_dump(),
                    "anomalies": anomalies_res.model_dump(),
                    "live_events": new_events_list
                }
                
                yield f"data: {json.dumps(stream_data)}\n\n"
            except Exception as e:
                # Graceful stream fallback on query exceptions
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(2)

    # Return as an SSE streaming response
    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")
