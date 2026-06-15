#!/bin/bash
#
# Combined 50/50 DPO transformation: half entries get infinite-nesting
# repetition rejected samples (equal-share 25-pattern uniform distribution,
# NO length match), half get full-coverage semantic perturbation.
# Uses transform_to_dpo_infinite_nesting_equal_share.py helpers (with
# randomized target_length) for repetition, and
# transform_to_dpo_semantic_full_coverage.py for semantic.
#
# Sibling to run_transform_dpo_combined.sh which routes to the
# frequency-weighted no_length_match variant. This variant instead routes
# to the equal-share taxonomy so each of the 25 repetition sub-patterns
# contributes ~1/25 of the repetition half.
#
# If semantic perturbation fails for an entry (no CSS to perturb), the entry
# automatically falls back to repetition so no entries are wasted.
#
# Usage:
#   ./run_transform_dpo_combined_equal_share.sh --input <input.jsonl> --output <output.jsonl> \
#       [--limit N] [--seed S] [--perturb-rate R] [--categories CSV] [--split F]
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
# INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800_human_read_token8000.jsonl"
# OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800_human_read_token8000_dpo_combined_equal_share.jsonl"
INPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/fixed/meta_data_web_human_read_10K_1280_token_8000_nonsolid_200k.jsonl"
OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/our_all/fixed/meta_data_web_200k_dpo.jsonl"
LIMIT=""
SEED="42"
PERTURB_RATE="0.7"
CATEGORIES="all"
SPLIT="0.5"

# Function to display usage
usage() {
    echo -e "${BLUE}ms_swift DPO Dataset Transformation Tool (Combined: Equal-Share Repetition + Semantic)${NC}"
    echo ""
    echo "Combines two rejection strategies into a single DPO dataset:"
    echo ""
    echo -e "${YELLOW}Repetition half (equal-share, no length match):${NC}"
    echo "  - 25 failure sub-patterns, sampled UNIFORMLY (~4% each)"
    echo "  - 12 completion-failure (cf_*) + 13 inline-repetition (inline_*)"
    echo "  - No length-match guarantee (target_length randomized in"
    echo "    [max(64, len/3), len])"
    echo ""
    echo -e "${YELLOW}Semantic half:${NC}"
    echo "  - 16 perturbation axes from full-coverage generator"
    echo "  - Colors, dimensions, opacity, fonts, font_weight, transform,"
    echo "    display, img_dims, text_style, position, overflow, border_style,"
    echo "    filter, background, unitless_number, table_attrs"
    echo "  - HTML structure preserved; only visual values changed"
    echo ""
    echo -e "${YELLOW}Fallback:${NC}"
    echo "  If semantic perturbation fails (no CSS), the entry falls back"
    echo "  to repetition so no entries are wasted."
    echo ""
    echo -e "${YELLOW}Usage:${NC}"
    echo "  $0 --input <input.jsonl> --output <output.jsonl> [options]"
    echo ""
    echo -e "${YELLOW}Required Arguments:${NC}"
    echo "  -i, --input FILE          Input JSONL file (ms_swift format)"
    echo "  -o, --output FILE         Output JSONL file (DPO format)"
    echo ""
    echo -e "${YELLOW}Optional Arguments:${NC}"
    echo "  -l, --limit N             Limit processing to first N entries"
    echo "  -s, --seed S              Random seed (default: 42)"
    echo "  -r, --perturb-rate R      Per-value perturbation prob for semantic"
    echo "                            half (default: 0.7)"
    echo "  -c, --categories CSV      Semantic categories to enable"
    echo "                            (default: 'all' = all 16)"
    echo "      --split F             Fraction routed to semantic [0.0-1.0]"
    echo "                            (default: 0.5 = 50/50)"
    echo "  -h, --help                Show this help message"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  # Default 50/50 split with equal-share repetition"
    echo "  $0 -i input.jsonl -o output_dpo.jsonl"
    echo ""
    echo "  # 70% semantic, 30% equal-share repetition"
    echo "  $0 -i input.jsonl -o out.jsonl --split 0.7"
    echo ""
    echo "  # 100% equal-share repetition (no semantic)"
    echo "  $0 -i input.jsonl -o out.jsonl --split 0.0"
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
        -r|--perturb-rate)
            PERTURB_RATE="$2"
            shift 2
            ;;
        -c|--categories)
            CATEGORIES="$2"
            shift 2
            ;;
        --split)
            SPLIT="$2"
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
echo -e "${BLUE}==================================================================${NC}"
echo -e "${BLUE}ms_swift DPO Transformation (Combined: Equal-Share Rep + Semantic)${NC}"
echo -e "${BLUE}==================================================================${NC}"
echo ""
echo -e "${GREEN}Configuration:${NC}"
echo "  Input:         $INPUT"
echo "  Output:        $OUTPUT"
if [[ -n "$LIMIT" ]]; then
    echo "  Limit:         $LIMIT entries"
else
    echo "  Limit:         All entries"
fi
echo "  Seed:          $SEED"
echo "  Split:         $SPLIT semantic / $(echo "1 - $SPLIT" | bc) repetition"
echo "  Perturb rate:  $PERTURB_RATE (semantic half only)"
echo "  Categories:    $CATEGORIES (semantic half only)"
echo "  Repetition:    equal-share 25-pattern uniform, NO length match"
echo ""

# Build command
CMD="python3 ${SCRIPT_DIR}/transform_to_dpo_combined_equal_share.py --input \"$INPUT\" --output \"$OUTPUT\" --seed $SEED --perturb-rate $PERTURB_RATE --categories \"$CATEGORIES\" --split $SPLIT"

if [[ -n "$LIMIT" ]]; then
    CMD="$CMD --limit $LIMIT"
fi

echo -e "${GREEN}Running transformation...${NC}"
echo ""

eval $CMD

echo ""
echo -e "${GREEN}Transformation complete!${NC}"
