#!/bin/bash
#
# Fast parallel filter for JSONL entries whose images are solid-color.
# Companion to run_filter_solid_color_images.sh -- same arguments plus
# --workers, defaults to all CPU cores.
#
# Usage:
#   bash ms_swift_format_process/filter/run_filter_solid_color_images_fast.sh \
#     --jsonl /path/to/data.jsonl --image-root /path/to/tars
#
# Options:
#   --jsonl PATH           Input JSONL file path (required)
#   --image-root PATH      Directory containing tar files (required)
#   --output PATH          Output JSONL file path (default: <input>_solid_filtered.jsonl)
#   --std-threshold N      Std-dev threshold for solid-color detection (default: 0)
#   --workers N            Worker processes (default: all CPU cores)
#   --verbose              Print every filtered/error image
#   --help                 Show this help message
#
# Example matching the slow run:
#   bash ms_swift_format_process/filter/run_filter_solid_color_images_fast.sh \
#     --jsonl /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_human_read_10K_1280_token_8000.jsonl \
#     --output /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_human_read_10K_1280_token_8000_nonsolid.jsonl \
#     --image-root /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/tars

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

DEFAULT_STD_THRESHOLD="0"

JSONL=""
IMAGE_ROOT=""
OUTPUT=""
STD_THRESHOLD="$DEFAULT_STD_THRESHOLD"
WORKERS=""
VERBOSE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --jsonl)
            JSONL="$2"; shift 2 ;;
        --image-root)
            IMAGE_ROOT="$2"; shift 2 ;;
        --output)
            OUTPUT="$2"; shift 2 ;;
        --std-threshold)
            STD_THRESHOLD="$2"; shift 2 ;;
        --workers)
            WORKERS="$2"; shift 2 ;;
        --verbose)
            VERBOSE="--verbose"; shift ;;
        --help)
            sed -n '2,30p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if [ -z "$JSONL" ]; then
    echo "Error: --jsonl is required."
    exit 1
fi
if [ -z "$IMAGE_ROOT" ]; then
    echo "Error: --image-root is required."
    exit 1
fi
if [ ! -f "$JSONL" ]; then
    echo "Error: Input file not found: $JSONL"
    exit 1
fi
if [ ! -d "$IMAGE_ROOT" ]; then
    echo "Error: Image root directory not found: $IMAGE_ROOT"
    exit 1
fi

# Resolve worker count default
if [ -z "$WORKERS" ]; then
    if command -v nproc >/dev/null 2>&1; then
        WORKERS="$(nproc)"
    else
        WORKERS="8"
    fi
fi

echo "========================================"
echo "JSONL Solid-Color Image Filter (FAST)"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Std-dev threshold: $STD_THRESHOLD"
echo "Workers: $WORKERS"
echo "Input file: $JSONL"
echo "Image root: $IMAGE_ROOT"
if [ -n "$OUTPUT" ]; then
    echo "Output file: $OUTPUT"
else
    echo "Output file: (auto-generated)"
fi
echo ""

echo "Checking dependencies..."
if ! python3 -c "from PIL import Image" 2>/dev/null; then
    echo "Warning: Pillow not found. Installing..."
    pip install --user Pillow
fi
if ! python3 -c "import numpy" 2>/dev/null; then
    echo "Warning: numpy not found. Installing..."
    pip install --user numpy
fi
echo "Dependencies checked."
echo ""

CMD=(python3 "$SCRIPT_DIR/filter_solid_color_images_fast.py"
     --jsonl "$JSONL"
     --image_root "$IMAGE_ROOT"
     --std-threshold "$STD_THRESHOLD"
     --workers "$WORKERS")
if [ -n "$OUTPUT" ]; then
    CMD+=(--output "$OUTPUT")
fi
if [ -n "$VERBOSE" ]; then
    CMD+=("$VERBOSE")
fi

echo "Starting solid-color image filtering (parallel)..."
echo ""
"${CMD[@]}"

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
