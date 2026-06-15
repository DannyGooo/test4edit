#!/bin/bash
#
# Keep top-N JSON entries ranked by HTML value density:
#   score = value-containing tags / total HTML tags
#
# Usage:
#   bash transform_from_json/run_filter_value_ratio.sh --top-n 10000
#   bash transform_from_json/run_filter_value_ratio.sh --top-n 5000 --min-total-tags 20 --require-html
#
# Options:
#   --top-n N                Number of samples to keep (required; use -1 to keep all)
#   --min-total-tags N       Only consider entries with at least N HTML tags (default: 0)
#   --require-html           Discard entries with 0 HTML tags
#   --input PATH             Input JSON file path
#   --output PATH            Output JSON file path
#   --scores-output PATH     Optional: write all scored entries (id/ratio/tags) to PATH
#   --help                   Show this help message
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths (kept consistent with other scripts in this repo)
DEFAULT_INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo/coco_webdataset_200k_8000.json"
DEFAULT_OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo/coco_webdataset_200k_8000_value_ratio_50k.json"
DEFAULT_MIN_TOTAL_TAGS="0"

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"
TOP_N=""
MIN_TOTAL_TAGS="$DEFAULT_MIN_TOTAL_TAGS"
REQUIRE_HTML="false"
SCORES_OUTPUT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --top-n)
            TOP_N="$2"
            shift 2
            ;;
        --min-total-tags)
            MIN_TOTAL_TAGS="$2"
            shift 2
            ;;
        --require-html)
            REQUIRE_HTML="true"
            shift 1
            ;;
        --input)
            INPUT="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --scores-output)
            SCORES_OUTPUT="$2"
            shift 2
            ;;
        --help)
            echo "Keep top-N JSON entries ranked by HTML value density"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --top-n N                Number of samples to keep (required; use -1 to keep all)"
            echo "  --min-total-tags N       Only consider entries with at least N HTML tags (default: $DEFAULT_MIN_TOTAL_TAGS)"
            echo "  --require-html           Discard entries with 0 HTML tags"
            echo "  --input PATH             Input JSON file path"
            echo "  --output PATH            Output JSON file path"
            echo "  --scores-output PATH     Optional: write per-entry scores JSON to PATH"
            echo "  --help                   Show this help message"
            echo ""
            echo "Default configuration:"
            echo "  Input:      $DEFAULT_INPUT"
            echo "  Output:     $DEFAULT_OUTPUT"
            echo ""
            echo "Examples:"
            echo "  $0 --top-n 10000"
            echo "  $0 --top-n 5000 --min-total-tags 20 --require-html"
            echo "  $0 --top-n -1 --scores-output /tmp/all_scores.json"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

if [[ -z "$TOP_N" ]]; then
    echo "Error: --top-n is required"
    echo "Use --help for usage information"
    exit 1
fi

echo "========================================"
echo "HTML Value Density Filter Script"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Top N: $TOP_N"
echo "Min total tags: $MIN_TOTAL_TAGS"
echo "Require HTML: $REQUIRE_HTML"
echo "Input file: $INPUT"
echo "Output file: $OUTPUT"
if [[ -n "$SCORES_OUTPUT" ]]; then
  echo "Scores output: $SCORES_OUTPUT"
fi
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

echo "Checking dependencies..."
if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not found in PATH"
    exit 1
fi
echo "Dependencies checked ✓"
echo ""

echo "Starting filtering..."
echo ""

CMD=(python3 "$SCRIPT_DIR/filter_value_ratio.py"
    --top-n "$TOP_N"
    --min-total-tags "$MIN_TOTAL_TAGS"
    --input "$INPUT"
    --output "$OUTPUT"
)

if [[ "$REQUIRE_HTML" == "true" ]]; then
    CMD+=(--require-html)
fi

if [[ -n "$SCORES_OUTPUT" ]]; then
    CMD+=(--scores-output "$SCORES_OUTPUT")
fi

"${CMD[@]}"

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Filtered data: $OUTPUT"
if [[ -n "$SCORES_OUTPUT" ]]; then
  echo "  Scores:        $SCORES_OUTPUT"
fi
echo ""

