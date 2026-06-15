#!/bin/bash
#
# Transform ms_swift JSONL dataset to DPO format with FULL-COVERAGE semantic
# rejected samples. Extends run_transform_dpo_semantic_multi_axis.sh from 8
# perturbation categories to 16, reaching the long tail of CSS / HTML visual
# semantics (typography, layout flow, overflow, borders, filters, backgrounds,
# unitless numbers, HTML table integer attrs).
#
# Usage:
#   ./run_transform_dpo_semantic_full_coverage.sh --input <input.jsonl> --output <output.jsonl> \
#       [--limit N] [--seed S] [--perturb-rate R] [--categories CSV]
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
OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800_human_read_token8000_dpo_semantic_full_coverage.jsonl"
LIMIT=""
SEED="42"
PERTURB_RATE="0.7"
CATEGORIES="all"

# Function to display usage
usage() {
    echo -e "${BLUE}ms_swift DPO Dataset Transformation Tool (Semantic, Full Coverage, 16 axes)${NC}"
    echo ""
    echo "Transforms ms_swift JSONL dataset to DPO format with rejected samples"
    echo "that keep full HTML structure but perturb visual semantics along 16"
    echo "independent axes — twice the coverage of run_transform_dpo_semantic_multi_axis.sh."
    echo ""
    echo -e "${YELLOW}Perturbation scope:${NC}"
    echo "  1. <style>...</style> blocks (CSS-scoped categories)"
    echo "  2. style=\"...\" / style='...' inline attributes (CSS-scoped categories)"
    echo "  3. Whole HTML (img_dims, table_attrs categories only)"
    echo ""
    echo -e "${YELLOW}Existing 8 categories (inherited from multi_axis):${NC}"
    echo "  colors          - hex/rgb/rgba/hsl/hsla/31 named CSS colors"
    echo "  dimensions      - px/em/rem/%/vh/vw/pt/cm/mm/in/deg scaled by discrete factors"
    echo "  opacity         - opacity/fill-opacity/stroke-opacity floats"
    echo "  fonts           - font-family replaced with distinct stacks"
    echo "  font_weight     - font-weight remapped via swap table"
    echo "  transform       - rotate/scale/translate/skew function args"
    echo "  display         - display/flex-direction/justify-content/align-items"
    echo "  img_dims        - <img width=> <img height=> HTML integer attrs"
    echo ""
    echo -e "${YELLOW}New 8 categories (full_coverage only):${NC}"
    echo "  text_style      - text-align/text-decoration/text-transform/font-style"
    echo "  position        - position/float/clear keyword swaps"
    echo "  overflow        - overflow/visibility/white-space/box-sizing"
    echo "  border_style    - border-style/outline-style/list-style-type/cursor"
    echo "  filter          - CSS filter fn args (blur/brightness/grayscale/…)"
    echo "                    + mix-blend-mode keyword swap"
    echo "  background      - background-position/background-size/background-repeat"
    echo "  unitless_number - z-index/order/flex-grow/flex-shrink/line-height/"
    echo "                    tab-size/column-count (no unit)"
    echo "  table_attrs     - <table border/cellpadding/cellspacing> and"
    echo "                    <td/th colspan/rowspan> HTML integer attrs"
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
    echo "  -r, --perturb-rate R      Per-value perturbation probability in"
    echo "                            (0.0, 1.0] (default: 0.7)"
    echo "  -c, --categories CSV      Comma-separated categories to enable."
    echo "                            Valid: colors,dimensions,opacity,fonts,"
    echo "                            font_weight,transform,display,img_dims,"
    echo "                            text_style,position,overflow,border_style,"
    echo "                            filter,background,unitless_number,table_attrs"
    echo "                            (default: 'all')"
    echo "  -h, --help                Show this help message"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  # Full 16-axis perturbation (default)"
    echo "  $0 -i input.jsonl -o output_dpo.jsonl"
    echo ""
    echo "  # Only the 8 new categories (compare against multi_axis)"
    echo "  $0 -i input.jsonl -o out.jsonl \\"
    echo "     --categories text_style,position,overflow,border_style,filter,background,unitless_number,table_attrs"
    echo ""
    echo "  # Aggressive (all 16 categories at perturb-rate 0.9)"
    echo "  $0 -i input.jsonl -o out.jsonl --limit 1000 --perturb-rate 0.9"
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
echo -e "${BLUE}=============================================================${NC}"
echo -e "${BLUE}ms_swift DPO Transformation (Semantic, Full Coverage, 16 axes)${NC}"
echo -e "${BLUE}=============================================================${NC}"
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
echo "  Perturb rate:  $PERTURB_RATE"
echo "  Categories:    $CATEGORIES"
echo ""

# Build command
CMD="python3 ${SCRIPT_DIR}/transform_to_dpo_semantic_full_coverage.py --input \"$INPUT\" --output \"$OUTPUT\" --seed $SEED --perturb-rate $PERTURB_RATE --categories \"$CATEGORIES\""

if [[ -n "$LIMIT" ]]; then
    CMD="$CMD --limit $LIMIT"
fi

echo -e "${GREEN}Running transformation...${NC}"
echo ""

eval $CMD

echo ""
echo -e "${GREEN}Transformation complete!${NC}"
