#!/bin/bash
#
# Transform webdataset tar files to Qwen3-VL training format
#
# Usage:
#   ./scripts/run_transform_webdataset.sh [OPTIONS]
#
#   bash scripts/run_transform_webdataset.sh --num-workers 60 --batch-size 500
# Options:
#   --max-samples N    Process only N samples (default: all)
#   --help             Show this help message
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Default paths
INPUT_PATTERN="/home/len091/scratch/vision2code/dataset/coco_image/webdataset_chunk_*.tar"
OUTPUT_JSON="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json"
OUTPUT_IMAGE_DIR="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/images"

# Parse arguments
MAX_SAMPLES=""
NUM_WORKERS=""
BATCH_SIZE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --max-samples)
            MAX_SAMPLES="--max-samples $2"
            shift 2
            ;;
        --num-workers)
            NUM_WORKERS="--num-workers $2"
            shift 2
            ;;
        --batch-size)
            BATCH_SIZE="--batch-size $2"
            shift 2
            ;;
        --help)
            echo "Transform webdataset tar files to Qwen3-VL training format"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --max-samples N    Process only N samples (default: all)"
            echo "  --num-workers N    Number of parallel workers (default: 8)"
            echo "  --batch-size N     Batch size for processing (default: 100)"
            echo "  --help             Show this help message"
            echo ""
            echo "Default paths:"
            echo "  Input:  $INPUT_PATTERN"
            echo "  Output JSON: $OUTPUT_JSON"
            echo "  Output images: $OUTPUT_IMAGE_DIR"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Change to project directory
cd "$PROJECT_DIR"

echo "========================================"
echo "Webdataset Transformation Script"
echo "========================================"
echo "Project directory: $PROJECT_DIR"
echo "Input pattern: $INPUT_PATTERN"
echo "Output JSON: $OUTPUT_JSON"
echo "Output images: $OUTPUT_IMAGE_DIR"
echo ""

# Check if webdataset is installed
if ! python -c "import webdataset" 2>/dev/null; then
    echo "⚠ Warning: webdataset not found. Installing..."
    pip install webdataset
    echo ""
fi

# Run the transformation script
echo "Starting transformation..."
echo ""

python -m src.dataset.transform_webdataset \
    --input-pattern "$INPUT_PATTERN" \
    --output-json "$OUTPUT_JSON" \
    --output-image-dir "$OUTPUT_IMAGE_DIR" \
    $MAX_SAMPLES \
    $NUM_WORKERS \
    $BATCH_SIZE

echo ""
echo "========================================"
echo "Transformation complete!"
echo "========================================"
echo ""
echo "You can now use the dataset for training:"
echo "  Data file: $OUTPUT_JSON"
echo "  Images: $OUTPUT_IMAGE_DIR"
echo ""
echo "Example training command:"
echo "  bash scripts/finetune_lora.sh"
echo ""
