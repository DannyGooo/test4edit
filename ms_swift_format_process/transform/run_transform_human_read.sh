#!/bin/bash
#
# Transform ms_swift JSONL entries to HUMAN-READABLE format
#
# Ultra-simple usage: only --input and --output required
# All prettification features enabled by default
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web.jsonl"
DEFAULT_OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_human_read.jsonl"

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"
NUM_SAMPLES=0

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
        --num_samples)
            NUM_SAMPLES="$2"
            shift 2
            ;;
        --help)
            echo "Transform ms_swift JSONL to human-readable format"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH          Input JSONL file path (default: $DEFAULT_INPUT)"
            echo "  --output PATH         Output JSONL file path (default: $DEFAULT_OUTPUT)"
            echo "  --num_samples N       Number of samples to process (0 = all, default: 0)"
            echo "  --help                Show this help message"
            echo ""
            echo "Features (ALL ENABLED by default):"
            echo "  - HTML prettification (proper indentation)"
            echo "  - CSS prettification (formatted rules and properties)"
            echo ""
            echo "Example:"
            echo "  $0 --input data.jsonl --output data_readable.jsonl"
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
echo "Human-Readable Formatting Script (ms_swift)"
echo "========================================"
echo "Input file:  $INPUT"
echo "Output file: $OUTPUT"
if [ "$NUM_SAMPLES" -gt 0 ]; then
    echo "Num samples: $NUM_SAMPLES"
else
    echo "Num samples: all"
fi
echo ""
echo "Formatting features (ALL ENABLED):"
echo "  - HTML prettification"
echo "  - CSS prettification"
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."

if ! python3 -c "import bs4" 2>/dev/null; then
    echo "Warning: beautifulsoup4 not found. Installing..."
    pip install beautifulsoup4
    echo ""
fi

if ! python3 -c "import lxml" 2>/dev/null; then
    echo "Warning: lxml not found. Installing..."
    pip install lxml
    echo ""
fi

echo "Dependencies checked."
echo ""

# Run the transformation script
echo "Starting human-readable formatting transformation..."
echo ""

python3 "$SCRIPT_DIR/transform_human_read.py" --input "$INPUT" --output "$OUTPUT" --num_samples "$NUM_SAMPLES"

echo ""
echo "========================================"
echo "Transformation complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Formatted data: $OUTPUT"
if [ -f "${OUTPUT}.skipped.json" ]; then
    echo "  Skip report:    ${OUTPUT}.skipped.json"
    echo ""
    echo "Note: Some entries were skipped."
    echo "      Check the skip report for details."
fi
echo ""
