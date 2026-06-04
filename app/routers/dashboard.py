import asyncio
import json
from datetime import datetime, timezone
from typing import Generator, List
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import IngestedEvent
from app.routers.analytics import (
    store_metrics, store_funnel, store_heatmap, store_anomalies, _get_camera_status
)
from app.routers.health import health

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Store Intelligence — Purplle Analytics</title>
    <meta name="description" content="Real-time computer vision retail analytics. Track footfall, conversion funnels, billing queues, and zone heatmaps from CCTV cameras.">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:           #080c14;
            --surface:      rgba(16, 24, 40, 0.70);
            --surface-2:    rgba(22, 32, 54, 0.90);
            --border:       rgba(255,255,255,0.07);
            --border-hover: rgba(255,255,255,0.14);
            --primary:      #6366f1;
            --primary-dim:  rgba(99,102,241,0.18);
            --rose:         #f43f5e;
            --amber:        #f59e0b;
            --emerald:      #10b981;
            --sky:          #38bdf8;
            --text:         #f1f5f9;
            --muted:        #64748b;
            --muted2:       #94a3b8;
        }
        *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Outfit', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            overflow-x: hidden;
            background-image:
                radial-gradient(ellipse 80% 50% at 10% 0%,  rgba(99,102,241,0.13) 0, transparent 60%),
                radial-gradient(ellipse 60% 40% at 90% 100%, rgba(244,63,94,0.08) 0, transparent 60%);
            background-attachment: fixed;
        }

        /* ── Layout ──────────────────────────────── */
        .wrap   { max-width: 1440px; margin: 0 auto; padding: 1.5rem 2rem; }

        /* ── Header ──────────────────────────────── */
        header {
            display: flex; justify-content: space-between; align-items: center;
            padding-bottom: 1.25rem; margin-bottom: 1.75rem;
            border-bottom: 1px solid var(--border);
        }
        .logo h1 {
            font-size: 1.7rem; font-weight: 800; letter-spacing: -0.5px;
            background: linear-gradient(120deg, #a5b4fc 0%, #f472b6 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
        }
        .logo p { font-size: 0.8rem; color: var(--muted); margin-top: 3px; }

        .header-right { display: flex; align-items: center; gap: 1rem; }

        /* ── Status Badge ─────────────────────────── */
        .badge {
            display: flex; align-items: center; gap: 0.4rem;
            padding: 0.4rem 0.9rem; border-radius: 9999px; font-size: 0.78rem; font-weight: 700;
            background: rgba(16,185,129,0.12); border: 1px solid rgba(16,185,129,0.25); color: var(--emerald);
        }
        .badge.warn  { background: rgba(245,158,11,0.12); border-color: rgba(245,158,11,0.25); color: var(--amber); }
        .dot { width:7px; height:7px; border-radius:50%; background:currentColor; animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%,100%{transform:scale(0.9);opacity:0.6} 50%{transform:scale(1.2);opacity:1} }

        /* ── Refresh Info ─────────────────────────── */
        .refresh-info {
            display: flex; flex-direction: column; align-items: flex-end; gap: 2px;
            font-size: 0.73rem; color: var(--muted);
        }
        .refresh-info span { color: var(--muted2); font-weight: 600; }

        /* ── Cards ───────────────────────────────── */
        .card {
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 18px; padding: 1.4rem;
            backdrop-filter: blur(20px);
            box-shadow: 0 8px 32px rgba(0,0,0,0.4);
            transition: border-color .25s, transform .25s;
        }
        .card:hover { border-color: var(--border-hover); transform: translateY(-3px); }

        .card-title {
            font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.8px; color: var(--muted); margin-bottom: 1rem;
            display: flex; align-items: center; gap: 0.4rem;
        }
        .card-title .icon { font-size: 1rem; }

        /* ── KPI Grid (top row) ───────────────────── */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 1.25rem; margin-bottom: 1.5rem;
        }

        .kpi-val {
            font-size: 2.4rem; font-weight: 800; line-height: 1;
            background: linear-gradient(135deg,#fff 0%,#cbd5e1 100%);
            -webkit-background-clip: text; -webkit-text-fill-color: transparent;
            margin-bottom: 0.4rem;
        }
        .kpi-sub { font-size: 0.78rem; color: var(--muted); }
        .kpi-sub strong { color: var(--muted2); font-weight: 600; }
        .kpi-sep { margin: 0 0.3rem; color: var(--border); }

        /* Visitors split pill */
        .visitor-split {
            display: flex; gap: 0.5rem; margin-top: 0.6rem; flex-wrap: wrap;
        }
        .v-pill {
            display: flex; align-items: center; gap: 0.3rem;
            padding: 0.2rem 0.6rem; border-radius: 9999px; font-size: 0.72rem; font-weight: 600;
        }
        .v-pill.customer { background: rgba(99,102,241,0.15); color: #a5b4fc; }
        .v-pill.staff    { background: rgba(245,158,11,0.12);  color: #fcd34d; }

        /* ── Camera Status Panel ─────────────────── */
        .cam-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 0.6rem;
        }
        .cam-row {
            display: flex; align-items: center; justify-content: space-between;
            background: rgba(255,255,255,0.03); border: 1px solid var(--border);
            border-radius: 10px; padding: 0.55rem 0.75rem;
        }
        .cam-label { font-size: 0.82rem; font-weight: 600; }
        .cam-role  { font-size: 0.7rem; color: var(--muted); }
        .cam-status {
            font-size: 0.72rem; font-weight: 700; padding: 0.15rem 0.5rem;
            border-radius: 9999px;
        }
        .cam-status.on  { background: rgba(16,185,129,0.15); color: var(--emerald); }
        .cam-status.off { background: rgba(100,116,139,0.15); color: var(--muted); }

        /* ── Main Body Grid ───────────────────────── */
        .body-grid {
            display: grid;
            grid-template-columns: 1.55fr 1fr;
            gap: 1.25rem;
            margin-bottom: 1.5rem;
        }
        .left-col  { display: flex; flex-direction: column; gap: 1.25rem; }
        .right-col { display: flex; flex-direction: column; gap: 1.25rem; }

        /* ── Funnel ───────────────────────────────── */
        .funnel { display: flex; flex-direction: column; gap: 0.9rem; }
        .f-row  { display: flex; align-items: center; gap: 0.75rem; }
        .f-lbl  { width: 130px; font-size: 0.85rem; font-weight: 600; flex-shrink: 0; }
        .f-track {
            flex: 1; height: 30px; background: rgba(255,255,255,0.04);
            border-radius: 8px; overflow: hidden;
            border: 1px solid rgba(255,255,255,0.05); position: relative;
        }
        .f-bar {
            height: 100%; border-radius: 8px; width: 0%;
            background: linear-gradient(90deg, #6366f1, #ec4899);
            transition: width 1.1s cubic-bezier(.4,0,.2,1);
        }
        .f-num {
            position: absolute; right: 10px; top: 50%; transform: translateY(-50%);
            font-size: 0.82rem; font-weight: 800;
        }
        .f-drop {
            width: 68px; flex-shrink: 0; text-align: center;
            font-size: 0.72rem; font-weight: 700; padding: 0.15rem 0;
            border-radius: 5px;
            background: rgba(239,68,68,0.12); color: #f87171;
            border: 1px solid rgba(239,68,68,0.2);
        }
        .f-drop.hidden { visibility: hidden; }

        /* ── Heatmap ─────────────────────────────── */
        .heat-list { display: flex; flex-direction: column; gap: 0.8rem; }
        .heat-row {
            display: flex; flex-direction: column; gap: 0.3rem;
        }
        .heat-header { display: flex; justify-content: space-between; align-items: baseline; }
        .heat-name   { font-size: 0.88rem; font-weight: 600; }
        .heat-meta   { font-size: 0.72rem; color: var(--muted); }
        .heat-pct    { font-size: 0.82rem; font-weight: 800; }
        .heat-track  {
            height: 8px; border-radius: 9999px;
            background: rgba(255,255,255,0.06); overflow: hidden;
        }
        .heat-fill {
            height: 100%; border-radius: 9999px; width: 0%;
            transition: width 1.1s cubic-bezier(.4,0,.2,1);
        }

        /* ── Event Log ───────────────────────────── */
        .log-box {
            height: 280px; overflow-y: auto;
            display: flex; flex-direction: column; gap: 0.4rem;
            padding-right: 4px;
        }
        .log-box::-webkit-scrollbar { width: 3px; }
        .log-box::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

        .log-row {
            display: flex; align-items: center; justify-content: space-between;
            padding: 0.45rem 0.7rem; border-radius: 8px;
            background: rgba(255,255,255,0.015); border: 1px solid rgba(255,255,255,0.04);
            font-size: 0.78rem; animation: slideIn 0.3s ease;
        }
        @keyframes slideIn { from{transform:translateX(8px);opacity:0} to{transform:translateX(0);opacity:1} }
        .log-time { color: var(--muted); font-family: monospace; }
        .log-vid  { flex: 1; margin: 0 0.5rem; font-weight: 500; }
        .log-zone { color: var(--muted); font-size: 0.7rem; }

        .eb { padding: 0.12rem 0.45rem; border-radius: 4px; font-size: 0.68rem; font-weight: 800; }
        .eb-entry             { background:rgba(16,185,129,0.15);  color:#6ee7b7; border:1px solid rgba(16,185,129,0.25); }
        .eb-exit              { background:rgba(239,68,68,0.12);   color:#fca5a5; border:1px solid rgba(239,68,68,0.2); }
        .eb-zone_enter        { background:rgba(99,102,241,0.15);  color:#a5b4fc; border:1px solid rgba(99,102,241,0.25); }
        .eb-zone_exit         { background:rgba(236,72,153,0.12);  color:#f9a8d4; border:1px solid rgba(236,72,153,0.2); }
        .eb-zone_dwell        { background:rgba(56,189,248,0.12);  color:#7dd3fc; border:1px solid rgba(56,189,248,0.2); }
        .eb-billing_queue_join   { background:rgba(245,158,11,0.15); color:#fcd34d; border:1px solid rgba(245,158,11,0.25); }
        .eb-billing_queue_abandon{ background:rgba(239,68,68,0.15); color:#f87171; border:1px solid rgba(239,68,68,0.25); }
        .eb-purchase          { background:rgba(16,185,129,0.2);   color:#34d399; border:1px solid rgba(16,185,129,0.35); }
        .eb-reentry           { background:rgba(16,185,129,0.1);   color:#6ee7b7; border:1px solid rgba(16,185,129,0.2); }

        /* ── Anomalies ───────────────────────────── */
        .anom-list { display: flex; flex-direction: column; gap: 0.65rem; }
        .anom-card {
            padding: 0.85rem; border-radius: 12px;
            border: 1px solid rgba(239,68,68,0.18);
            background: rgba(239,68,68,0.05);
        }
        .anom-card.warn {
            border-color: rgba(245,158,11,0.2);
            background: rgba(245,158,11,0.05);
        }
        .anom-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
        .anom-type { font-weight:700; font-size:0.85rem; }
        .sev { padding:0.1rem 0.35rem; border-radius:4px; font-size:0.65rem; font-weight:800; }
        .sev-CRITICAL { background:var(--rose);    color:#fff; }
        .sev-WARN     { background:var(--amber);   color:#000; }
        .sev-INFO     { background:var(--primary);  color:#fff; }
        .anom-action  { font-size:0.76rem; color:var(--muted); }

        /* ── Footer stat row ─────────────────────── */
        .stat-footer {
            display: flex; align-items: center; justify-content: space-between;
            padding: 0.75rem 1.25rem;
            background: var(--surface-2);
            border: 1px solid var(--border);
            border-radius: 14px;
            font-size: 0.78rem; color: var(--muted);
        }
        .stat-footer .stat { display:flex; align-items:center; gap:0.4rem; }
        .stat-footer strong { color: var(--muted2); }

        /* ── Responsive ──────────────────────────── */
        @media (max-width: 1100px) {
            .kpi-grid  { grid-template-columns: repeat(3, 1fr); }
            .body-grid { grid-template-columns: 1fr; }
        }
        @media (max-width: 700px) {
            .kpi-grid { grid-template-columns: repeat(2, 1fr); }
        }
    </style>
</head>
<body>
<div class="wrap">

    <!-- Header -->
    <header>
        <div class="logo">
            <h1>Store Intelligence Center</h1>
            <p>Computer Vision Retail Analytics &mdash; Purplle Hackathon Demo</p>
        </div>
        <div class="header-right">
            <div class="refresh-info">
                <div>Last event: <span id="last-event-ts">—</span></div>
                <div>Events processed: <span id="events-total">0</span></div>
            </div>
            <div id="health-badge" class="badge">
                <div class="dot"></div>
                <span id="health-txt">CONNECTING</span>
            </div>
        </div>
    </header>

    <!-- KPI Row -->
    <div class="kpi-grid">
        <!-- Visitors -->
        <div class="card">
            <div class="card-title"><span class="icon">👥</span> Visitors Today</div>
            <div class="kpi-val" id="uv">0</div>
            <div class="visitor-split">
                <div class="v-pill customer">🛒 Customers: <strong id="v-cust">0</strong></div>
                <div class="v-pill staff">🪪 Staff: <strong id="v-staff">0</strong></div>
            </div>
            <div class="kpi-sub" style="margin-top:0.4rem">Active inside: <strong id="v-active">0</strong></div>
        </div>

        <!-- Conversion -->
        <div class="card">
            <div class="card-title"><span class="icon">💳</span> Conversion Rate</div>
            <div class="kpi-val" id="conv">0.0%</div>
            <div class="kpi-sub">Queue → POS purchase correlation</div>
        </div>

        <!-- Queue -->
        <div class="card">
            <div class="card-title"><span class="icon">🧾</span> Max Queue Depth</div>
            <div class="kpi-val" id="qdepth">0</div>
            <div class="kpi-sub">Current in queue: <strong id="qcurr">0</strong></div>
        </div>

        <!-- Abandonment -->
        <div class="card">
            <div class="card-title"><span class="icon">🚪</span> Abandonment Rate</div>
            <div class="kpi-val" id="aband">0.0%</div>
            <div class="kpi-sub">Billing queue exits without purchase</div>
        </div>

        <!-- Camera Status -->
        <div class="card">
            <div class="card-title"><span class="icon">📷</span> Active Cameras</div>
            <div class="cam-grid" id="cam-grid">
                <div class="cam-row">
                    <div>
                        <div class="cam-label">ENTRY</div>
                        <div class="cam-role">CAM_ENTRY_01</div>
                    </div>
                    <div class="cam-status off">PENDING</div>
                </div>
                <div class="cam-row">
                    <div>
                        <div class="cam-label">ZONE-1</div>
                        <div class="cam-role">CAM_ZONE_01</div>
                    </div>
                    <div class="cam-status off">PENDING</div>
                </div>
                <div class="cam-row">
                    <div>
                        <div class="cam-label">ZONE-2</div>
                        <div class="cam-role">CAM_ZONE_02</div>
                    </div>
                    <div class="cam-status off">PENDING</div>
                </div>
                <div class="cam-row">
                    <div>
                        <div class="cam-label">BILLING</div>
                        <div class="cam-role">CAM_BILLING_01</div>
                    </div>
                    <div class="cam-status off">PENDING</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Body -->
    <div class="body-grid">
        <div class="left-col">
            <!-- Funnel -->
            <div class="card">
                <div class="card-title"><span class="icon">📊</span> Customer Conversion Funnel</div>
                <div class="funnel">
                    <div class="f-row">
                        <div class="f-lbl">Store Entry</div>
                        <div class="f-track">
                            <div class="f-bar" id="fb-entry"></div>
                            <div class="f-num" id="fv-entry">0</div>
                        </div>
                        <div class="f-drop hidden">—</div>
                    </div>
                    <div class="f-row">
                        <div class="f-lbl">Zone Visit</div>
                        <div class="f-track">
                            <div class="f-bar" id="fb-zone"></div>
                            <div class="f-num" id="fv-zone">0</div>
                        </div>
                        <div class="f-drop" id="fd-zone">—</div>
                    </div>
                    <div class="f-row">
                        <div class="f-lbl">Billing Queue</div>
                        <div class="f-track">
                            <div class="f-bar" id="fb-bill"></div>
                            <div class="f-num" id="fv-bill">0</div>
                        </div>
                        <div class="f-drop" id="fd-bill">—</div>
                    </div>
                    <div class="f-row">
                        <div class="f-lbl">Purchase</div>
                        <div class="f-track">
                            <div class="f-bar" id="fb-purch" style="background:linear-gradient(90deg,#10b981,#34d399)"></div>
                            <div class="f-num" id="fv-purch">0</div>
                        </div>
                        <div class="f-drop" id="fd-purch">—</div>
                    </div>
                </div>
            </div>

            <!-- Live Events -->
            <div class="card">
                <div class="card-title"><span class="icon">⚡</span> Live Event Stream</div>
                <div class="log-box" id="log-box">
                    <div style="text-align:center;padding:3rem 0;color:var(--muted);">Waiting for events…</div>
                </div>
            </div>
        </div>

        <div class="right-col">
            <!-- Heatmap -->
            <div class="card">
                <div class="card-title"><span class="icon">🔥</span> Zone Dwell Heatmap</div>
                <div class="heat-list" id="heat-list">
                    <div style="text-align:center;padding:2rem 0;color:var(--muted);">No zone visits recorded</div>
                </div>
            </div>

            <!-- Anomalies -->
            <div class="card">
                <div class="card-title"><span class="icon">⚠️</span> Operational Anomalies</div>
                <div class="anom-list" id="anom-list">
                    <div style="text-align:center;padding:2rem 0;color:var(--muted);">No active anomalies ✓</div>
                </div>
            </div>
        </div>
    </div>

    <!-- Footer Status Bar -->
    <div class="stat-footer">
        <div class="stat">🕐 Last Refresh: <strong id="last-refresh">—</strong></div>
        <div class="stat">📡 Stream: <strong id="stream-status">Connecting…</strong></div>
        <div class="stat">🏪 Store: <strong id="store-id-disp">—</strong></div>
        <div class="stat">📦 Events DB: <strong id="events-total2">0</strong> records</div>
    </div>
</div>

<script>
    const es = new EventSource('/dashboard/stream');
    let logFirstRender = true;
    let totalEventCount = 0;

    // ── Heatmap colour gradient (blue→orange→red) based on 0–100 score ──────
    function heatColor(score) {
        // 0-40 = indigo, 40-70 = amber, 70-100 = rose
        if (score < 40) {
            const t = score / 40;
            return `hsl(${240 - t*30}, 80%, ${55 + t*5}%)`;
        } else if (score < 70) {
            const t = (score - 40) / 30;
            return `hsl(${210 - t*160}, ${80 + t*10}%, ${60 - t*5}%)`;
        } else {
            const t = (score - 70) / 30;
            return `hsl(${50 - t*50}, 95%, ${55 - t*5}%)`;
        }
    }

    // ── Camera status panel ─────────────────────────────────────────────────
    function updateCameras(cameras) {
        if (!cameras || cameras.length === 0) return;
        const grid = document.getElementById('cam-grid');
        grid.innerHTML = cameras.map(c => `
            <div class="cam-row">
                <div>
                    <div class="cam-label">${c.role}</div>
                    <div class="cam-role">${c.camera_id}</div>
                </div>
                <div class="cam-status ${c.active ? 'on' : 'off'}">${c.active ? '✓ LIVE' : 'STALE'}</div>
            </div>
        `).join('');
    }

    es.onopen = () => {
        document.getElementById('stream-status').textContent = 'Connected';
    };

    es.onmessage = (evt) => {
        const d = JSON.parse(evt.data);

        // ── Refresh timestamp ─────────────────────
        const now = new Date();
        document.getElementById('last-refresh').textContent =
            now.toLocaleTimeString('en-IN', {hour:'2-digit', minute:'2-digit', second:'2-digit'});

        // ── Health badge ──────────────────────────
        const badge = document.getElementById('health-badge');
        const htxt  = document.getElementById('health-txt');
        if (d.health && d.health.status === 'ok') {
            badge.className = 'badge';
            htxt.textContent = 'SYSTEM ONLINE';
        } else {
            badge.className = 'badge warn';
            htxt.textContent = 'FEED DELAYED';
        }

        // ── KPI Cards ─────────────────────────────
        if (d.metrics) {
            const m = d.metrics;
            document.getElementById('uv').textContent        = m.unique_visitors;
            document.getElementById('v-cust').textContent    = m.customers ?? m.unique_visitors;
            document.getElementById('v-staff').textContent   = m.staff_count ?? m.staff_excluded ?? 0;
            document.getElementById('v-active').textContent  = m.active_visitors ?? 0;
            document.getElementById('conv').textContent      = (m.conversion_rate * 100).toFixed(1) + '%';
            document.getElementById('qdepth').textContent    = m.queue_depth;
            document.getElementById('qcurr').textContent     = m.current_queue ?? 0;
            document.getElementById('aband').textContent     = (m.abandonment_rate * 100).toFixed(1) + '%';
            document.getElementById('store-id-disp').textContent = m.store_id;
        }

        // ── Camera status ─────────────────────────
        if (d.cameras) updateCameras(d.cameras);

        // ── Funnel ────────────────────────────────
        if (d.funnel) {
            const s = d.funnel.stages, dp = d.funnel.drop_off_percent;
            const mx = Math.max(s.entry, 1);
            document.getElementById('fv-entry').textContent = s.entry;
            document.getElementById('fb-entry').style.width = '100%';

            document.getElementById('fv-zone').textContent  = s.zone_visit;
            document.getElementById('fb-zone').style.width  = (s.zone_visit/mx*100)+'%';
            document.getElementById('fd-zone').textContent  = '-'+dp.entry_to_zone_visit.toFixed(0)+'%';

            document.getElementById('fv-bill').textContent  = s.billing_queue;
            document.getElementById('fb-bill').style.width  = (s.billing_queue/mx*100)+'%';
            document.getElementById('fd-bill').textContent  = '-'+dp.zone_to_billing_queue.toFixed(0)+'%';

            document.getElementById('fv-purch').textContent = s.purchase;
            document.getElementById('fb-purch').style.width = (s.purchase/mx*100)+'%';
            document.getElementById('fd-purch').textContent = '-'+dp.billing_queue_to_purchase.toFixed(0)+'%';
        }

        // ── Heatmap ───────────────────────────────
        if (d.heatmap && d.heatmap.zones) {
            const hl = document.getElementById('heat-list');
            const zones = d.heatmap.zones;
            if (zones.length === 0) {
                hl.innerHTML = '<div style="text-align:center;padding:2rem 0;color:var(--muted);">No zone visits recorded</div>';
            } else {
                const maxScore = Math.max(...zones.map(z => z.score_0_100), 1);
                const totalVisits = zones.reduce((a,z) => a + z.visits, 0) || 1;
                hl.innerHTML = zones.map(z => {
                    const pct = ((z.visits / totalVisits) * 100).toFixed(0);
                    const fill = (z.score_0_100 / maxScore * 100).toFixed(1);
                    const color = heatColor(z.score_0_100);
                    const dwell = (z.avg_dwell_ms / 1000).toFixed(1);
                    return `
                    <div class="heat-row">
                        <div class="heat-header">
                            <span class="heat-name">${z.zone_id}</span>
                            <span>
                                <span class="heat-pct" style="color:${color}">${pct}%</span>
                                <span class="heat-meta"> · ${z.visits} visits · ${dwell}s avg</span>
                            </span>
                        </div>
                        <div class="heat-track">
                            <div class="heat-fill" style="width:${fill}%;background:${color}"></div>
                        </div>
                    </div>`;
                }).join('');
            }
        }

        // ── Anomalies ─────────────────────────────
        if (d.anomalies && d.anomalies.anomalies) {
            const al = document.getElementById('anom-list');
            const anoms = d.anomalies.anomalies;
            if (anoms.length === 0) {
                al.innerHTML = '<div style="text-align:center;padding:2rem 0;color:var(--muted);">No active anomalies ✓</div>';
            } else {
                al.innerHTML = anoms.map(a => `
                    <div class="anom-card ${a.severity === 'WARN' ? 'warn' : ''}">
                        <div class="anom-head">
                            <span class="anom-type">${a.anomaly_type.replaceAll('_',' ')}</span>
                            <span class="sev sev-${a.severity}">${a.severity}</span>
                        </div>
                        <div class="anom-action">${a.suggested_action}</div>
                    </div>`).join('');
            }
        }

        // ── Live Event Log ────────────────────────
        if (d.live_events && d.live_events.length > 0) {
            const lb = document.getElementById('log-box');
            if (logFirstRender) { lb.innerHTML = ''; logFirstRender = false; }
            d.live_events.forEach(e => {
                const row = document.createElement('div');
                row.className = 'log-row';
                const ts = e.timestamp.split('T')[1]?.substring(0,8) ?? e.timestamp;
                const ec = e.event_type.toLowerCase();
                row.innerHTML = `
                    <span class="log-time">${ts}</span>
                    <span class="log-vid">${e.visitor_id}</span>
                    <span class="eb eb-${ec}">${e.event_type}</span>
                    <span class="log-zone">${e.zone_id ?? ''}</span>`;
                lb.insertBefore(row, lb.firstChild);
                if (lb.children.length > 40) lb.removeChild(lb.lastChild);

                // Update "last event" display
                document.getElementById('last-event-ts').textContent = ts;
            });
        }

        // ── Footer stats ──────────────────────────
        if (d.total_events !== undefined) {
            totalEventCount = d.total_events;
            document.getElementById('events-total').textContent  = totalEventCount;
            document.getElementById('events-total2').textContent = totalEventCount;
        }
    };

    es.onerror = () => {
        document.getElementById('health-badge').className = 'badge warn';
        document.getElementById('health-txt').textContent = 'STREAM ERROR';
        document.getElementById('stream-status').textContent = 'Reconnecting…';
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
                empty_data = {
                    "health": {"status": "ok"},
                    "metrics": {
                        "unique_visitors": 0, "customers": 0, "staff_count": 0,
                        "active_visitors": 0, "staff_excluded": 0,
                        "conversion_rate": 0, "queue_depth": 0,
                        "current_queue": 0, "abandonment_rate": 0,
                        "avg_dwell_per_zone_ms": {}
                    },
                    "cameras": [],
                    "funnel": {
                        "stages": {"entry": 0, "zone_visit": 0, "billing_queue": 0, "purchase": 0},
                        "drop_off_percent": {"entry_to_zone_visit": 0, "zone_to_billing_queue": 0, "billing_queue_to_purchase": 0}
                    },
                    "heatmap": {"zones": []},
                    "anomalies": {"anomalies": []},
                    "live_events": [],
                    "total_events": 0,
                }
                yield f"data: {json.dumps(empty_data)}\n\n"
                await asyncio.sleep(2)
                continue

            store_id = store_ids[0]

            try:
                metrics_res  = store_metrics(store_id, request, db)
                funnel_res   = store_funnel(store_id, request, db)
                heatmap_res  = store_heatmap(store_id, request, db)
                anomalies_res = store_anomalies(store_id, request, db)
                health_res   = health(request, db)

                # Camera status
                from app.routers.analytics import _get_store_events, _latest_timestamp_iso
                all_store_events = _get_store_events(db, store_id)
                latest_ts = _latest_timestamp_iso(all_store_events)
                cam_status = _get_camera_status(all_store_events, latest_ts)

                # Live event log
                total_events = db.execute(select(IngestedEvent)).scalars().all()
                total_event_count = len(total_events)

                new_events_list = []
                if total_event_count > last_event_count:
                    newest = sorted(total_events, key=lambda x: x.id)[last_event_count:]
                    new_events_list = [
                        {
                            "timestamp": e.timestamp,
                            "visitor_id": e.visitor_id,
                            "event_type": e.event_type,
                            "zone_id": e.zone_id,
                        }
                        for e in newest
                    ]
                    last_event_count = total_event_count

                stream_data = {
                    "health":      health_res.model_dump(),
                    "metrics":     metrics_res.model_dump(),
                    "cameras":     [c.model_dump() for c in cam_status],
                    "funnel":      funnel_res.model_dump(),
                    "heatmap":     heatmap_res.model_dump(),
                    "anomalies":   anomalies_res.model_dump(),
                    "live_events": new_events_list,
                    "total_events": total_event_count,
                }

                yield f"data: {json.dumps(stream_data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(2)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")
