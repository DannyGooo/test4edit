# Maximum Compression for JSON Datasets

Ultra-simple JSON transformation with **MAXIMUM compression** enabled by default. No configuration needed - just input and output paths.

## Overview

This tool applies aggressive compression to HTML/CSS content in JSON conversation datasets:

### ✅ All Features Enabled by Default

1. **CSS Class/ID Minification** - `header-container` → `a`, `navigation-menu` → `b`
2. **CSS Purging** - Remove unused CSS rules (PurgeCSS)
3. **CSS Content Minification** - Remove whitespace, comments, compress colors
4. **HTML Minification** - Remove whitespace, comments, optimize structure
5. **Small CSS Inlining** - Convert tiny CSS blocks (< 100 chars) to inline styles
6. **Compact JSON Output** - No indentation, minimal separators

## Quick Start

### Ultra-Simple Usage

```bash
bash transfrom_from_json_miniToken/run_transform_mini_token.sh \
  --input data.json \
  --output data_compressed.json
```

That's it! Maximum compression applied automatically.

## Installation

### Dependencies

```bash
pip install beautifulsoup4 lxml htmlmin
```

The bash wrapper automatically installs missing dependencies.

## Compression Features Explained

### 1. CSS Class/ID Name Minification

**Before:**
```css
.header-container { padding: 20px; }
.navigation-menu { display: flex; }
#main-content { font-size: 16px; }
```

**After:**
```css
.a{padding:20px}.b{display:flex}#a{font-size:16px}
```

Savings: ~70% reduction in class/ID names

### 2. CSS Purging (Remove Unused Rules)

**Before:**
```css
.used-class { color: red; }
.unused-class { color: blue; }  /* Not in HTML */
```

**After:**
```css
.a{color:red}
```

Savings: 3-8% additional reduction

### 3. CSS Content Minification

**Before:**
```css
/* Comment */
.container {
  background-color: #ffffff;
  padding: 10px;
}
```

**After:**
```css
.a{background-color:#fff;padding:10px}
```

Features:
- Remove comments
- Remove whitespace
- Compress colors (`#ffffff` → `#fff`)
- Remove trailing semicolons

Savings: 5-10% additional reduction

### 4. HTML Minification

**Before:**
```html
<!DOCTYPE html>
<html>
  <head>
    <!-- Comment -->
  </head>
  <body>
    <div class="container">
      Content
    </div>
  </body>
</html>
```

**After:**
```html
<!DOCTYPE html><html><head></head><body><div class=a>Content</div></body></html>
```

Savings: 10-20% additional reduction

### 5. Small CSS Inlining

For CSS blocks < 100 characters:

**Before:**
```html
<style>.a{color:red}</style>
<div class="a">Text</div>
```

**After:**
```html
<div style="color:red">Text</div>
```

Saves overhead of `<style>` tag for tiny CSS

### 6. Compact JSON Output

**Before:**
```json
{
  "id": "entry_1",
  "conversations": [
    {
      "from": "gpt",
      "value": "..."
    }
  ]
}
```

**After:**
```json
{"id":"entry_1","conversations":[{"from":"gpt","value":"..."}]}
```

Savings: 15-25% reduction in JSON file size

## Expected Compression Results

### Example Dataset (100,000 entries)

| Feature | Bytes Saved | Percentage |
|---------|-------------|------------|
| Class/ID minification | 40-50 MB | 35-40% |
| CSS purging | 5-10 MB | 4-8% |
| CSS minification | 8-12 MB | 6-10% |
| HTML minification | 15-20 MB | 12-16% |
| JSON compaction | 30-40 MB | 25-30% |
| **TOTAL** | **135-160 MB** | **40-50%** |

### Comparison

| Version | Size Saved | Total Compression |
|---------|------------|-------------------|
| Original (no compression) | 0 MB | 0% |
| `transform_classname` (--no-html-minify) | ~92 MB | ~25% |
| `transform_mini_token` (this tool) | **~135-160 MB** | **~40-50%** |

## Output Files

### 1. Compressed JSON (`output.json`)
- Compact JSON format (no indentation)
- All HTML/CSS content compressed
- Ready for training/deployment

