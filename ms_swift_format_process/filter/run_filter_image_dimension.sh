#!/bin/bash
#
# Filter JSONL entries whose image dimensions exceed a threshold
#
# Usage:
#   ./run_filter_image_height.sh [OPTIONS]
#
#   bash ms_swift_format_process/filter/run_filter_image_height.sh \
#     --jsonl /path/to/data.jsonl --image-root /path/to/tars
#
# Options:
#   --jsonl PATH         Input JSONL file path (required)
#   --image-root PATH    Directory containing tar files (required)
#   --output PATH        Output JSONL file path
#   --max-height N       Maximum image height in pixels (default: 10000)
#   --max-width N        Maximum image width in pixels (default: no limit)
#   --help               Show this help message
#  bash ms_swift_format_process/filter/run_filter_image_dimension.sh  --jsonl /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000.jsonl --output /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800.jsonl --max-width 1280 --max-height 10000 --image-root /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/tars


set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Defaults
DEFAULT_MAX_HEIGHT="10000"

JSONL=""
IMAGE_ROOT=""
OUTPUT=""
MAX_HEIGHT="$DEFAULT_MAX_HEIGHT"
MAX_WIDTH="1280"

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
        --max-height)
            MAX_HEIGHT="$2"
            shift 2
            ;;
        --max-width)
            MAX_WIDTH="$2"
            shift 2
            ;;
        --help)
            echo "Filter JSONL entries whose image dimensions exceed a threshold"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --jsonl PATH         Input JSONL file path (required)"
            echo "  --image-root PATH    Directory containing tar files (required)"
            echo "  --output PATH        Output JSONL file path (default: <input>_dim_filtered.jsonl)"
            echo "  --max-height N       Maximum image height in pixels (default: $DEFAULT_MAX_HEIGHT)"
            echo "  --max-width N        Maximum image width in pixels (default: no limit)"
            echo "  --help               Show this help message"
            echo ""
            echo "Examples:"
            echo "  $0 --jsonl /path/to/data.jsonl --image-root /path/to/tars"
            echo "  $0 --jsonl /path/to/data.jsonl --image-root /path/to/tars --max-height 5000 --max-width 1280"
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
echo "JSONL Image Dimension Filter"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Max height: ${MAX_HEIGHT}px"
if [ -n "$MAX_WIDTH" ]; then
    echo "Max width:  ${MAX_WIDTH}px"
else
    echo "Max width:  (no limit)"
fi
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
CMD="python3 $SCRIPT_DIR/filter_image_dimension.py --jsonl $JSONL --image_root $IMAGE_ROOT --max-height $MAX_HEIGHT"
if [ -n "$MAX_WIDTH" ]; then
    CMD="$CMD --max-width $MAX_WIDTH"
fi
if [ -n "$OUTPUT" ]; then
    CMD="$CMD --output $OUTPUT"
fi

echo "Starting image dimension filtering..."
echo ""
eval $CMD

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
