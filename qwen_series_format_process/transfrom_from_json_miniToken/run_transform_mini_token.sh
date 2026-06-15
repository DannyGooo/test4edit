#!/bin/bash
#
# Transform JSON entries with MAXIMUM compression
#
# Ultra-simple usage: only --input and --output required
# All compression features enabled by default
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json"
DEFAULT_OUTPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_mini_token.json"

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
            echo "Transform JSON with MAXIMUM compression"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH          Input JSON file path (default: $DEFAULT_INPUT)"
            echo "  --output PATH         Output JSON file path (default: $DEFAULT_OUTPUT)"
            echo "  --help                Show this help message"
            echo ""
            echo "Features (ALL ENABLED by default):"
            echo "  ✓ CSS class/ID name minification (a, b, c, ...)"
            echo "  ✓ Remove unused CSS rules (PurgeCSS)"
            echo "  ✓ CSS content minification (whitespace, comments, colors)"
            echo "  ✓ HTML structure minification"
            echo "  ✓ Inline small CSS blocks (< 100 chars)"
            echo "  ✓ Compact JSON output (no indentation)"
            echo ""
            echo "Example:"
            echo "  $0 --input data.json --output data_compressed.json"
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
echo "MAXIMUM Compression Script"
echo "========================================"
echo "Input file:  $INPUT"
echo "Output file: $OUTPUT"
echo ""
echo "Compression features (ALL ENABLED):"
echo "  ✓ Class/ID minification"
echo "  ✓ CSS purging (remove unused)"
echo "  ✓ CSS minification"
echo "  ✓ HTML minification"
echo "  ✓ Small CSS inlining"
echo "  ✓ Compact JSON output"
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

if ! python3 -c "import htmlmin" 2>/dev/null; then
    echo "⚠ Note: htmlmin not found. Installing..."
    pip install htmlmin
    echo ""
fi

echo "Dependencies checked ✓"
echo ""

# Run the transformation script
echo "Starting MAXIMUM compression transformation..."
echo ""

python3 "$SCRIPT_DIR/transform_mini_token.py" --input "$INPUT" --output "$OUTPUT"

echo ""
echo "========================================"
echo "Transformation complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Compressed data: $OUTPUT"
if [ -f "${OUTPUT}.skipped.json" ]; then
    echo "  Skip report:     ${OUTPUT}.skipped.json"
    echo ""
    echo "Note: Some entries were skipped."
    echo "      Check the skip report for details."
fi
echo ""
echo "Next steps:"
echo "  - Compare file sizes (ls -lh)"
echo "  - Review skip report if exists"
echo "  - Use compressed data for training/deployment"
echo ""
