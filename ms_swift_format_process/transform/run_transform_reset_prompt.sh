#!/bin/bash
#
# Reset the user-role prompt in every sample of an ms_swift JSONL file.
# The replacement text lives in the NEW_PROMPT_TEXT constant inside
# transform_reset_prompt.py — edit it there before running.
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

DEFAULT_INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_400k.jsonl"
DEFAULT_OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/meta_data_web_400k_fixed.jsonl"

INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"
NUM_SAMPLES=0

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
            echo "Reset the user-role prompt in every sample of an ms_swift JSONL file"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --input PATH          Input JSONL file path (default: $DEFAULT_INPUT)"
            echo "  --output PATH         Output JSONL file path (default: $DEFAULT_OUTPUT)"
            echo "  --num_samples N       Number of samples to process (0 = all, default: 0)"
            echo "  --help                Show this help message"
            echo ""
            echo "The replacement prompt text is the NEW_PROMPT_TEXT constant at the top"
            echo "of transform_reset_prompt.py. Edit that constant to change the prompt."
            echo ""
            echo "Example:"
            echo "  $0 --input data.jsonl --output data_reset.jsonl --num_samples 5"
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
echo "Reset Prompt Script (ms_swift)"
echo "========================================"
echo "Input file:  $INPUT"
echo "Output file: $OUTPUT"
if [ "$NUM_SAMPLES" -gt 0 ]; then
    echo "Num samples: $NUM_SAMPLES"
else
    echo "Num samples: all"
fi
echo ""

if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

echo "Starting prompt-reset transformation..."
echo ""

python3 "$SCRIPT_DIR/transform_reset_prompt.py" --input "$INPUT" --output "$OUTPUT" --num_samples "$NUM_SAMPLES"

echo ""
echo "========================================"
echo "Transformation complete!"
echo "========================================"
echo ""
echo "Results:"
echo "  Reset data:  $OUTPUT"
if [ -f "${OUTPUT}.skipped.json" ]; then
    echo "  Skip report: ${OUTPUT}.skipped.json"
    echo ""
    echo "Note: Some entries were skipped (no user message or invalid JSON)."
    echo "      Check the skip report for details."
fi
echo ""
