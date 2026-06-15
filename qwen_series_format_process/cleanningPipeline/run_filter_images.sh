#!/bin/bash
#
# Filter JSON entries with corrupted or solid-color images
#
# Usage:
#   ./run_filter_images.sh [OPTIONS]
#
#   bash cleanningPipeline/run_filter_images.sh --input /path/to/input.json --output /path/to/output.json
#
# Options:
#   --input PATH            Input JSON file path (required)
#   --output PATH           Output JSON file path (required)
#   --tars-dir PATH         Directory with images-*.tar files (tar mode)
#   --image-base-dir PATH   Base directory for resolving image paths (file mode)
#   --std-threshold N       Std dev threshold for solid-color detection (default: 0)
#   --help                  Show this help message
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Initialize variables
INPUT=""
OUTPUT=""
TARS_DIR=""
IMAGE_BASE_DIR=""
STD_THRESHOLD="0"

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
        --image-base-dir)
            IMAGE_BASE_DIR="$2"
            shift 2
            ;;
        --std-threshold)
            STD_THRESHOLD="$2"
            shift 2
            ;;
        --help)
            echo "Filter JSON entries with corrupted or solid-color images"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH            Input JSON file path (required)"
            echo "  --output PATH           Output JSON file path (required)"
            echo "  --tars-dir PATH         Directory with images-*.tar files (tar mode)"
            echo "  --image-base-dir PATH   Base directory for resolving image paths (file mode)"
            echo "  --std-threshold N       Std dev threshold for solid-color detection (default: 0)"
            echo "  --help                  Show this help message"
            echo ""
            echo "Examples:"
            echo "  # File mode (loose images on disk):"
            echo "  $0 --input /path/to/data.json --output /path/to/filtered.json"
            echo ""
            echo "  # Tar mode (images in tar archives):"
            echo "  $0 --input /path/to/meta_data_web.json --output /path/to/filtered.json --tars-dir /path/to/tars"
            echo ""
            echo "  $0 --input /path/to/data.json --output /path/to/filtered.json --std-threshold 1.0"
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
if [ -z "$INPUT" ]; then
    echo "Error: --input is required"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$OUTPUT" ]; then
    echo "Error: --output is required"
    echo "Use --help for usage information"
    exit 1
fi

echo "========================================"
echo "Image Quality Filter Script"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Input file:       $INPUT"
echo "Output file:      $OUTPUT"
if [ -n "$TARS_DIR" ]; then
    echo "Tars directory:   $TARS_DIR (tar mode)"
elif [ -n "$IMAGE_BASE_DIR" ]; then
    echo "Image base dir:   $IMAGE_BASE_DIR (file mode)"
else
    echo "Mode:             file mode (default)"
fi
echo "Std threshold:    $STD_THRESHOLD"
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check dependencies
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

if ! python3 -c "import numpy" 2>/dev/null; then
    echo "Warning: numpy not found. Installing..."
    pip install --user numpy
    echo ""
fi

echo "Dependencies checked."
echo ""

# Build command
CMD="python3 \"$SCRIPT_DIR/filter_images.py\" --input \"$INPUT\" --output \"$OUTPUT\" --std-threshold $STD_THRESHOLD"

if [ -n "$TARS_DIR" ]; then
    CMD="$CMD --tars-dir \"$TARS_DIR\""
fi

if [ -n "$IMAGE_BASE_DIR" ]; then
    CMD="$CMD --image-base-dir \"$IMAGE_BASE_DIR\""
fi

# Run the filtering script
echo "Starting image quality filtering..."
echo ""

eval $CMD

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Filtered data: $OUTPUT"
echo ""
