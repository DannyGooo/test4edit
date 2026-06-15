#!/bin/bash
#
# Transform ms_swift JSONL to code-anything format (3_web).
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/fixed/meta_data_web_200k_fixed.jsonl"
TAR_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/tars"
OUTPUT_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/code_anything"

echo "========================================"
echo "Transform ms_swift JSONL -> code-anything"
echo "========================================"
echo "Input:   $INPUT"
echo "Tar dir: $TAR_DIR"
echo "Output:  $OUTPUT_DIR"
echo ""

python3 "${SCRIPT_DIR}/transform_to_code_anything.py" \
    --input "${INPUT}" \
    --tar-dir "${TAR_DIR}" \
    --output "${OUTPUT_DIR}" \
    "$@"

echo ""
echo "Done. Output base: ${OUTPUT_DIR}/code-anything/3_web"
