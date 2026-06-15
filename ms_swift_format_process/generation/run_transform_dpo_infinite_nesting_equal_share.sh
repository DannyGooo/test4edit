#!/bin/bash
#
# Transform ms_swift JSONL dataset to DPO format with infinite-nesting rejected
# samples where ALL 25 failure sub-patterns are sampled with EQUAL probability
# (1/25 each) instead of the real-world frequency weights used by the sibling
# run_transform_dpo_infinite_nesting.sh. Length matching is preserved.
#
# Usage:
#   ./run_transform_dpo_infinite_nesting_equal_share.sh --input <input.jsonl> --output <output.jsonl> [--limit N] [--seed S]
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default values
INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800_human_read_token8000.jsonl"
OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800_human_read_token8000_dpo_infinite_nesting_equal_share.jsonl"
LIMIT=""
SEED="42"

# Function to display usage
usage() {
    echo -e "${BLUE}ms_swift DPO Dataset Transformation Tool (Infinite Nesting, Equal Share)${NC}"
    echo ""
    echo "Transforms ms_swift JSONL dataset to DPO format with length-matched"
    echo "rejected samples. Each of the 25 failure sub-patterns is sampled with"
    echo "EQUAL probability (1/25 ≈ 4%) regardless of real-world frequency."
    echo ""
    echo -e "${YELLOW}Coverage (uniform 1/25 each):${NC}"
    echo "  A) Completion failure (cf_*) — model never reaches </html>     [12 sub-types]"
    echo "       cf_whitespace_runaway, cf_css_bloat, cf_section_repetition,"
    echo "       cf_self_closing_spam, cf_closing_tag_spam, cf_truncated_padded,"
    echo "       cf_css_truncated_other, cf_css_rule_cycling, cf_enumeration,"
    echo "       cf_cycling, cf_incrementing, cf_pure"
    echo "  B) Inline repetition (inline_*) — model loops mid-document      [13 sub-types]"
    echo "       inline_char/tag/section/incrementing/closing_tag/self_closing/"
    echo "       deeply_nested + inline_css_rule/property/selector/"
    echo "       incrementing/value/multi_rule"
    echo ""
    echo -e "${YELLOW}Usage:${NC}"
    echo "  $0 --input <input.jsonl> --output <output.jsonl> [options]"
    echo ""
    echo -e "${YELLOW}Required Arguments:${NC}"
    echo "  -i, --input FILE      Input JSONL file (ms_swift format)"
    echo "  -o, --output FILE     Output JSONL file (DPO format)"
    echo ""
    echo -e "${YELLOW}Optional Arguments:${NC}"
    echo "  -l, --limit N         Limit processing to first N entries"
    echo "  -s, --seed S          Random seed for reproducibility (default: 42)"
    echo "  -h, --help            Show this help message"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  $0 -i input.jsonl -o output_dpo.jsonl"
    echo "  $0 -i input.jsonl -o output_dpo.jsonl --limit 1000"
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -i|--input)
            INPUT="$2"
            shift 2
            ;;
        -o|--output)
            OUTPUT="$2"
            shift 2
            ;;
        -l|--limit)
            LIMIT="$2"
            shift 2
            ;;
        -s|--seed)
            SEED="$2"
            shift 2
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo -e "${RED}Error: Unknown option: $1${NC}"
            echo ""
            usage
            ;;
    esac
done

# Validate required arguments
if [[ -z "$INPUT" ]]; then
    echo -e "${RED}Error: Input file is required${NC}"
    echo ""
    usage
fi

if [[ -z "$OUTPUT" ]]; then
    echo -e "${RED}Error: Output file is required${NC}"
    echo ""
    usage
fi

if [[ ! -f "$INPUT" ]]; then
    echo -e "${RED}Error: Input file not found: $INPUT${NC}"
    exit 1
fi

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is not installed${NC}"
    exit 1
fi

# Display configuration
echo -e "${BLUE}=========================================================${NC}"
echo -e "${BLUE}ms_swift DPO Transformation (Infinite Nesting, Equal Share)${NC}"
echo -e "${BLUE}=========================================================${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  Input:  $INPUT"
echo "  Output: $OUTPUT"
if [[ -n "$LIMIT" ]]; then
    echo "  Limit:  $LIMIT entries"
else
    echo "  Limit:  All entries"
fi
echo "  Seed:   $SEED"
echo ""

# Build command
CMD="python3 ${SCRIPT_DIR}/transform_to_dpo_infinite_nesting_equal_share.py --input \"$INPUT\" --output \"$OUTPUT\" --seed $SEED"

if [[ -n "$LIMIT" ]]; then
    CMD="$CMD --limit $LIMIT"
fi

echo -e "${GREEN}Running transformation...${NC}"
echo ""

eval $CMD

echo ""
echo -e "${GREEN}Transformation complete!${NC}"
