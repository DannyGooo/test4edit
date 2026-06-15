#!/bin/bash
#
# Filter JSONL entries where assistant response exceeds token limit
#
# Usage:
#   ./run_filter_token_count.sh [OPTIONS]
#
#   bash ms_swift_format_process/filter/run_filter_token_count.sh --tokenizer tiktoken
#   bash ms_swift_format_process/filter/run_filter_token_count.sh --tokenizer qwen --max-tokens 10000
#
# Options:
#   --tokenizer {tiktoken|qwen}  Tokenizer to use (default: tiktoken)
#   --max-tokens N               Maximum token count (default: 8000)
#   --jsonl PATH                 Input JSONL file path
#   --output PATH                Output JSONL file path
#   --help                       Show this help message
# bash ms_swift_format_process/filter/run_filter_token_count.sh    --tokenizer qwen --max-tokens 8000 --jsonl /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_human_read_10K_1280.jsonl --output /home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_human_read_10K_1280_token_8000.jsonl

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Defaults
DEFAULT_TOKENIZER="tiktoken"
DEFAULT_MAX_TOKENS="8000"
DEFAULT_JSONL=""
DEFAULT_OUTPUT=""

# Initialize variables
TOKENIZER="$DEFAULT_TOKENIZER"
MAX_TOKENS="$DEFAULT_MAX_TOKENS"
JSONL="$DEFAULT_JSONL"
OUTPUT="$DEFAULT_OUTPUT"

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
        --jsonl)
            JSONL="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        --help)
            echo "Filter JSONL entries where assistant response exceeds token limit"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --tokenizer {tiktoken|qwen}  Tokenizer to use (default: $DEFAULT_TOKENIZER)"
            echo "  --max-tokens N               Maximum token count (default: $DEFAULT_MAX_TOKENS)"
            echo "  --jsonl PATH                 Input JSONL file path (required)"
            echo "  --output PATH                Output JSONL file path (default: <input>_filtered.jsonl)"
            echo "  --help                       Show this help message"
            echo ""
            echo "Examples:"
            echo "  # Use default settings (tiktoken, 8000 tokens)"
            echo "  $0 --jsonl /path/to/data.jsonl"
            echo ""
            echo "  # Use Qwen tokenizer with custom limit"
            echo "  $0 --tokenizer qwen --max-tokens 10000 --jsonl /path/to/data.jsonl"
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

# Validate tokenizer choice
if [[ "$TOKENIZER" != "tiktoken" && "$TOKENIZER" != "qwen" ]]; then
    echo "Error: Invalid tokenizer '$TOKENIZER'. Must be 'tiktoken' or 'qwen'"
    exit 1
fi

echo "========================================"
echo "JSONL Token Filter"
echo "========================================"
echo "Script directory: $SCRIPT_DIR"
echo "Tokenizer: $TOKENIZER"
echo "Max tokens: $MAX_TOKENS"
echo "Input file: $JSONL"
if [ -n "$OUTPUT" ]; then
    echo "Output file: $OUTPUT"
else
    echo "Output file: (auto-generated)"
fi
echo ""

# Check if input file exists
if [ ! -f "$JSONL" ]; then
    echo "Error: Input file not found: $JSONL"
    exit 1
fi

# Check dependencies
echo "Checking dependencies..."

PIP_USER_FLAG=""
if [ -z "${VIRTUAL_ENV:-}" ] && [ -z "${CONDA_PREFIX:-}" ]; then
    PIP_USER_FLAG="--user"
fi

if ! python3 -c "import tiktoken" 2>/dev/null; then
    echo "Warning: tiktoken not found. Installing..."
    pip install $PIP_USER_FLAG tiktoken
    echo ""
fi

if [[ "$TOKENIZER" == "qwen" ]]; then
    if ! python3 -c "import transformers" 2>/dev/null; then
        echo "Warning: transformers not found (required for Qwen tokenizer). Installing..."
        pip install $PIP_USER_FLAG transformers
        echo ""
    fi
fi

echo "Dependencies checked."
echo ""

# Build command
CMD="python3 $SCRIPT_DIR/filter_token_count.py --jsonl $JSONL --tokenizer $TOKENIZER --max-tokens $MAX_TOKENS"
if [ -n "$OUTPUT" ]; then
    CMD="$CMD --output $OUTPUT"
fi

# Run
echo "Starting token filtering..."
echo ""
eval $CMD

echo ""
echo "========================================"
echo "Filtering complete!"
echo "========================================"
