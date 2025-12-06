#!/bin/bash
#
# Transform JSON entries to HUMAN-READABLE format
#
# Ultra-simple usage: only --input and --output required
# All prettification features enabled by default
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json"
DEFAULT_OUTPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_human_read.json"

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"

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
        --help)
            echo "Transform JSON to human-readable format"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH          Input JSON file path (default: $DEFAULT_INPUT)"
            echo "  --output PATH         Output JSON file path (default: $DEFAULT_OUTPUT)"
            echo "  --help                Show this help message"
            echo ""
            echo "Features (ALL ENABLED by default):"
            echo "  ✓ HTML prettification (proper indentation)"
            echo "  ✓ CSS prettification (formatted rules and properties)"
            echo "  ✓ Pretty-printed JSON output (2-space indentation)"
            echo ""
            echo "Example:"
            echo "  $0 --input data_compressed.json --output data_readable.json"
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
echo "Human-Readable Formatting Script"
echo "========================================"
echo "Input file:  $INPUT"
echo "Output file: $OUTPUT"
echo ""
echo "Formatting features (ALL ENABLED):"
echo "  ✓ HTML prettification"
echo "  ✓ CSS prettification"
echo "  ✓ Pretty JSON output"
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."

if ! python3 -c "import bs4" 2>/dev/null; then
    echo "⚠ Warning: beautifulsoup4 not found. Installing..."
    pip install beautifulsoup4
    echo ""
fi

if ! python3 -c "import lxml" 2>/dev/null; then
    echo "⚠ Warning: lxml not found. Installing..."
    pip install lxml
    echo ""
fi

echo "Dependencies checked ✓"
echo ""

# Run the transformation script
echo "Starting human-readable formatting transformation..."
echo ""

python3 "$SCRIPT_DIR/transform_human_read.py" --input "$INPUT" --output "$OUTPUT"

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
echo "Next steps:"
echo "  - Review formatted HTML/CSS"
echo "  - Compare with original (minified) version"
echo "  - Use readable data for debugging/development"
echo ""
