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
    store_metrics, store_funnel, store_heatmap, store_anomalies, store_peak_hours,
    _get_camera_status, _get_store_events, _latest_timestamp_iso,
)
from app.routers.health import health

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Store Intelligence — Purplle Analytics</title>
    <meta name="description" content="Real-time computer vision retail analytics dashboard for Purplle stores.">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:      #080c14;
            --surf:    rgba(14,22,38,0.80);
            --surf2:   rgba(20,30,52,0.95);
            --border:  rgba(255,255,255,0.07);
            --bh:      rgba(255,255,255,0.13);
            --indigo:  #6366f1;
            --rose:    #f43f5e;
            --amber:   #f59e0b;
            --emerald: #10b981;
            --sky:     #38bdf8;
            --violet:  #a78bfa;
            --text:    #f1f5f9;
            --muted:   #64748b;
            --muted2:  #94a3b8;
        }
        *, *::before, *::after { box-sizing:border-box; margin:0; padding:0; }

        body {
            font-family:'Outfit',sans-serif;
            background:var(--bg);
            color:var(--text);
            min-height:100vh;
            overflow-x:hidden;
            background-image:
                radial-gradient(ellipse 80% 50% at 5%  0%,   rgba(99,102,241,.14) 0, transparent 60%),
                radial-gradient(ellipse 60% 40% at 95% 100%, rgba(244,63,94,.09)  0, transparent 60%);
            background-attachment:fixed;
        }

        .wrap { max-width:1440px; margin:0 auto; padding:1.4rem 2rem; }

        /* ── Header ─────────────────────────────────────────────── */
        header {
            display:flex; justify-content:space-between; align-items:center;
            padding-bottom:1.2rem; margin-bottom:1.6rem;
            border-bottom:1px solid var(--border);
        }
        .logo h1 {
            font-size:1.65rem; font-weight:800; letter-spacing:-.5px;
            background:linear-gradient(120deg,#a5b4fc 0%,#f472b6 100%);
            -webkit-background-clip:text; -webkit-text-fill-color:transparent;
        }
        .logo p { font-size:.78rem; color:var(--muted); margin-top:3px; }

        .header-right { display:flex; align-items:center; gap:.9rem; }

        .badge {
            display:flex; align-items:center; gap:.4rem;
            padding:.38rem .9rem; border-radius:9999px;
            font-size:.75rem; font-weight:700;
            background:rgba(16,185,129,.12); border:1px solid rgba(16,185,129,.25); color:var(--emerald);
            transition:all .3s;
        }
        .badge.warn  { background:rgba(245,158,11,.12); border-color:rgba(245,158,11,.25); color:var(--amber); }
        .badge.error { background:rgba(244,63,94,.12);  border-color:rgba(244,63,94,.25);  color:var(--rose); }
        .dot { width:7px; height:7px; border-radius:50%; background:currentColor; animation:pulse 1.5s infinite; }
        @keyframes pulse { 0%,100%{transform:scale(.9);opacity:.6} 50%{transform:scale(1.2);opacity:1} }

        .hdr-stat { font-size:.72rem; color:var(--muted); text-align:right; line-height:1.5; }
        .hdr-stat b { color:var(--muted2); font-weight:600; }

        /* ── Cards ───────────────────────────────────────────────── */
        .card {
            background:var(--surf);
            border:1px solid var(--border);
            border-radius:18px; padding:1.3rem;
            backdrop-filter:blur(20px);
            box-shadow:0 8px 32px rgba(0,0,0,.4);
            transition:border-color .25s, transform .25s;
        }
        .card:hover { border-color:var(--bh); transform:translateY(-2px); }

        .ctitle {
            font-size:.7rem; font-weight:700; text-transform:uppercase;
            letter-spacing:.8px; color:var(--muted); margin-bottom:.9rem;
            display:flex; align-items:center; gap:.35rem;
        }
        .ctitle .icon { font-size:.95rem; }

        /* ── KPI Grid (Row 1 — 5 cols) ───────────────────────────── */
        .kpi-r1 {
            display:grid;
            grid-template-columns: repeat(5,1fr);
            gap:1.1rem; margin-bottom:1.1rem;
        }
        /* KPI Grid (Row 2 — 3 new KPI + camera = 4 cols) */
        .kpi-r2 {
            display:grid;
            grid-template-columns: 1fr 1fr 1fr 1.6fr;
            gap:1.1rem; margin-bottom:1.4rem;
        }

        .kval {
            font-size:2.2rem; font-weight:800; line-height:1;
            background:linear-gradient(135deg,#fff 0%,#cbd5e1 100%);
            -webkit-background-clip:text; -webkit-text-fill-color:transparent;
            margin-bottom:.35rem;
        }
        .kval.green { background:linear-gradient(135deg,#34d399,#10b981);
                       -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
        .kval.amber { background:linear-gradient(135deg,#fcd34d,#f59e0b);
                       -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
        .ksub { font-size:.76rem; color:var(--muted); }
        .ksub b { color:var(--muted2); font-weight:600; }

        /* visitor pills */
        .vpills { display:flex; gap:.4rem; margin-top:.5rem; flex-wrap:wrap; }
        .vpill {
            display:flex; align-items:center; gap:.25rem;
            padding:.18rem .55rem; border-radius:9999px; font-size:.68rem; font-weight:700;
        }
        .vpill.c { background:rgba(99,102,241,.15); color:#a5b4fc; }
        .vpill.s { background:rgba(245,158,11,.12);  color:#fcd34d; }

        /* ── Camera Status ───────────────────────────────────────── */
        .cam-grid { display:grid; grid-template-columns:1fr 1fr; gap:.5rem; }
        .cam-row {
            display:flex; align-items:center; justify-content:space-between;
            background:rgba(255,255,255,.03); border:1px solid var(--border);
            border-radius:10px; padding:.5rem .7rem;
        }
        .cam-lbl  { font-size:.8rem; font-weight:700; }
        .cam-sub  { font-size:.65rem; color:var(--muted); margin-top:1px; }
        .cam-sta  {
            text-align:right;
        }
        .cam-tag {
            font-size:.65rem; font-weight:800; padding:.12rem .45rem;
            border-radius:9999px; display:block;
        }
        .cam-tag.on  { background:rgba(16,185,129,.18); color:var(--emerald); }
        .cam-tag.off { background:rgba(100,116,139,.15); color:var(--muted); }
        .cam-age { font-size:.6rem; color:var(--muted); margin-top:2px; display:block; }

        /* ── Body grid ───────────────────────────────────────────── */
        .body-grid {
            display:grid; grid-template-columns:1.55fr 1fr;
            gap:1.2rem; margin-bottom:1.4rem;
        }
        .left-col { display:flex; flex-direction:column; gap:1.2rem; }
        .right-col { display:flex; flex-direction:column; gap:1.2rem; }

        /* ── Funnel ──────────────────────────────────────────────── */
        .funnel { display:flex; flex-direction:column; gap:.75rem; }
        .f-row  { display:flex; align-items:center; gap:.65rem; }
        .f-lbl  { width:120px; font-size:.82rem; font-weight:600; flex-shrink:0; }
        .f-track {
            flex:1; height:28px; border-radius:8px; overflow:hidden;
            background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.05); position:relative;
        }
        .f-bar {
            height:100%; border-radius:8px; width:0%;
            background:linear-gradient(90deg,#6366f1,#ec4899);
            transition:width 1.1s cubic-bezier(.4,0,.2,1);
        }
        .f-bar.green { background:linear-gradient(90deg,#10b981,#34d399); }
        .f-num {
            position:absolute; right:10px; top:50%; transform:translateY(-50%);
            font-size:.78rem; font-weight:800;
        }
        .f-drop {
            width:60px; flex-shrink:0; text-align:center;
            font-size:.68rem; font-weight:700; padding:.12rem 0;
            border-radius:5px;
            background:rgba(239,68,68,.1); color:#f87171;
            border:1px solid rgba(239,68,68,.2);
        }
        .f-drop.hidden { visibility:hidden; }

        /* ── Peak hour chart ─────────────────────────────────────── */
        .peak-chart {
            display:flex; align-items:flex-end; gap:3px;
            height:80px; padding-top:.5rem;
        }
        .peak-bar-wrap { display:flex; flex-direction:column; align-items:center; flex:1; gap:3px; }
        .peak-bar {
            width:100%; border-radius:4px 4px 0 0;
            background:linear-gradient(180deg,var(--indigo),rgba(99,102,241,.3));
            transition:height .6s cubic-bezier(.4,0,.2,1);
            min-height:3px;
        }
        .peak-bar.peak-hi { background:linear-gradient(180deg,#f43f5e,rgba(244,63,94,.4)); }
        .peak-lbl { font-size:.55rem; color:var(--muted); white-space:nowrap; transform:rotate(-45deg); }
        .peak-summary { font-size:.76rem; color:var(--muted); margin-top:.5rem; }
        .peak-summary b { color:var(--sky); }

        /* ── Heatmap ─────────────────────────────────────────────── */
        .heat-list { display:flex; flex-direction:column; gap:.75rem; }
        .heat-row { display:flex; flex-direction:column; gap:.25rem; }
        .heat-hdr { display:flex; justify-content:space-between; align-items:baseline; }
        .heat-name { font-size:.85rem; font-weight:700; }
        .heat-meta { font-size:.68rem; color:var(--muted); }
        .heat-pct  { font-size:.78rem; font-weight:800; }
        .heat-track { height:7px; border-radius:9999px; background:rgba(255,255,255,.06); overflow:hidden; }
        .heat-fill { height:100%; border-radius:9999px; width:0%; transition:width 1.1s cubic-bezier(.4,0,.2,1); }

        /* ── Event log ───────────────────────────────────────────── */
        .log-box {
            height:260px; overflow-y:auto;
            display:flex; flex-direction:column; gap:.35rem; padding-right:4px;
        }
        .log-box::-webkit-scrollbar { width:3px; }
        .log-box::-webkit-scrollbar-thumb { background:var(--border); border-radius:2px; }
        .log-row {
            display:flex; align-items:center; justify-content:space-between;
            padding:.4rem .65rem; border-radius:8px;
            background:rgba(255,255,255,.015); border:1px solid rgba(255,255,255,.04);
            font-size:.75rem; animation:slideIn .25s ease;
        }
        @keyframes slideIn { from{transform:translateX(8px);opacity:0} to{transform:translateX(0);opacity:1} }
        .log-time { color:var(--muted); font-family:monospace; flex-shrink:0; }
        .log-vid  { flex:1; margin:0 .45rem; font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .log-zone { color:var(--muted); font-size:.66rem; flex-shrink:0; }

        .eb { padding:.1rem .4rem; border-radius:4px; font-size:.63rem; font-weight:800; flex-shrink:0; }
        .eb-entry              { background:rgba(16,185,129,.15);  color:#6ee7b7; border:1px solid rgba(16,185,129,.25); }
        .eb-exit               { background:rgba(239,68,68,.12);   color:#fca5a5; border:1px solid rgba(239,68,68,.2); }
        .eb-zone_enter         { background:rgba(99,102,241,.15);  color:#a5b4fc; border:1px solid rgba(99,102,241,.25); }
        .eb-zone_exit          { background:rgba(236,72,153,.12);  color:#f9a8d4; border:1px solid rgba(236,72,153,.2); }
        .eb-zone_dwell         { background:rgba(56,189,248,.12);  color:#7dd3fc; border:1px solid rgba(56,189,248,.2); }
        .eb-billing_queue_join    { background:rgba(245,158,11,.15); color:#fcd34d; border:1px solid rgba(245,158,11,.25); }
        .eb-billing_queue_abandon { background:rgba(239,68,68,.15); color:#f87171; border:1px solid rgba(239,68,68,.25); }
        .eb-purchase           { background:rgba(16,185,129,.25);  color:#34d399; border:1px solid rgba(16,185,129,.4); }
        .eb-reentry            { background:rgba(16,185,129,.1);   color:#6ee7b7; border:1px solid rgba(16,185,129,.2); }

        /* ── Anomalies ───────────────────────────────────────────── */
        .anom-list { display:flex; flex-direction:column; gap:.6rem; }
        .anom-card {
            padding:.8rem; border-radius:12px;
            border:1px solid rgba(239,68,68,.18); background:rgba(239,68,68,.05);
        }
        .anom-card.warn { border-color:rgba(245,158,11,.2); background:rgba(245,158,11,.05); }
        .anom-card.info { border-color:rgba(99,102,241,.2); background:rgba(99,102,241,.05); }
        .anom-hd { display:flex; justify-content:space-between; align-items:center; margin-bottom:4px; }
        .anom-type { font-weight:700; font-size:.82rem; }
        .sev { padding:.1rem .35rem; border-radius:4px; font-size:.6rem; font-weight:800; }
        .sev-CRITICAL { background:var(--rose);    color:#fff; }
        .sev-WARN     { background:var(--amber);   color:#000; }
        .sev-INFO     { background:var(--indigo);  color:#fff; }
        .anom-act { font-size:.73rem; color:var(--muted); line-height:1.4; }
        .anom-det { font-size:.68rem; color:var(--muted2); margin-top:3px; font-family:monospace; }

        /* ── Footer ──────────────────────────────────────────────── */
        .footer-bar {
            display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:.5rem;
            padding:.7rem 1.2rem;
            background:var(--surf2); border:1px solid var(--border); border-radius:14px;
            font-size:.72rem; color:var(--muted);
        }
        .footer-bar b { color:var(--muted2); }

        /* ── Responsive ──────────────────────────────────────────── */
        @media (max-width:1200px) {
            .kpi-r1 { grid-template-columns:repeat(3,1fr); }
            .kpi-r2 { grid-template-columns:repeat(2,1fr); }
            .body-grid { grid-template-columns:1fr; }
        }
        @media (max-width:700px) {
            .kpi-r1,.kpi-r2 { grid-template-columns:repeat(2,1fr); }
            .wrap { padding:1rem; }
        }
    </style>
</head>
<body>
<div class="wrap">

    <!-- ── Header ──────────────────────────────────────────────────────── -->
    <header>
        <div class="logo">
            <h1>Store Intelligence Center</h1>
            <p>Computer Vision Retail Analytics &mdash; Powered by YOLOv8 + ByteTrack</p>
        </div>
        <div class="header-right">
            <div class="hdr-stat">
                Last event: <b id="last-evt-ts">—</b><br>
                Events in DB: <b id="evts-total">0</b>
            </div>
            <div id="live-badge" class="badge error">
                <div class="dot"></div>
                <span id="live-txt">CONNECTING</span>
            </div>
        </div>
    </header>

    <!-- ── KPI Row 1 (5 cards) ────────────────────────────────────────── -->
    <div class="kpi-r1">
        <!-- Visitors -->
        <div class="card">
            <div class="ctitle"><span class="icon">👥</span> Visitors Today</div>
            <div class="kval" id="uv">0</div>
            <div class="vpills">
                <div class="vpill c">🛒 Customers <b id="v-cust">0</b></div>
                <div class="vpill s">🪪 Staff <b id="v-staff">0</b></div>
            </div>
            <div class="ksub" style="margin-top:.4rem">Active: <b id="v-active">0</b></div>
        </div>
        <!-- Conversion -->
        <div class="card">
            <div class="ctitle"><span class="icon">💳</span> Conversion Rate</div>
            <div class="kval green" id="conv">0.0%</div>
            <div class="ksub">Queue → Purchase correlation</div>
        </div>
        <!-- Queue -->
        <div class="card">
            <div class="ctitle"><span class="icon">🧾</span> Queue Depth</div>
            <div class="kval amber" id="qdepth">0</div>
            <div class="ksub">Current: <b id="qcurr">0</b> in queue</div>
        </div>
        <!-- Abandonment -->
        <div class="card">
            <div class="ctitle"><span class="icon">🚪</span> Abandonment</div>
            <div class="kval" id="aband">0.0%</div>
            <div class="ksub">Billing exits without purchase</div>
        </div>
        <!-- Staff ratio -->
        <div class="card">
            <div class="ctitle"><span class="icon">📊</span> Staff Analytics</div>
            <div class="kval" id="ratio">—</div>
            <div class="ksub">Customer / Staff ratio</div>
            <div class="ksub" style="margin-top:.25rem">
                <span style="color:var(--indigo)">●</span> <b id="s-cust2">0</b> customers &nbsp;
                <span style="color:var(--amber)">●</span> <b id="s-staff2">0</b> staff
            </div>
        </div>
    </div>

    <!-- ── KPI Row 2 (3 new + camera status) ─────────────────────────── -->
    <div class="kpi-r2">
        <!-- Avg dwell -->
        <div class="card">
            <div class="ctitle"><span class="icon">⏱</span> Avg Dwell Time</div>
            <div class="kval" id="avg-dwell">0.0 min</div>
            <div class="ksub">Average across all product zones</div>
        </div>
        <!-- Top zone -->
        <div class="card">
            <div class="ctitle"><span class="icon">🏆</span> Most Visited Zone</div>
            <div class="kval" id="top-zone" style="font-size:1.4rem">—</div>
            <div class="ksub">Highest customer traffic zone</div>
        </div>
        <!-- Repeat visitors -->
        <div class="card">
            <div class="ctitle"><span class="icon">🔄</span> Repeat Visitors</div>
            <div class="kval" id="repeat-vis">0</div>
            <div class="ksub">Seen in 2+ camera zones</div>
        </div>
        <!-- Camera status -->
        <div class="card">
            <div class="ctitle"><span class="icon">📷</span> Active Cameras</div>
            <div class="cam-grid" id="cam-grid">
                <div class="cam-row">
                    <div><div class="cam-lbl">ENTRY</div><div class="cam-sub">CAM_ENTRY_01</div></div>
                    <div class="cam-sta"><span class="cam-tag off">PENDING</span></div>
                </div>
                <div class="cam-row">
                    <div><div class="cam-lbl">ZONE-1</div><div class="cam-sub">CAM_ZONE_01</div></div>
                    <div class="cam-sta"><span class="cam-tag off">PENDING</span></div>
                </div>
                <div class="cam-row">
                    <div><div class="cam-lbl">ZONE-2</div><div class="cam-sub">CAM_ZONE_02</div></div>
                    <div class="cam-sta"><span class="cam-tag off">PENDING</span></div>
                </div>
                <div class="cam-row">
                    <div><div class="cam-lbl">BILLING</div><div class="cam-sub">CAM_BILLING_01</div></div>
                    <div class="cam-sta"><span class="cam-tag off">PENDING</span></div>
                </div>
            </div>
        </div>
    </div>

    <!-- ── Body ──────────────────────────────────────────────────────── -->
    <div class="body-grid">
        <div class="left-col">
            <!-- Funnel -->
            <div class="card">
                <div class="ctitle"><span class="icon">📊</span> Customer Conversion Funnel</div>
                <div class="funnel">
                    <div class="f-row">
                        <div class="f-lbl">Store Entry</div>
                        <div class="f-track"><div class="f-bar" id="fb-e"></div><div class="f-num" id="fv-e">0</div></div>
                        <div class="f-drop hidden">—</div>
                    </div>
                    <div class="f-row">
                        <div class="f-lbl">Zone Visit</div>
                        <div class="f-track"><div class="f-bar" id="fb-z"></div><div class="f-num" id="fv-z">0</div></div>
                        <div class="f-drop" id="fd-z">—</div>
                    </div>
                    <div class="f-row">
                        <div class="f-lbl">Billing Queue</div>
                        <div class="f-track"><div class="f-bar" id="fb-b"></div><div class="f-num" id="fv-b">0</div></div>
                        <div class="f-drop" id="fd-b">—</div>
                    </div>
                    <div class="f-row">
                        <div class="f-lbl">Purchase ✓</div>
                        <div class="f-track"><div class="f-bar green" id="fb-p"></div><div class="f-num" id="fv-p">0</div></div>
                        <div class="f-drop" id="fd-p">—</div>
                    </div>
                </div>
            </div>

            <!-- Peak Hours -->
            <div class="card">
                <div class="ctitle"><span class="icon">📈</span> Peak Traffic Hours</div>
                <div class="peak-chart" id="peak-chart">
                    <div style="color:var(--muted);font-size:.8rem;padding:1rem">No traffic data yet…</div>
                </div>
                <div class="peak-summary" id="peak-summary"></div>
            </div>

            <!-- Live Events -->
            <div class="card">
                <div class="ctitle"><span class="icon">⚡</span> Live Event Stream</div>
                <div class="log-box" id="log-box">
                    <div style="text-align:center;padding:2.5rem 0;color:var(--muted)">Waiting for events…</div>
                </div>
            </div>
        </div>

        <div class="right-col">
            <!-- Heatmap -->
            <div class="card">
                <div class="ctitle"><span class="icon">🔥</span> Zone Dwell Heatmap</div>
                <div class="heat-list" id="heat-list">
                    <div style="text-align:center;padding:2rem 0;color:var(--muted)">No zone data recorded</div>
                </div>
            </div>

            <!-- Anomalies -->
            <div class="card">
                <div class="ctitle"><span class="icon">⚠️</span> Operational Anomalies</div>
                <div class="anom-list" id="anom-list">
                    <div style="text-align:center;padding:2rem 0;color:var(--muted)">No active anomalies ✓</div>
                </div>
            </div>
        </div>
    </div>

    <!-- ── Footer ────────────────────────────────────────────────────── -->
    <div class="footer-bar">
        <div>🕐 Refreshed: <b id="last-refresh">—</b></div>
        <div>📡 Stream: <b id="stream-stat">Connecting…</b></div>
        <div>🏪 Store: <b id="store-disp">—</b></div>
        <div>📦 DB Records: <b id="evts-total2">0</b></div>
        <div>⚙️ Powered by YOLOv8n + ByteTrack</div>
    </div>
</div>

<script>
// ── Heat colour function ───────────────────────────────────────────────────
function heatColor(score) {
    if (score < 33)       return `hsl(240,75%,60%)`;
    else if (score < 66)  return `hsl(${240-(score-33)*4},85%,58%)`;
    else                  return `hsl(${108-(score-66)*3},90%,50%)`;
}

// ── Format seconds as human-readable ─────────────────────────────────────
function fmtAgo(secs) {
    if (secs === null || secs === undefined) return '';
    if (secs < 60)  return secs + 's ago';
    if (secs < 3600) return Math.floor(secs/60) + 'm ago';
    return Math.floor(secs/3600) + 'h ago';
}

// ── Camera panel ──────────────────────────────────────────────────────────
function renderCameras(cameras) {
    if (!cameras || cameras.length === 0) return;
    const g = document.getElementById('cam-grid');
    g.innerHTML = cameras.map(c => `
        <div class="cam-row">
            <div>
                <div class="cam-lbl">${c.role}</div>
                <div class="cam-sub">${c.camera_id}</div>
            </div>
            <div class="cam-sta">
                <span class="cam-tag ${c.active ? 'on' : 'off'}">${c.active ? '✓ LIVE' : 'STALE'}</span>
                <span class="cam-age">${fmtAgo(c.seconds_since_last_event)}</span>
            </div>
        </div>`).join('');
}

// ── Peak hour chart ───────────────────────────────────────────────────────
function renderPeakChart(data) {
    if (!data || !data.hourly_buckets || data.hourly_buckets.length === 0) return;
    const chart = document.getElementById('peak-chart');
    const maxCount = Math.max(...data.hourly_buckets.map(b => b.visitor_count), 1);
    chart.innerHTML = data.hourly_buckets.map(b => {
        const pct = Math.max(5, (b.visitor_count / maxCount) * 100);
        const isPeak = b.hour === data.peak_hour;
        return `<div class="peak-bar-wrap" title="${b.label}: ${b.visitor_count} visitors">
            <div class="peak-bar ${isPeak ? 'peak-hi' : ''}" style="height:${pct}%"></div>
            <div class="peak-lbl">${b.label}</div>
        </div>`;
    }).join('');
    const s = document.getElementById('peak-summary');
    s.innerHTML = `Peak: <b>${data.peak_hour_label}</b> &mdash; ${data.peak_count} visitor${data.peak_count!==1?'s':''}`;
}

// ── SSE connection ─────────────────────────────────────────────────────────
let lastSSETime = 0;
let logFirst = true;

const es = new EventSource('/dashboard/stream');

es.onopen = () => {
    lastSSETime = Date.now();
    document.getElementById('stream-stat').textContent = 'Connected';
};

es.onmessage = (evt) => {
    lastSSETime = Date.now();
    const d = JSON.parse(evt.data);

    // ── LIVE badge: based on SSE connection, not event recency ────────
    const badge = document.getElementById('live-badge');
    const ltxt  = document.getElementById('live-txt');
    badge.className = 'badge';
    ltxt.textContent = 'LIVE';

    // ── Refresh time ──────────────────────────────────────────────────
    const now = new Date();
    document.getElementById('last-refresh').textContent =
        now.toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',second:'2-digit'});

    // ── KPI Row 1 ─────────────────────────────────────────────────────
    if (d.metrics) {
        const m = d.metrics;
        document.getElementById('uv').textContent     = m.unique_visitors;
        document.getElementById('v-cust').textContent  = m.customers ?? m.unique_visitors;
        document.getElementById('v-staff').textContent = m.staff_count ?? m.staff_excluded ?? 0;
        document.getElementById('v-active').textContent= m.active_visitors ?? 0;
        document.getElementById('conv').textContent    = (m.conversion_rate * 100).toFixed(1) + '%';
        document.getElementById('qdepth').textContent  = m.queue_depth;
        document.getElementById('qcurr').textContent   = m.current_queue ?? 0;
        document.getElementById('aband').textContent   = (m.abandonment_rate * 100).toFixed(1) + '%';

        // Staff analytics card
        const cust  = m.customers ?? m.unique_visitors;
        const staff = m.staff_count ?? m.staff_excluded ?? 0;
        document.getElementById('ratio').textContent   = staff > 0 ? (cust/staff).toFixed(1)+'x' : (cust > 0 ? '∞' : '—');
        document.getElementById('s-cust2').textContent = cust;
        document.getElementById('s-staff2').textContent= staff;

        // KPI Row 2
        const dwell = m.avg_dwell_ms ?? 0;
        document.getElementById('avg-dwell').textContent  = dwell > 0 ? (dwell/60000).toFixed(1)+' min' : '—';
        document.getElementById('top-zone').textContent   = m.most_visited_zone ?? '—';
        document.getElementById('repeat-vis').textContent = m.repeat_visitors ?? 0;

        document.getElementById('store-disp').textContent = m.store_id;
    }

    // ── Cameras ───────────────────────────────────────────────────────
    if (d.cameras) renderCameras(d.cameras);

    // ── Funnel ────────────────────────────────────────────────────────
    if (d.funnel) {
        const s = d.funnel.stages, dp = d.funnel.drop_off_percent;
        const mx = Math.max(s.entry, 1);
        document.getElementById('fv-e').textContent = s.entry;
        document.getElementById('fb-e').style.width = '100%';

        document.getElementById('fv-z').textContent = s.zone_visit;
        document.getElementById('fb-z').style.width = (s.zone_visit/mx*100)+'%';
        document.getElementById('fd-z').textContent = '-'+dp.entry_to_zone_visit.toFixed(0)+'%';

        document.getElementById('fv-b').textContent = s.billing_queue;
        document.getElementById('fb-b').style.width = (s.billing_queue/mx*100)+'%';
        document.getElementById('fd-b').textContent = '-'+dp.zone_to_billing_queue.toFixed(0)+'%';

        document.getElementById('fv-p').textContent = s.purchase;
        document.getElementById('fb-p').style.width = (s.purchase/mx*100)+'%';
        document.getElementById('fd-p').textContent = '-'+dp.billing_queue_to_purchase.toFixed(0)+'%';
    }

    // ── Peak hours ────────────────────────────────────────────────────
    if (d.peak_hours) renderPeakChart(d.peak_hours);

    // ── Heatmap ───────────────────────────────────────────────────────
    if (d.heatmap && d.heatmap.zones) {
        const hl = document.getElementById('heat-list');
        const zones = d.heatmap.zones;
        if (zones.length === 0) {
            hl.innerHTML = '<div style="text-align:center;padding:2rem 0;color:var(--muted)">No zone data recorded</div>';
        } else {
            const totalVisits = zones.reduce((a,z) => a+z.visits, 0) || 1;
            const maxScore = Math.max(...zones.map(z => z.score_0_100), 1);
            hl.innerHTML = zones.map(z => {
                const pct   = ((z.visits/totalVisits)*100).toFixed(0);
                const fill  = ((z.score_0_100/maxScore)*100).toFixed(1);
                const color = heatColor(z.score_0_100);
                const dwell = (z.avg_dwell_ms/1000).toFixed(0);
                return `<div class="heat-row">
                    <div class="heat-hdr">
                        <span class="heat-name">${z.zone_id}</span>
                        <span>
                            <span class="heat-pct" style="color:${color}">${pct}%</span>
                            <span class="heat-meta"> · ${z.visits} visit${z.visits!==1?'s':''} · ${dwell}s dwell</span>
                        </span>
                    </div>
                    <div class="heat-track">
                        <div class="heat-fill" style="width:${fill}%;background:${color}"></div>
                    </div>
                </div>`;
            }).join('');
        }
    }

    // ── Anomalies ─────────────────────────────────────────────────────
    if (d.anomalies && d.anomalies.anomalies) {
        const al = document.getElementById('anom-list');
        const anoms = d.anomalies.anomalies;
        if (anoms.length === 0) {
            al.innerHTML = '<div style="text-align:center;padding:2rem 0;color:var(--muted)">No active anomalies ✓</div>';
        } else {
            al.innerHTML = anoms.map(a => {
                const cls = a.severity === 'WARN' ? 'warn' : (a.severity === 'INFO' ? 'info' : '');
                const dets = Object.entries(a.details || {})
                    .map(([k,v]) => `${k}: ${v}`).join(' · ');
                return `<div class="anom-card ${cls}">
                    <div class="anom-hd">
                        <span class="anom-type">${a.anomaly_type.replaceAll('_',' ')}</span>
                        <span class="sev sev-${a.severity}">${a.severity}</span>
                    </div>
                    <div class="anom-act">${a.suggested_action}</div>
                    ${dets ? `<div class="anom-det">${dets}</div>` : ''}
                </div>`;
            }).join('');
        }
    }

    // ── Live event log ────────────────────────────────────────────────
    if (d.live_events && d.live_events.length > 0) {
        const lb = document.getElementById('log-box');
        if (logFirst) { lb.innerHTML = ''; logFirst = false; }
        d.live_events.forEach(e => {
            const row = document.createElement('div');
            row.className = 'log-row';
            const ts = (e.timestamp || '').split('T')[1]?.substring(0,8) ?? e.timestamp;
            const ec = (e.event_type || '').toLowerCase();
            row.innerHTML = `
                <span class="log-time">${ts}</span>
                <span class="log-vid">${e.visitor_id}</span>
                <span class="eb eb-${ec}">${e.event_type}</span>
                <span class="log-zone">${e.zone_id ?? ''}</span>`;
            lb.insertBefore(row, lb.firstChild);
            if (lb.children.length > 40) lb.removeChild(lb.lastChild);
            document.getElementById('last-evt-ts').textContent = ts;
        });
    }

    // ── Footer stats ──────────────────────────────────────────────────
    if (d.total_events !== undefined) {
        document.getElementById('evts-total').textContent  = d.total_events;
        document.getElementById('evts-total2').textContent = d.total_events;
    }
};

es.onerror = () => {
    // Only mark as error if we haven't received data recently
    if (Date.now() - lastSSETime > 5000) {
        document.getElementById('live-badge').className = 'badge error';
        document.getElementById('live-txt').textContent = 'DISCONNECTED';
        document.getElementById('stream-stat').textContent = 'Reconnecting…';
    }
};

// ── Periodic LIVE badge keep-alive check ─────────────────────────────────
setInterval(() => {
    const age = Date.now() - lastSSETime;
    const badge = document.getElementById('live-badge');
    const ltxt  = document.getElementById('live-txt');
    if (lastSSETime === 0) return;
    if (age < 5000) {
        badge.className = 'badge';
        ltxt.textContent = 'LIVE';
    } else if (age < 15000) {
        badge.className = 'badge warn';
        ltxt.textContent = 'FEED DELAYED';
    } else {
        badge.className = 'badge error';
        ltxt.textContent = 'DISCONNECTED';
    }
}, 3000);
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

            store_ids = db.execute(select(IngestedEvent.store_id).distinct()).scalars().all()

            if not store_ids:
                empty = {
                    "health": {"status": "ok"},
                    "metrics": {
                        "unique_visitors": 0, "customers": 0, "staff_count": 0,
                        "active_visitors": 0, "staff_excluded": 0, "conversion_rate": 0,
                        "queue_depth": 0, "current_queue": 0, "abandonment_rate": 0,
                        "avg_dwell_ms": 0, "most_visited_zone": None, "repeat_visitors": 0,
                        "avg_dwell_per_zone_ms": {},
                    },
                    "cameras": [],
                    "funnel": {
                        "stages": {"entry": 0, "zone_visit": 0, "billing_queue": 0, "purchase": 0},
                        "drop_off_percent": {"entry_to_zone_visit": 0, "zone_to_billing_queue": 0, "billing_queue_to_purchase": 0},
                    },
                    "heatmap": {"zones": []},
                    "anomalies": {"anomalies": []},
                    "peak_hours": {"hourly_buckets": [], "peak_hour": 12, "peak_hour_label": "12 PM", "peak_count": 0},
                    "live_events": [],
                    "total_events": 0,
                }
                yield f"data: {json.dumps(empty)}\n\n"
                await asyncio.sleep(2)
                continue

            store_id = store_ids[0]

            try:
                metrics_res   = store_metrics(store_id, request, db)
                funnel_res    = store_funnel(store_id, request, db)
                heatmap_res   = store_heatmap(store_id, request, db)
                anomalies_res = store_anomalies(store_id, request, db)
                peak_res      = store_peak_hours(store_id, request, db)
                health_res    = health(request, db)

                # Camera status
                all_store_events = _get_store_events(db, store_id)
                latest_ts = _latest_timestamp_iso(all_store_events)
                cam_status = _get_camera_status(all_store_events, latest_ts)

                # Live events
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
                    "peak_hours":  peak_res.model_dump(),
                    "live_events": new_events_list,
                    "total_events": total_event_count,
                }

                yield f"data: {json.dumps(stream_data)}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'error': str(e)})}\n\n"

            await asyncio.sleep(2)

    from fastapi.responses import StreamingResponse
    return StreamingResponse(event_generator(), media_type="text/event-stream")
