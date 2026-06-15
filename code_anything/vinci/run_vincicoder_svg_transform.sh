#!/bin/bash

# Convert VinciCoder img2svg parquet to code-anything format

INPUT_PARQUET="/home/liu282/scratch3/projects/vision_to_code/dataset/baseline/vincicoder/download/VinciCoder-1.6M-SFT/img2svg_1.parquet"
OUTPUT_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/baseline/vincicoder/codeAnything_series"

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run the transformation
python3 "${SCRIPT_DIR}/vincicoder_parquet_to_code_anything.py" \
    --input "${INPUT_PARQUET}" \
    --output "${OUTPUT_DIR}" \
    "$@"
