#!/bin/bash
#
# Extract the first N samples from a ms_swift JSONL file into a new JSONL file.
#
# Ultra-simple usage: --input, --output, and --num_samples.
# If --output is omitted, a default path is derived from --input + the chosen N.
#

set -e  # Exit on error

# Script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Default paths
DEFAULT_INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_human_read_10K_1280_token_8000_nonsolid.jsonl"
DEFAULT_NUM_SAMPLES=400000

# Initialize variables
INPUT="$DEFAULT_INPUT"
OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/fixed/meta_data_web_human_read_10K_1280_token_8000_nonsolid_400k.jsonl"
NUM_SAMPLES="$DEFAULT_NUM_SAMPLES"

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
            echo "Extract the first N samples from a ms_swift JSONL file"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH          Input JSONL file path (default: $DEFAULT_INPUT)"
            echo "  --output PATH         Output JSONL file path"
            echo "                        (default: <input_dir>/<input_stem>_first<N>.jsonl)"
            echo "  --num_samples N       Number of samples to extract (default: $DEFAULT_NUM_SAMPLES, must be > 0)"
            echo "  --help                Show this help message"
            echo ""
            echo "Example:"
            echo "  $0 --input data.jsonl --num_samples 10000"
            echo "  $0 --input data.jsonl --output data_head.jsonl --num_samples 500"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Derive default OUTPUT if the user didn't pass one
if [ -z "$OUTPUT" ]; then
    INPUT_DIR="$(dirname "$INPUT")"
    INPUT_BASE="$(basename "$INPUT")"
    INPUT_STEM="${INPUT_BASE%.jsonl}"
    OUTPUT="${INPUT_DIR}/${INPUT_STEM}_first${NUM_SAMPLES}.jsonl"
fi

echo "========================================"
echo "Split First-N Script (ms_swift)"
echo "========================================"
echo "Input file:  $INPUT"
echo "Output file: $OUTPUT"
echo "Num samples: $NUM_SAMPLES"
echo ""

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Basic sanity check on num_samples (python script re-validates)
if ! [[ "$NUM_SAMPLES" =~ ^[0-9]+$ ]] || [ "$NUM_SAMPLES" -le 0 ]; then
    echo "Error: --num_samples must be a positive integer, got: $NUM_SAMPLES"
    exit 1
fi

# Run the split script
echo "Starting split..."
echo ""

python3 "$SCRIPT_DIR/split_first_n.py" --input "$INPUT" --output "$OUTPUT" --num_samples "$NUM_SAMPLES"

echo ""
echo "========================================"
echo "Split complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Output: $OUTPUT"
echo ""
