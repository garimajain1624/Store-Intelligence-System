#!/bin/bash
# run.sh <clip_folder> [store_id] [output_json]

CLIP_FOLDER=$1
STORE_ID=${2:-STORE_BLR_002}
OUTPUT_JSON=${3:-out/events.json}

if [ -z "$CLIP_FOLDER" ]; then
  echo "Usage: bash run.sh <clip_folder> [store_id] [output_json]"
  exit 1
fi

# Detect python executable in venv or system
PYTHON_EXE="python"
if [ -f "./venv/Scripts/python.exe" ]; then
  PYTHON_EXE="./venv/Scripts/python.exe"
elif [ -f "./venv/bin/python" ]; then
  PYTHON_EXE="./venv/bin/python"
fi

echo "Step 1: Running detection & tracking (detect.py) on $CLIP_FOLDER..."
$PYTHON_EXE pipeline/detect.py --input "$CLIP_FOLDER" --store-id "$STORE_ID" --output out/tracks.jsonl

echo "Step 2: Running Re-ID matching & staff detection (tracker.py)..."
$PYTHON_EXE pipeline/tracker.py --input "$CLIP_FOLDER" --tracks out/tracks.jsonl --output out/visitor_tracks.jsonl

echo "Step 3: Generating event schema & emission (emit.py)..."
$PYTHON_EXE pipeline/emit.py --input out/visitor_tracks.jsonl --output "$OUTPUT_JSON"

echo "Done! Events emitted to $OUTPUT_JSON"
