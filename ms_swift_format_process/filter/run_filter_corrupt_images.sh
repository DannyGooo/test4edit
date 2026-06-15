#!/bin/bash
#
# Scan a JSONL dataset for corrupt images and produce a cleaned version
#
# Usage:
#   ./run_filter_corrupt_images.sh [OPTIONS]
#
#   bash ms_swift_format_process/filter/run_filter_corrupt_images.sh \
#     --jsonl /path/to/data.jsonl --image-root /path/to/tars
#
# Options:
#   --jsonl PATH         Input JSONL file path (required)
#   --image-root PATH    Directory containing tar files (required)
#   --output PATH        Output JSONL file path (default: <input>_clean.jsonl)
#   --help               Show this help message
#
#  bash ms_swift_format_process/filter/run_filter_corrupt_images.sh \
#    --jsonl /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web.jsonl \
#    --output /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_noncorrupt.jsonl \
#    --image-root /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/tars


set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

JSONL=""
IMAGE_ROOT=""
OUTPUT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --jsonl)
            JSONL="$2"
            shift 2
            ;;
        --image-root)
            IMAGE_ROOT="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --help)
            echo "Scan a JSONL dataset for corrupt images and produce a cleaned version"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --jsonl PATH         Input JSONL file path (required)"
            echo "  --image-root PATH    Directory containing tar files (required)"
            echo "  --output PATH        Output JSONL file path (default: <input>_clean.jsonl)"
            echo "  --help               Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --jsonl /path/to/data.jsonl --image-root /path/to/tars"
            echo "  $0 --jsonl /path/to/data.jsonl --image-root /path/to/tars --output /path/to/clean.jsonl"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$JSONL" ]; then
    echo "Error: --jsonl is required. Use --help for usage information."
    exit 1
fi

if [ -z "$IMAGE_ROOT" ]; then
    echo "Error: --image-root is required. Use --help for usage information."
    exit 1
fi

echo "========================================"
echo "JSONL Corrupt Image Filter"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Input file: $JSONL"
echo "Image root: $IMAGE_ROOT"
if [ -n "$OUTPUT" ]; then
    echo "Output file: $OUTPUT"
else
    echo "Output file: (auto-generated)"
fi
echo ""

if [ ! -f "$JSONL" ]; then
    echo "Error: Input file not found: $JSONL"
    exit 1
fi

if [ ! -d "$IMAGE_ROOT" ]; then
    echo "Error: Image root directory not found: $IMAGE_ROOT"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."
if ! python3 -c "from PIL import Image" 2>/dev/null; then
    echo "Warning: Pillow not found. Installing..."
    pip install --user Pillow
    echo ""
fi
echo "Dependencies checked."
echo ""

# Build command
CMD="python3 $SCRIPT_DIR/filter_corrupt_images.py --jsonl $JSONL --image_root $IMAGE_ROOT"
if [ -n "$OUTPUT" ]; then
    CMD="$CMD --output $OUTPUT"
fi

echo "Starting corrupt image filtering..."
echo ""
eval $CMD

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