### 2. Skip Report (`output.json.skipped.json`)
- Only generated if entries are skipped
- Lists entry IDs and skip reasons
- Formatted JSON (readable)

## Skip Reasons

Entries may be skipped for:

- **no_gpt_response** - No GPT response in conversations
- **no_style_tags** - No `<style>` tags in HTML
- **no_css_content** - Empty `<style>` tags
- **error** - Processing errors (logged with details)

## Command-Line Options

```bash
bash run_transform_mini_token.sh [OPTIONS]

Options:
  --input PATH          Input JSON file path
  --output PATH         Output JSON file path
  --help                Show help message
```

## Examples

### Example 1: Basic Usage

```bash
bash transfrom_from_json_miniToken/run_transform_mini_token.sh \
  --input dataset.json \
  --output dataset_compressed.json
```

### Example 2: Check Compression Results

```bash
# Before compression
ls -lh dataset.json

# Run compression
bash transfrom_from_json_miniToken/run_transform_mini_token.sh \
  --input dataset.json \
  --output dataset_compressed.json

# After compression (compare sizes)
ls -lh dataset_compressed.json
```

### Example 3: Review Skip Report

```bash
# After running compression
cat dataset_compressed.json.skipped.json

# Count skipped entries
jq '.total_skipped' dataset_compressed.json.skipped.json
```

## Output Example

```
========================================
MAXIMUM Compression Script
========================================
Input file:  data.json
Output file: data_compressed.json

Compression features (ALL ENABLED):
  ✓ Class/ID minification
  ✓ CSS purging (remove unused)
  ✓ CSS minification
  ✓ HTML minification
  ✓ Small CSS inlining
  ✓ Compact JSON output

Processing 100000 entries with MAXIMUM compression...
Progress: 100000/100000 entries processed

============================================================
MAXIMUM COMPRESSION SUMMARY
============================================================
Total entries:          100000
Entries processed:      99996
Entries skipped:        4

Classes minified:       2075263
IDs minified:           952869
CSS inlined:            1234 entries

Bytes removed breakdown:
  - CSS purged:         5,234,123
  - CSS minified:       8,123,456
  - HTML minified:      18,234,567
  - TOTAL REMOVED:      142,567,890

Output written to:      data_compressed.json
Skip report written to: data_compressed.json.skipped.json
============================================================
```

## Comparison with Other Tools

| Tool | Purpose | Compression | JSON Format |
|------|---------|-------------|-------------|
| `transform_from_json` | Token filtering | N/A (filters) | Pretty |
| `transform_from_json_classname` | Class minification | ~25% | Pretty |
| **`transfrom_from_json_miniToken`** | **Maximum compression** | **~40-50%** | **Compact** |
| `purgecss-html.js` | HTML file compression | ~30% | N/A (HTML files) |

## When to Use

### ✅ Use Maximum Compression When:
- **Production deployment** - Final dataset for serving
- **Storage optimization** - Need to save disk space
- **Transfer optimization** - Faster uploads/downloads
- **Training datasets** - Model doesn't need readable HTML

### ⚠️ Don't Use If:
- **Debugging** - Need readable HTML/CSS
- **Development** - Still testing transformations
- **Human review** - Need to manually inspect code

For debugging, use `transform_from_json_classname` with `--no-html-minify` instead.

## Performance

- **Speed**: ~100 entries/second (varies by HTML complexity)
- **Memory**: Processes one entry at a time (low memory usage)
- **CPU**: Single-threaded (can run multiple instances in parallel)

## Troubleshooting

### Issue: "No module named 'htmlmin'"
```bash
pip install htmlmin
```

### Issue: "Input file not found"
Check file path:
```bash
ls -l /path/to/input.json
```

### Issue: High skip rate
Check skip report:
```bash
cat output.json.skipped.json | jq '.skip_reasons'
```

## License

Part of the webpage vision dataset transformation toolkit.

## See Also

- `transform_from_json/` - Token filtering scripts
- `transform_from_json_classname/` - Class minification (readable output)
- `purgecss-html.js` - Single HTML file compression
