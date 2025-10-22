#!/bin/bash

# Script to process all tar archives in the dataset directory
# This will create purged versions of all tar files

DATASET_DIR="/home/zha439/scratch/vision2code/dataset/screenshots_with_html_100k"
OUTPUT_DIR="./processed_dataset"
TEMP_DIR="/tmp/purgecss-temp-dataset"

echo "=========================================="
echo "PurgeCSS Dataset Processing"
echo "=========================================="
echo "Input directory: $DATASET_DIR"
echo "Output directory: $OUTPUT_DIR"
echo "Temporary directory: $TEMP_DIR"
echo ""
echo "This will process all 17 tar files (~100k HTML files)"
echo "Estimated time: 15-30 minutes"
echo ""

# Create output directory
mkdir -p "$OUTPUT_DIR"

# Run the processing script
node process-tar-archives.js \
  --output "$OUTPUT_DIR" \
  --temp "$TEMP_DIR" \
  --skip-existing \
  "$DATASET_DIR"

echo ""
echo "=========================================="
echo "Processing complete!"
echo "=========================================="
echo "Purged tar files are in: $OUTPUT_DIR"
echo ""
