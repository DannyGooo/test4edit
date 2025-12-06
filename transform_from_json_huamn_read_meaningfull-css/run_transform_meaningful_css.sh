#!/bin/bash

# run_transform_meaningful_css.sh
# Wrapper script to transform HTML/CSS with semantic, meaningful class and ID names

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_NAME="transform_meaningful_css.py"
SCRIPT_PATH="$SCRIPT_DIR/$SCRIPT_NAME"

# Default paths
DEFAULT_INPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset.json"
DEFAULT_OUTPUT="/home/len091/scratch/vision2code/dataset/qwenVersionFinueCoCo/coco_webdataset_human_meaningfull_css.json"

# Default values
INPUT="$DEFAULT_INPUT"
OUTPUT="$DEFAULT_OUTPUT"

# Function to display usage
usage() {
    cat << EOF
Usage: $0 [--input INPUT_FILE] [--output OUTPUT_FILE]

Transform HTML/CSS with semantic, meaningful class and ID names.

This script analyzes CSS properties and HTML element context to generate
semantically appropriate class and ID names (e.g., 'a' -> 'navbar-container',
'b' -> 'btn-primary'), then prettifies the HTML and CSS output.

Options:
    --input FILE        Input JSON file containing HTML/CSS conversations
                        (default: $DEFAULT_INPUT)
    --output FILE       Output JSON file with transformed content
                        (default: $DEFAULT_OUTPUT)
    --help              Display this help message

Examples:
    # Use default paths
    $0

    # Transform test file
    $0 --input test_sample.json --output test_output.json

    # Transform production data
    $0 --input input.json --output output_semantic.json

Dependencies:
    - Python 3.6+
    - beautifulsoup4
    - lxml

The script will automatically install missing dependencies.
EOF
    exit 0
}

# Parse command-line arguments
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
        --help)
            usage
            ;;
        *)
            echo "Error: Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate required arguments (use defaults if not provided)
if [ -z "$INPUT" ]; then
    echo "Error: Input path is empty"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$OUTPUT" ]; then
    echo "Error: Output path is empty"
    echo "Use --help for usage information"
    exit 1
fi

# Check if input file exists
if [ ! -f "$INPUT" ]; then
    echo "Error: Input file not found: $INPUT"
    exit 1
fi

# Check Python version
echo "Checking Python version..."
if ! command -v python3 &> /dev/null; then
    echo "Error: Python 3 is not installed"
    exit 1
fi

PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
echo "Python version: $PYTHON_VERSION"

# Check and install dependencies
echo "Checking dependencies..."

check_and_install_package() {
    local package=$1
    if ! python3 -c "import $package" 2>/dev/null; then
        echo "Installing $package..."
        pip install $package || {
            echo "Error: Failed to install $package"
            echo "Please install manually: pip install $package"
            exit 1
        }
    else
        echo "✓ $package is installed"
    fi
}

check_and_install_package "bs4"
check_and_install_package "lxml"

# Check if script exists
if [ ! -f "$SCRIPT_PATH" ]; then
    echo "Error: Python script not found: $SCRIPT_PATH"
    exit 1
fi

# Run the transformation
echo ""
echo "=========================================="
echo "Starting transformation..."
echo "Input:  $INPUT"
echo "Output: $OUTPUT"
echo "=========================================="
echo ""

python3 "$SCRIPT_PATH" --input "$INPUT" --output "$OUTPUT"

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "Transformation completed successfully!"
    echo "Output written to: $OUTPUT"
    if [ -f "$OUTPUT.skipped.json" ]; then
        echo "Skip report: $OUTPUT.skipped.json"
    fi
    echo "=========================================="
else
    echo ""
    echo "=========================================="
    echo "Transformation failed with exit code: $exit_code"
    echo "=========================================="
fi

exit $exit_code
