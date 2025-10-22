# Quick Start Guide

## Process Your Full Dataset

To process all HTML files in your dataset (`/home/zha439/scratch/vision2code/dataset/screenshots_with_html_100k`), simply run:

```bash
./process-full-dataset.sh
```

This will:
- Process all 17 tar files containing ~100k HTML files
- Remove unused CSS from `<style>` tags in each HTML file
- Create new tar archives with `_purged.tar` suffix
- Save them to `./processed_dataset/`
- Skip any files already processed (safe to re-run)

**Estimated time**: 15-30 minutes

## Manual Processing

If you want more control, use the scripts directly:

### Process a Single Tar File

```bash
node process-tar-archives.js \
  --output ./output \
  /home/zha439/scratch/vision2code/dataset/screenshots_with_html_100k
```

### Process with Custom Options

```bash
node process-tar-archives.js \
  --output ./processed \
  --temp /custom/tmp \
  --skip-existing \
  --pattern "webdataset_chunk_0000*.tar" \
  /home/zha439/scratch/vision2code/dataset/screenshots_with_html_100k
```

### Process Individual HTML Files

```bash
# Single file
node purgecss-html.js myfile.html

# Multiple files with pattern
node purgecss-html.js "src/**/*.html"

# Dry run first
node purgecss-html.js --dry-run myfile.html
```

## Results

After processing, you'll see:
- **Input**: `webdataset_chunk_00000.tar`
- **Output**: `webdataset_chunk_00000_purged.tar`

Each tar file contains the same PNG and HTML files, but with optimized CSS.

## Verification

Check the results:

```bash
# List files in output
ls -lh processed_dataset/

# View contents of a purged tar
tar -tf processed_dataset/webdataset_chunk_00000_purged.tar | head

# Extract a sample for inspection
mkdir sample_check
tar -xf processed_dataset/webdataset_chunk_00000_purged.tar -C sample_check
```

## Troubleshooting

### Out of disk space
Use a custom temp directory with more space:
```bash
node process-tar-archives.js --temp /path/to/large/tmp /path/to/tar/files
```

### Resume interrupted processing
The `--skip-existing` flag allows you to resume:
```bash
./process-full-dataset.sh  # Safe to run multiple times
```

### Check progress
The script shows real-time progress for each tar file being processed.
