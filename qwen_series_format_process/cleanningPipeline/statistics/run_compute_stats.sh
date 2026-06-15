#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------
# Compute image & text statistics for a tar-based dataset
# ---------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Defaults
INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset/advanced_web2m/newQwenFormat/qwen_series_original/meta_data_web.json"
TARS_DIR="/home/liu282/scratch3/projects/vision_to_code/dataset/advanced_web2m/newQwenFormat/qwen_series_original/tars"
OUTPUT_DIR="cleanningPipeline/statistics"
TOKENIZER="Qwen/Qwen2.5-VL-7B-Instruct"

usage() {
    echo "Usage: $0 --input <json> --tars-dir <dir> --output-dir <dir> [--tokenizer <name>]"
    echo ""
    echo "  --input       Input JSON metadata file (required)"
    echo "  --tars-dir    Directory containing images-*.tar files (required)"
    echo "  --output-dir  Directory to write stats.jsonl + summary.json (required)"
    echo "  --tokenizer   HuggingFace tokenizer name (default: ${TOKENIZER})"
    exit 1
}

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --input)      INPUT="$2";      shift 2 ;;
        --tars-dir)   TARS_DIR="$2";   shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        --tokenizer)  TOKENIZER="$2";  shift 2 ;;
        -h|--help)    usage ;;
        *) echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$INPUT" || -z "$TARS_DIR" || -z "$OUTPUT_DIR" ]]; then
    echo "Error: --input, --tars-dir, and --output-dir are all required."
    usage
fi

# Check dependencies
echo "Checking Python dependencies..."
python3 -c "import ijson, PIL, numpy, transformers" 2>/dev/null || {
    echo "Missing one or more dependencies: ijson, Pillow, numpy, transformers"
    echo "Install with: pip install ijson Pillow numpy transformers"
    exit 1
}

echo "Running compute_stats.py..."
python3 "${SCRIPT_DIR}/compute_stats.py" \
    --input "$INPUT" \
    --tars-dir "$TARS_DIR" \
    --output-dir "$OUTPUT_DIR" \
    --tokenizer "$TOKENIZER"
