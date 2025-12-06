#!/bin/bash
#
# Filter JSON entries where GPT response exceeds token limit
#
# Usage:
#   ./run_filter_json.sh [OPTIONS]
#
#   bash transform_from_json/run_filter_json.sh --tokenizer qwen
#   bash transform_from_json/run_filter_json.sh --tokenizer tiktoken --max-tokens 10000
#
# Options:
#   --tokenizer {tiktoken|qwen}  Tokenizer to use (default: qwen)
#   --max-tokens N               Maximum token count (default: 8000)
#   --input PATH                 Input JSON file path
#   --output PATH                Output JSON file path
#   --help                       Show this help message
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_human_read.json"
DEFAULT_OUTPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_human_read_8000.json"
DEFAULT_TOKENIZER="qwen"
DEFAULT_MAX_TOKENS="8000"

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"
TOKENIZER="$DEFAULT_TOKENIZER"
MAX_TOKENS="$DEFAULT_MAX_TOKENS"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --tokenizer)
            TOKENIZER="$2"
            shift 2
            ;;
        --max-tokens)
            MAX_TOKENS="$2"
            shift 2
            ;;
        --input)
            INPUT="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --help)
            echo "Filter JSON entries where GPT response exceeds token limit"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --tokenizer {tiktoken|qwen}  Tokenizer to use (default: $DEFAULT_TOKENIZER)"
            echo "  --max-tokens N               Maximum token count (default: $DEFAULT_MAX_TOKENS)"
            echo "  --input PATH                 Input JSON file path"
            echo "  --output PATH                Output JSON file path"
            echo "  --help                       Show this help message"
            echo ""
            echo "Default configuration:"
            echo "  Tokenizer:  $DEFAULT_TOKENIZER"
            echo "  Max tokens: $DEFAULT_MAX_TOKENS"
            echo "  Input:      $DEFAULT_INPUT"
            echo "  Output:     $DEFAULT_OUTPUT"
            echo ""
            echo "Examples:"
            echo "  # Use default settings (Qwen tokenizer, 8000 tokens)"
            echo "  $0"
            echo ""
            echo "  # Use tiktoken instead"
            echo "  $0 --tokenizer tiktoken"
            echo ""
            echo "  # Custom token limit"
            echo "  $0 --max-tokens 10000"
            echo ""
            echo "  # Custom input/output paths"
            echo "  $0 --input /path/to/input.json --output /path/to/output.json"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate tokenizer choice
if [[ "$TOKENIZER" != "tiktoken" && "$TOKENIZER" != "qwen" ]]; then
    echo "Error: Invalid tokenizer '$TOKENIZER'. Must be 'tiktoken' or 'qwen'"
    exit 1
fi

echo "========================================"
echo "JSON Token Filter Script"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Tokenizer: $TOKENIZER"
echo "Max tokens: $MAX_TOKENS"
echo "Input file: $INPUT"
echo "Output file: $OUTPUT"
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check if tiktoken is installed
echo "Checking dependencies..."
if ! python3 -c "import tiktoken" 2>/dev/null; then
    echo "⚠ Warning: tiktoken not found. Installing..."
    pip install tiktoken
    echo ""
fi

# Check if transformers is needed and installed
if [[ "$TOKENIZER" == "qwen" ]]; then
    if ! python3 -c "import transformers" 2>/dev/null; then
        echo "⚠ Warning: transformers not found (required for Qwen tokenizer). Installing..."
        pip install transformers
        echo ""
    fi
fi

echo "Dependencies checked ✓"
echo ""

# Run the filtering script
echo "Starting JSON filtering..."
echo ""

python3 "$SCRIPT_DIR/transform_json.py" \
    --tokenizer "$TOKENIZER" \
    --max-tokens "$MAX_TOKENS" \
    --input "$INPUT" \
    --output "$OUTPUT"

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Filtered data: $OUTPUT"
echo ""
echo "Next steps:"
echo "  - Review the filtered dataset"
echo "  - Use the filtered data for training"
echo ""
