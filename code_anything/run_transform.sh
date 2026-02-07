#!/bin/bash

# Transform dataset to code-anything format

INPUT_JSON="/home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo/coco_webdataset_filtered_qwen_8000-100-remain-50000.json"
INPUT_IMAGE_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo"
OUTPUT_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/codeAnything"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run the transformation
python3 "${SCRIPT_DIR}/transform_to_code_anything.py" \
    --input "${INPUT_JSON}" \
    --input-image-dir "${INPUT_IMAGE_DIR}" \
    --output "${OUTPUT_DIR}" \
    "$@"
