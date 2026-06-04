# run_all.ps1 — Full pipeline runner for all store folders (Windows)
# Usage:  .\pipeline\run_all.ps1
#         .\pipeline\run_all.ps1 -StoreRoot "data" -StoreId "STORE_BLR_002" -OutputJson "out/events.json"

param(
    [string]$StoreRoot  = "data",
    [string]$StoreId    = "STORE_BLR_002",
    [string]$OutputJson = "out/events.json",
    [int]$Stride        = 15,
    [float]$Conf        = 0.10
)

$ErrorActionPreference = "Stop"

# Locate Python (prefer venv)
$PythonExe = "python"
if (Test-Path ".\venv\Scripts\python.exe") {
    $PythonExe = ".\venv\Scripts\python.exe"
}

Write-Host "=== Store Intelligence Pipeline ===" -ForegroundColor Cyan
Write-Host "Store Root : $StoreRoot"
Write-Host "Store ID   : $StoreId"
Write-Host "Python     : $PythonExe"
Write-Host ""

# ── Step 1: Detect & Track (processes ALL video files recursively) ─────────────
Write-Host "Step 1/3  Detect + Track (detect.py) ..." -ForegroundColor Yellow
Write-Host "         Input: $StoreRoot  (recursive — finds entry, zone, billing cameras)"
& $PythonExe pipeline/detect.py `
    --input    $StoreRoot `
    --store-id $StoreId `
    --output   out/tracks.jsonl `
    --stride   $Stride `
    --conf     $Conf

if ($LASTEXITCODE -ne 0) { Write-Error "detect.py failed"; exit 1 }
Write-Host "  Done. Tracks -> out/tracks.jsonl" -ForegroundColor Green

# ── Step 2: Re-ID + Staff Detection ───────────────────────────────────────────
Write-Host ""
Write-Host "Step 2/3  Re-ID + Staff Detection (tracker.py) ..." -ForegroundColor Yellow
& $PythonExe pipeline/tracker.py `
    --input  $StoreRoot `
    --tracks out/tracks.jsonl `
    --output out/visitor_tracks.jsonl

if ($LASTEXITCODE -ne 0) { Write-Error "tracker.py failed"; exit 1 }
Write-Host "  Done. Visitor tracks -> out/visitor_tracks.jsonl" -ForegroundColor Green

# ── Step 3: Emit Events ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Step 3/3  Event Emission (emit.py) ..." -ForegroundColor Yellow
& $PythonExe pipeline/emit.py `
    --input  out/visitor_tracks.jsonl `
    --output $OutputJson

if ($LASTEXITCODE -ne 0) { Write-Error "emit.py failed"; exit 1 }
Write-Host "  Done. Events -> $OutputJson" -ForegroundColor Green

# ── Summary ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=== Pipeline Complete ===" -ForegroundColor Cyan

$Events = Get-Content $OutputJson | ConvertFrom-Json
$EventTypes = $Events | Group-Object event_type | Sort-Object Count -Descending

Write-Host "Event type breakdown:" -ForegroundColor White
$EventTypes | Format-Table Name, Count -AutoSize

Write-Host ""
Write-Host "To ingest into the API, run:" -ForegroundColor Yellow
Write-Host "  curl -X POST http://localhost:8000/events/ingest -H 'Content-Type: application/json' -d `@$OutputJson" -ForegroundColor Gray

# ── Optional: Auto-ingest if API is running ────────────────────────────────────
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/health" -TimeoutSec 2 -ErrorAction Stop
    if ($health.status -eq "ok" -or $health.status -eq "degraded") {
        Write-Host ""
        Write-Host "API detected at localhost:8000 — auto-ingesting events..." -ForegroundColor Cyan
        $body = Get-Content $OutputJson -Raw
        $result = Invoke-RestMethod -Uri "http://localhost:8000/events/ingest" `
            -Method POST `
            -ContentType "application/json" `
            -Body $body
        Write-Host "  Ingested: $($result.ingested)  Skipped: $($result.skipped)" -ForegroundColor Green
    }
} catch {
    Write-Host "(API not running — skipping auto-ingest)" -ForegroundColor DarkGray
}
