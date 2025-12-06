#!/bin/bash
#
# Transform JSON entries by minifying CSS class names and IDs
#
# Usage:
#   ./run_transform_classname.sh [OPTIONS]
#
#   bash transform_from_json_classname/run_transform_classname.sh
#   bash transform_from_json_classname/run_transform_classname.sh --safelist "active,selected"
#
# Options:
#   --input PATH                 Input JSON file path
#   --output PATH                Output JSON file path
#   --safelist CLASSES           Comma-separated list of class/ID names to preserve
#   --no-html-minify             Disable HTML minification (keep formatting)
#   --help                       Show this help message
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json"
DEFAULT_OUTPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_classname_minified.json"

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"
SAFELIST=""
NO_HTML_MINIFY=""

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
        --safelist)
            SAFELIST="$2"
            shift 2
            ;;
        --no-html-minify)
            NO_HTML_MINIFY="--no-html-minify"
            shift
            ;;
        --help)
            echo "Transform JSON entries by minifying CSS class names and IDs"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH                 Input JSON file path (default: $DEFAULT_INPUT)"
            echo "  --output PATH                Output JSON file path (default: $DEFAULT_OUTPUT)"
            echo "  --safelist CLASSES           Comma-separated list of class/ID names to preserve"
            echo "  --no-html-minify             Disable HTML minification (keep formatting)"
            echo "  --help                       Show this help message"
            echo ""
            echo "Default configuration:"
            echo "  Input:      $DEFAULT_INPUT"
            echo "  Output:     $DEFAULT_OUTPUT"
            echo ""
            echo "Examples:"
            echo "  # Use default settings"
            echo "  $0"
            echo ""
            echo "  # Preserve specific class names"
            echo "  $0 --safelist \"active,selected,highlight\""
            echo ""
            echo "  # Disable HTML minification"
            echo "  $0 --no-html-minify"
            echo ""
            echo "  # Custom input/output paths"
            echo "  $0 --input /path/to/input.json --output /path/to/output.json"
            echo ""
            echo "Features:"
            echo "  - Minifies CSS class names and IDs (a, b, c, aa, ab, ...)"
            echo "  - Replaces names in both HTML attributes and CSS selectors"
            echo "  - Avoids reserved keywords (ad, ads, banner, if, do, for)"
            echo "  - Optionally preserves specific class/ID names (safelist)"
            echo "  - Optional HTML minification for additional size reduction"
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
echo "JSON Class Name Minification Script"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Input file: $INPUT"
echo "Output file: $OUTPUT"
if [ -n "$SAFELIST" ]; then
    echo "Safelist: $SAFELIST"
fi
if [ -n "$NO_HTML_MINIFY" ]; then
    echo "HTML minification: DISABLED"
else
    echo "HTML minification: ENABLED"
fi
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."

# Check if beautifulsoup4 is installed
if ! python3 -c "import bs4" 2>/dev/null; then
    echo "⚠ Warning: beautifulsoup4 not found. Installing..."
    pip install beautifulsoup4
    echo ""
fi

# Check if lxml is installed (recommended parser for BeautifulSoup)
if ! python3 -c "import lxml" 2>/dev/null; then
    echo "⚠ Warning: lxml not found (recommended for faster parsing). Installing..."
    pip install lxml
    echo ""
fi

# Check if htmlmin is installed (optional but recommended)
if ! python3 -c "import htmlmin" 2>/dev/null; then
    echo "⚠ Note: htmlmin not found (optional for HTML minification). Installing..."
    pip install htmlmin
    echo ""
fi

echo "Dependencies checked ✓"
echo ""

# Build the command
CMD="python3 \"$SCRIPT_DIR/transform_classname.py\" --input \"$INPUT\" --output \"$OUTPUT\""

if [ -n "$SAFELIST" ]; then
    CMD="$CMD --safelist \"$SAFELIST\""
fi

if [ -n "$NO_HTML_MINIFY" ]; then
    CMD="$CMD $NO_HTML_MINIFY"
fi

# Run the transformation script
echo "Starting class name minification..."
echo ""

eval $CMD

echo ""
echo "========================================"
echo "Transformation complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Transformed data: $OUTPUT"
if [ -f "${OUTPUT}.skipped.json" ]; then
    echo "  Skip report:      ${OUTPUT}.skipped.json"
    echo ""
    echo "Note: Some entries were skipped during transformation."
    echo "      Check the skip report for details."
fi
echo ""
echo "Next steps:"
echo "  - Review the transformed dataset"
if [ -f "${OUTPUT}.skipped.json" ]; then
    echo "  - Review the skip report to understand skipped entries"
fi
echo "  - Compare file sizes (original vs. minified)"
echo "  - Use the minified data for training or deployment"
echo ""
