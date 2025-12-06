#!/bin/bash
#
# Transform HTML/CSS dataset to DPO format with loopy rejected samples
#
# Usage:
#   ./run_transform_dpo.sh --input <input.json> --output <output.json> [--limit N] [--seed S]
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
INPUT=""
OUTPUT=""
LIMIT=""
SEED="42"

# Function to display usage
usage() {
    echo -e "${BLUE}DPO Dataset Transformation Tool${NC}"
    echo ""
    echo "Transforms HTML/CSS conversation dataset to DPO format with synthetic"
    echo "loopy rejected samples that simulate model getting stuck in repetition."
    echo ""
    echo -e "${YELLOW}Usage:${NC}"
    echo "  $0 --input <input.json> --output <output.json> [options]"
    echo ""
    echo -e "${YELLOW}Required Arguments:${NC}"
    echo "  -i, --input FILE      Input JSON file with conversation data"
    echo "  -o, --output FILE     Output JSON file for DPO data"
    echo ""
    echo -e "${YELLOW}Optional Arguments:${NC}"
    echo "  -l, --limit N         Limit processing to first N entries"
    echo "  -s, --seed S          Random seed for reproducibility (default: 42)"
    echo "  -h, --help            Show this help message"
    echo ""
    echo -e "${YELLOW}Examples:${NC}"
    echo "  # Process all entries"
    echo "  $0 -i input.json -o output_dpo.json"
    echo ""
    echo "  # Process first 1000 entries for testing"
    echo "  $0 -i input.json -o output_dpo.json --limit 1000"
    echo ""
    echo "  # Use custom random seed"
    echo "  $0 -i input.json -o output_dpo.json --seed 12345"
    echo ""
    echo -e "${YELLOW}Output:${NC}"
    echo "  - output.json: DPO formatted data with chosen/rejected pairs"
    echo "  - output.json.skipped.json: Skip report (if any entries skipped)"
    echo ""
    echo -e "${YELLOW}Loop Types Generated:${NC}"
    echo "  - Character loop: Repeat 1-2 characters"
    echo "  - Tag loop: Repeat HTML tags (<div>, </span>, etc.)"
    echo "  - Section loop: Repeat complete HTML elements"
    echo "  - Incrementing tag loop: Tags with incrementing class numbers"
    echo "  - CSS rule loop: Repeat CSS rule blocks (when in <style>)"
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

# Check if input file exists
if [[ ! -f "$INPUT" ]]; then
    echo -e "${RED}Error: Input file not found: $INPUT${NC}"
    exit 1
fi

# Check Python
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 is not installed${NC}"
    exit 1
fi

# Display configuration
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}DPO Dataset Transformation${NC}"
echo -e "${BLUE}========================================${NC}"
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
CMD="python3 ${SCRIPT_DIR}/transform_to_dpo.py --input \"$INPUT\" --output \"$OUTPUT\" --seed $SEED"

if [[ -n "$LIMIT" ]]; then
    CMD="$CMD --limit $LIMIT"
fi

# Run transformation
echo -e "${GREEN}Running transformation...${NC}"
echo ""

eval $CMD

echo ""
echo -e "${GREEN}Transformation complete!${NC}"
