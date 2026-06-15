#!/bin/bash
#
# Simple wrapper for dedup_near_duplicates.py
# Focuses on a small set of practical options.
#
# bash transform_from_json/dedup_subset/run_dedup_near_duplicates.sh --input /home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo/coco_webdataset_200k_8000.json --output /home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo/coco_webdataset_20k_8000_html_dedup_image100k.json --top-n  100000 --with-image-dedup --image-source tar --image-tars-dir /home/liu282/scratch3/projects/vision_to_code/dataset/qwenVersionFinueCoCo/tars
set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

INPUT=""
OUTPUT=""
REPORT_OUTPUT=""
TOP_N="50000"
PROFILE="balanced"  # strict|balanced|loose
WITH_IMAGE_DEDUP="false"
IMAGE_ROOT=""
IMAGE_KEY="image"
IMAGE_SOURCE="auto"  # auto|filesystem|tar
IMAGE_TARS_DIR=""
IMAGE_TAR_PATTERN="images-*.tar"
TAR_LOOKUP="chunk-map"
MISSING_IMAGE_POLICY="keep"
QUALITY_SCORE_KEY="ratio"

# Fixed internals for simple mode
HTML_CLUSTER_KEEP_K="1"
SIMHASH_BAND_BITS="16"
IMAGE_HASH_TYPE="phash"
IMAGE_CLUSTER_KEEP_K="1"

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
        --report-output)
            REPORT_OUTPUT="$2"
            shift 2
            ;;
        --top-n)
            TOP_N="$2"
            shift 2
            ;;
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --with-image-dedup)
            WITH_IMAGE_DEDUP="true"
            shift 1
            ;;
        --image-root)
            IMAGE_ROOT="$2"
            shift 2
            ;;
        --image-source)
            IMAGE_SOURCE="$2"
            shift 2
            ;;
        --image-tars-dir)
            IMAGE_TARS_DIR="$2"
            shift 2
            ;;
        --image-tar-pattern)
            IMAGE_TAR_PATTERN="$2"
            shift 2
            ;;
        --missing-image-policy)
            MISSING_IMAGE_POLICY="$2"
            shift 2
            ;;
        --image-key)
            IMAGE_KEY="$2"
            shift 2
            ;;
        --quality-score-key)
            QUALITY_SCORE_KEY="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 --input PATH --output PATH [OPTIONS]"
            echo ""
            echo "Required:"
            echo "  --input PATH                   Input JSON"
            echo "  --output PATH                  Output JSON"
            echo ""
            echo "Simple options:"
            echo "  --top-n N                      Final size after dedup (default: 50000, use -1 for all)"
            echo "  --profile NAME                 strict|balanced|loose (default: balanced)"
            echo "  --report-output PATH           Optional report JSON"
            echo "  --quality-score-key KEY        Ranking score key (default: ratio)"
            echo ""
            echo "Image dedup (optional):"
            echo "  --with-image-dedup             Enable screenshot dedup stage"
            echo "  --image-source MODE            auto|filesystem|tar (default: auto)"
            echo "  --image-root PATH              Root dir for extracted/normal image files"
            echo "  --image-tars-dir PATH          Directory that contains image tar shards"
            echo "  --image-tar-pattern GLOB       Tar shard pattern (default: images-*.tar)"
            echo "  --missing-image-policy POLICY  keep|drop|fail (default: keep)"
            echo "  --image-key KEY                Entry key holding image path (default: image)"
            echo ""
            echo "Examples:"
            echo "  # HTML dedup only"
            echo "  $0 --input in.json --output out.json --top-n 50000"
            echo ""
            echo "  # HTML + screenshot dedup from extracted image files"
            echo "  $0 --input in.json --output out.json --with-image-dedup --image-source filesystem --image-root /data/images"
            echo ""
            echo "  # HTML + screenshot dedup directly from tar shards"
            echo "  $0 --input in.json --output out.json --with-image-dedup --image-source tar --image-tars-dir /data/tars"
            echo ""
            echo "Advanced tuning:"
            echo "  Use python3 $SCRIPT_DIR/dedup_near_duplicates.py --help"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage."
            exit 1
            ;;
    esac
