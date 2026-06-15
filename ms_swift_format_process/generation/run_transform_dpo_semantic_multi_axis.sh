#!/bin/bash
#
# Transform ms_swift JSONL dataset to DPO format with MULTI-AXIS semantic
# rejected samples. Sibling to run_transform_dpo_semantic.sh that perturbs far
# more visual axes (typography, layout, effects — not just colors & sizes) so
# the rejected HTML diverges from the chosen along many independent dimensions.
#
# Usage:
#   ./run_transform_dpo_semantic_multi_axis.sh --input <input.jsonl> --output <output.jsonl> \
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
OUTPUT="/home/liu282/scratch3/projects/vision_to_code/dataset_clean/htmlSlicer/output/screenshot_final_outout/ms_swift/meta_data_web_clean_8000_1280_12800_human_read_token8000_dpo_semantic_multi_axis.jsonl"
LIMIT=""
SEED="42"
PERTURB_RATE="0.7"
CATEGORIES="all"

# Function to display usage
usage() {
    echo -e "${BLUE}ms_swift DPO Dataset Transformation Tool (Semantic, Multi-Axis)${NC}"
    echo ""
    echo "Transforms ms_swift JSONL dataset to DPO format with rejected samples"
    echo "that keep full HTML structure but perturb visual semantics along 8"
    echo "independent axes. The rejected response is syntactically valid HTML"
    echo "that renders to a screenshot visibly wrong on multiple dimensions."
    echo ""
    echo -e "${YELLOW}Perturbation scope (CSS-scoped, plus <img> HTML attrs):${NC}"
    echo "  1. All <style>...</style> blocks"
    echo "  2. All style=\"...\" / style='...' inline attributes"
    echo "  3. <img width=...> <img height=...> HTML attributes (img_dims category only)"
    echo ""
    echo -e "${YELLOW}Perturbation categories:${NC}"
    echo "  colors      - hex (#rgb/#rrggbb/#rrggbbaa), rgb()/rgba(), hsl()/hsla(),"
    echo "                and 31 named CSS colors remapped."
    echo "  dimensions  - px/em/rem/%/vh/vw/pt/cm/mm/in/deg values scaled by"
    echo "                {0.25x, 0.5x, 2x, 3x, 4x}."
    echo "  opacity     - opacity/fill-opacity/stroke-opacity remapped to a"
    echo "                value \u2265 0.2 away from the original."
    echo "  fonts       - font-family replaced with a visually distinct stack"
    echo "                (serif/sans-serif/monospace/cursive)."
    echo "  font_weight - font-weight remapped via swap table (400->900, etc)."
    echo "  transform   - rotate/scale/translate/skew function args perturbed"
    echo "                in place."
    echo "  display     - display/flex-direction/justify-content/align-items"
    echo "                keywords swapped."
    echo "  img_dims    - <img width/height> HTML attrs scaled like dimensions."
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
    echo "                            font_weight,transform,display,img_dims"
    echo "                            (default: 'all')"
    echo "  -h, --help                Show this help message"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  # Full multi-axis perturbation (default)"
    echo "  $0 -i input.jsonl -o output_dpo.jsonl"
    echo ""
    echo "  # Ablation: only colors and font-family"
    echo "  $0 -i input.jsonl -o out.jsonl --categories colors,fonts"
    echo ""
    echo "  # Aggressive (all categories, perturb-rate 0.9)"
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
echo -e "${BLUE}=========================================================${NC}"
echo -e "${BLUE}ms_swift DPO Transformation (Semantic, Multi-Axis)${NC}"
echo -e "${BLUE}=========================================================${NC}"
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
CMD="python3 ${SCRIPT_DIR}/transform_to_dpo_semantic_multi_axis.py --input \"$INPUT\" --output \"$OUTPUT\" --seed $SEED --perturb-rate $PERTURB_RATE --categories \"$CATEGORIES\""

if [[ -n "$LIMIT" ]]; then
    CMD="$CMD --limit $LIMIT"
fi

echo -e "${GREEN}Running transformation...${NC}"
echo ""

eval $CMD

echo ""
echo -e "${GREEN}Transformation complete!${NC}"
