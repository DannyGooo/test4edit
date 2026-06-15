#!/bin/bash
#
# Transform ms_swift JSONL to qwen_series JSON format
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

DEFAULT_INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000.jsonl"
DEFAULT_OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_qwen_series.jsonl"

INPUT="${1:-$DEFAULT_INPUT}"
OUTPUT="${2:-$DEFAULT_OUTPUT}"
NUM_SAMPLES="${3:-0}"

echo "========================================"
echo "Transform ms_swift JSONL -> qwen_series JSON"
echo "========================================"
echo "Input:  $INPUT"
echo "Output: $OUTPUT"
if [ "$NUM_SAMPLES" -gt 0 ] 2>/dev/null; then
    echo "Samples: $NUM_SAMPLES"
else
    echo "Samples: all"
fi
echo ""

if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

python3 "$SCRIPT_DIR/transform_to_qwen_series.py" --input "$INPUT" --output "$OUTPUT" --num_samples "$NUM_SAMPLES"

echo ""
echo "Done. Output: $OUTPUT"