done

if [[ -z "$INPUT" || -z "$OUTPUT" ]]; then
    echo "Error: --input and --output are required."
    exit 1
fi

case "$PROFILE" in
    strict)
        HTML_TEXT_THRESHOLD="2"
        HTML_TEXT_THRESHOLD_WITH_DOM="4"
        HTML_DOM_THRESHOLD="4"
        IMAGE_THRESHOLD="6"
        ;;
    balanced)
        HTML_TEXT_THRESHOLD="3"
        HTML_TEXT_THRESHOLD_WITH_DOM="6"
        HTML_DOM_THRESHOLD="6"
        IMAGE_THRESHOLD="8"
        ;;
    loose)
        HTML_TEXT_THRESHOLD="4"
        HTML_TEXT_THRESHOLD_WITH_DOM="8"
        HTML_DOM_THRESHOLD="8"
        IMAGE_THRESHOLD="10"
        ;;
    *)
        echo "Error: --profile must be one of strict|balanced|loose."
        exit 1
        ;;
esac

if [[ "$IMAGE_SOURCE" != "auto" && "$IMAGE_SOURCE" != "filesystem" && "$IMAGE_SOURCE" != "tar" ]]; then
    echo "Error: --image-source must be one of auto|filesystem|tar."
    exit 1
fi

if [[ "$MISSING_IMAGE_POLICY" != "keep" && "$MISSING_IMAGE_POLICY" != "drop" && "$MISSING_IMAGE_POLICY" != "fail" ]]; then
    echo "Error: --missing-image-policy must be one of keep|drop|fail."
    exit 1
fi

if [[ "$WITH_IMAGE_DEDUP" == "true" && "$IMAGE_SOURCE" == "tar" && -z "$IMAGE_TARS_DIR" ]]; then
    echo "Error: --image-tars-dir is required when --with-image-dedup and --image-source tar are used."
    exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not found in PATH."
    exit 1
fi

CMD=(python3 "$SCRIPT_DIR/dedup_near_duplicates.py"
    --input "$INPUT"
    --output "$OUTPUT"
    --top-n "$TOP_N"
    --quality-score-key "$QUALITY_SCORE_KEY"
    --html-cluster-keep-k "$HTML_CLUSTER_KEEP_K"
    --html-text-threshold "$HTML_TEXT_THRESHOLD"
    --html-text-threshold-with-dom "$HTML_TEXT_THRESHOLD_WITH_DOM"
    --html-dom-threshold "$HTML_DOM_THRESHOLD"
    --simhash-band-bits "$SIMHASH_BAND_BITS"
)

if [[ -n "$REPORT_OUTPUT" ]]; then
    CMD+=(--report-output "$REPORT_OUTPUT")
fi

if [[ "$WITH_IMAGE_DEDUP" == "true" ]]; then
    CMD+=(--enable-image-dedup)
    CMD+=(--image-key "$IMAGE_KEY")
    CMD+=(--image-source "$IMAGE_SOURCE")
    CMD+=(--image-hash-type "$IMAGE_HASH_TYPE")
    CMD+=(--image-threshold "$IMAGE_THRESHOLD")
    CMD+=(--image-cluster-keep-k "$IMAGE_CLUSTER_KEEP_K")
    CMD+=(--missing-image-policy "$MISSING_IMAGE_POLICY")
    CMD+=(--tar-lookup "$TAR_LOOKUP")
    if [[ -n "$IMAGE_ROOT" ]]; then
        CMD+=(--image-root "$IMAGE_ROOT")
    fi
    if [[ -n "$IMAGE_TARS_DIR" ]]; then
        CMD+=(--image-tars-dir "$IMAGE_TARS_DIR")
        CMD+=(--image-tar-pattern "$IMAGE_TAR_PATTERN")
    fi
fi

echo "Running dedup with profile: $PROFILE"
printf '  %q' "${CMD[@]}"
echo ""
echo ""

"${CMD[@]}"
