#!/usr/bin/env bash
# Convert every code-anything per-category zip into ms-swift JSONL + tar shards.
#
# Each zip lands in its own ms_swift/<category_name>/ so a single zip's failure
# does not block others. --resume is passed every time, so re-running picks up
# any crashed partial run.
#
# Each emitted user prompt now includes a per-category task label, auto-resolved
# from the zip's category_name via CATEGORY_TO_TASK in code_anything_to_ms_swift.py
# (e.g. svg.zip -> "Task: SVG generation"). Pass --task "<custom>" to override
# for a single zip; see EXPECTED_OUTPUT.md for the full mapping.
#
# Usage:
#   ./run_code_anything_transform.sh                       # defaults below
#   SRC_DIR=/path/to/zips OUT_DIR=/path/to/out ./run_code_anything_transform.sh
#   ZIPS="svg.zip chart.zip" ./run_code_anything_transform.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_SCRIPT="${SCRIPT_DIR}/code_anything_to_ms_swift.py"

SRC_DIR="${SRC_DIR:-/home/liu282/scratch3/projects/vision_to_code/dataset/code_anything}"
OUT_DIR="${OUT_DIR:-/home/liu282/scratch3/projects/vision_to_code/dataset/code_anything/ms-swift-format}"

# Zip list mirrors gdown.sh, minus Mesh.zip (stores .mp4 videos; the converter
# is image-only). Override via ZIPS="a.zip b.zip" to process a subset, or to
# force Mesh.zip through with an external video decoder (will currently fail
# every entry as skipped_image_decode_error). See EXPECTED_OUTPUT.md.
ZIPS="${ZIPS:-svg.zip chart.zip 11_Formulation.zip 8_CircuiTikZ.zip cad.zip \
ABCNotation.zip Chemical.zip Chemical_smiles.zip Biological_structure.zip \
Math_graphics.zip Diagram_FlowChart.zip Table.zip Slide_Marp.zip Slide_Beamer.zip}"

mkdir -p "$OUT_DIR"

echo "[run] src=$SRC_DIR"
echo "[run] out=$OUT_DIR"

for z in $ZIPS; do
    zip_path="$SRC_DIR/$z"
    if [[ ! -f "$zip_path" ]]; then
        echo "[skip]   $z (not found at $zip_path)"
        continue
    fi
    echo "[start]  $z"
    if python "$PY_SCRIPT" --input-zip "$zip_path" -o "$OUT_DIR" --resume; then
        echo "[done]   $z"
    else
        rc=$?
        echo "[failed] $z (exit=$rc) - continuing with remaining zips"
    fi
done

echo "[run] all zips attempted; output under $OUT_DIR/ms_swift/"
