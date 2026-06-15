#!/bin/bash

# Convert MCD dataset (html + chart) to code-anything format

JSON_DATA="/home/liu282/scratch3/projects/vision_to_code/dataset/baseline/MultimodalCodingDataset/mcd_598k.json"
IMAGES_ZIP="/home/liu282/scratch3/projects/vision_to_code/dataset/baseline/MultimodalCodingDataset/mcd_images.zip"
OUTPUT_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/codeAnything"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run the transformation
python3 "${SCRIPT_DIR}/mcd_to_code_anything.py" \
    --json-data "${JSON_DATA}" \
    --images-zip "${IMAGES_ZIP}" \
    --output "${OUTPUT_DIR}" \
    --category both \
    "$@"
