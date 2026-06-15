#!/bin/bash
#
# Filter out samples with extreme image dimensions
#
# Usage:
#   bash filter/run_filter_image_dimensions.sh
#   bash filter/run_filter_image_dimensions.sh --max-width 1024 --max-height 4096 --max-aspect-ratio 5.0
#
# Options:
#   --input PATH                Input JSON file path
#   --output PATH               Output JSON file path
#   --tars-dir PATH             Directory containing tar files with images
#   --max-width N               Maximum width in pixels (default: 1280)
#   --max-height N              Maximum height in pixels (default: 5000)
#   --max-aspect-ratio N        Maximum aspect ratio max(w,h)/min(w,h) (default: 10.0)
#   --pure-color-threshold N    Max pixel std-dev for pure color detection (default: 1.0)
#   --help                      Show this help message
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset/advanced_web2m/newQwenFormat/qwen_series_original/meta_data_web_8000.json"
DEFAULT_OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset/advanced_web2m/newQwenFormat/qwen_series_original/meta_data_web_8000_dimfiltered.json"
DEFAULT_TARS_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/advanced_web2m/newQwenFormat/qwen_series_original/tars"
DEFAULT_MAX_WIDTH="1280"
DEFAULT_MAX_HEIGHT="20000"
DEFAULT_MAX_ASPECT_RATIO="10.0"
DEFAULT_PURE_COLOR_THRESHOLD="1.0"
DEFAULT_FILTERED_OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset/advanced_web2m/newQwenFormat/qwen_series_original/meta_data_web_8000_dimfiltered_rejected.json"

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"
TARS_DIR="$DEFAULT_TARS_DIR"
MAX_WIDTH="$DEFAULT_MAX_WIDTH"
MAX_HEIGHT="$DEFAULT_MAX_HEIGHT"
MAX_ASPECT_RATIO="$DEFAULT_MAX_ASPECT_RATIO"
PURE_COLOR_THRESHOLD="$DEFAULT_PURE_COLOR_THRESHOLD"
FILTERED_OUTPUT="$DEFAULT_FILTERED_OUTPUT"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --input)
            INPUT="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --tars-dir)
            TARS_DIR="$2"
            shift 2
            ;;
        --max-width)
            MAX_WIDTH="$2"
            shift 2
            ;;
        --max-height)
            MAX_HEIGHT="$2"
            shift 2
            ;;
        --max-aspect-ratio)
            MAX_ASPECT_RATIO="$2"
            shift 2
            ;;
        --pure-color-threshold)
            PURE_COLOR_THRESHOLD="$2"
            shift 2
            ;;
        --filtered-output)
            FILTERED_OUTPUT="$2"
            shift 2
            ;;
        --help)
            echo "Filter out samples with extreme image dimensions"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH              Input JSON file path"
            echo "  --output PATH             Output JSON file path"
            echo "  --tars-dir PATH           Directory with tar files containing images"
            echo "  --max-width N               Maximum width in pixels (default: $DEFAULT_MAX_WIDTH)"
            echo "  --max-height N              Maximum height in pixels (default: $DEFAULT_MAX_HEIGHT)"
            echo "  --max-aspect-ratio N        Maximum aspect ratio (default: $DEFAULT_MAX_ASPECT_RATIO)"
            echo "  --pure-color-threshold N    Max pixel std-dev for pure color (default: $DEFAULT_PURE_COLOR_THRESHOLD)"
            echo "  --filtered-output PATH      Write filtered-out entries with reasons to PATH"
            echo "  --help                      Show this help message"
            echo ""
            echo "Default configuration:"
            echo "  Input:            $DEFAULT_INPUT"
            echo "  Output:           $DEFAULT_OUTPUT"
            echo "  Tars dir:         $DEFAULT_TARS_DIR"
            echo "  Max width:              $DEFAULT_MAX_WIDTH"
            echo "  Max height:             $DEFAULT_MAX_HEIGHT"
            echo "  Max aspect ratio:       $DEFAULT_MAX_ASPECT_RATIO"
            echo "  Pure color threshold:   $DEFAULT_PURE_COLOR_THRESHOLD"
            echo "  Filtered output:        $DEFAULT_FILTERED_OUTPUT"
            echo ""
            echo "Examples:"
            echo "  $0"
            echo "  $0 --max-width 1024 --max-height 4096 --max-aspect-ratio 5.0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

echo "========================================"
echo "Image Dimension Filter Script"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Input file:       $INPUT"
echo "Output file:      $OUTPUT"
echo "Tars directory:   $TARS_DIR"
echo "Max width:              $MAX_WIDTH"
echo "Max height:             $MAX_HEIGHT"
echo "Max aspect ratio:       $MAX_ASPECT_RATIO"
echo "Pure color threshold:   $PURE_COLOR_THRESHOLD"
echo "Filtered output:        $FILTERED_OUTPUT"
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check if tars directory exists
if [ ! -d "$TARS_DIR" ]; then
    echo "Error: Tars directory not found: $TARS_DIR"
    exit 1
fi

echo "Checking dependencies..."
if ! python3 -c "import ijson" 2>/dev/null; then
    echo "Warning: ijson not found. Installing..."
    pip install --user ijson
    echo ""
fi

if ! python3 -c "from PIL import Image" 2>/dev/null; then
    echo "Warning: Pillow not found. Installing..."
    pip install --user Pillow
    echo ""
fi

echo "Dependencies checked"
echo ""

echo "Starting image dimension filtering..."
echo ""

python3 "$SCRIPT_DIR/filter_image_dimensions.py" \
    --input "$INPUT" \
    --output "$OUTPUT" \
    --tars-dir "$TARS_DIR" \
    --max-width "$MAX_WIDTH" \
    --max-height "$MAX_HEIGHT" \
    --max-aspect-ratio "$MAX_ASPECT_RATIO" \
    --pure-color-threshold "$PURE_COLOR_THRESHOLD" \
    --filtered-output "$FILTERED_OUTPUT"

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Kept data:        $OUTPUT"
echo "  Rejected data:    $FILTERED_OUTPUT"
echo ""
