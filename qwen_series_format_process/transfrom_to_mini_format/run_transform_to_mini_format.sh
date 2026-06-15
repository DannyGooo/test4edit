#!/bin/bash
# Whitespace-only HTML minification (no content alteration)
# No external dependencies needed - pure stdlib

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

INPUT="${1:---input}"
OUTPUT="${2:---output}"

# If positional args look like flags, pass through as-is
if [[ "$INPUT" == --* ]]; then
    python3 "$SCRIPT_DIR/transform_to_mini_format.py" "$@"
else
    python3 "$SCRIPT_DIR/transform_to_mini_format.py" --input "$INPUT" --output "$OUTPUT"
fi
