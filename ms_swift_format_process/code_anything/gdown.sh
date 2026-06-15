#!/usr/bin/env bash
set -euo pipefail

TARGET_DIR=/home/liu282/scratch3/projects/vision_to_code/dataset/code_anything
mkdir -p "$TARGET_DIR"
cd "$TARGET_DIR"

# Skip if $output_name already exists in TARGET_DIR; otherwise gdown -O it.
download_if_missing() {
    local file_id="$1"
    local output_name="$2"
    if [[ -f "$output_name" ]]; then
        echo "[skip]     $output_name already exists"
        return 0
    fi
    echo "[download] $output_name"
    if ! gdown -O "$output_name" "https://drive.google.com/file/d/${file_id}/view"; then
        echo "[warn]     $output_name failed — continuing with remaining downloads"
        rm -f "$output_name"  # remove any partial / empty file so next run retries
    fi
}

# Already-downloaded — canonical names match the files currently in TARGET_DIR.
download_if_missing "16IFdrZlJiVZEmm03CeC6yJdjKiWt-ggt" "11_Formulation.zip"
download_if_missing "1lh4QNGdHrhA-yqxGKvf9Ks7qPIWMK-kt" "8_CircuiTikZ.zip"
download_if_missing "1QoVnNz9yEkIdhI8axux5YKlciV93pAOB" "chart.zip"
download_if_missing "1GUIkFgj8XGlwnrvck636DGR63PXubytz" "svg.zip"

# Not yet downloaded — canonical names chosen from the script's comment labels.
download_if_missing "1k9FlUg3sD5fYGJ1UlHRB_D3fX9mh5dg5" "Mesh.zip"
download_if_missing "1KM_Iz0ErRUExdG0vdZHVPviKI_-s_hq1" "ABCNotation.zip"
download_if_missing "1qRt4m9crFKWuZx_hmTujRo003FRyK9ha" "Chemical.zip"
download_if_missing "1RuRsnVkJuam823zBob0v4oxmQHMZYy7A" "Chemical_smiles.zip"
download_if_missing "1CxUUusvvxPZpaFmHVzQ5c1i4ip_JV-LC" "Biological_structure.zip"
download_if_missing "1sz4rQris_WKk4qYPrKT1mshR35YLY5e1" "Math_graphics.zip"
download_if_missing "11p9xnzgEEtmC2hBi4f7w0zBnrSnIhJ2H" "Diagram_FlowChart.zip"
download_if_missing "1hDJvCNf6EklnuZyJ05hKh6hXLxw3jmQf" "Table.zip"
download_if_missing "1n62eBkx2Ju6Zl8HcvR9zJuGWfOjgP9YH" "Slide_Marp.zip"
download_if_missing "1-JGG3LxbObHgKYE4pXrcOvZdsyH7Yurq" "Slide_Beamer.zip"
download_if_missing "1wlaVLQ2S--1dmKs5CIQOtemt3p0g4eyR" "cad.zip"
