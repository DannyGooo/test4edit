# CSS Class Name Minification for JSON Datasets

This directory contains scripts to transform JSON conversation datasets by minifying CSS class names and IDs in HTML/CSS content, similar to how `purgecss-html.js` works.

## Overview

The scripts process JSON files containing web development conversations and minify all CSS class names and IDs to reduce file size while maintaining functionality.

### Features

- ✅ Minifies CSS class names and IDs (e.g., `header-container` → `a`)
- ✅ Uses short alphabetic names: a-z, aa-zz, aaa-zzz, etc.
- ✅ Replaces names in both HTML attributes and CSS selectors
- ✅ Avoids reserved keywords (ad, ads, banner, if, do, for)
- ✅ Safelist support to preserve specific class/ID names
- ✅ Optional HTML minification for additional size reduction
- ✅ Detailed statistics reporting

## Files

- **`transform_classname.py`**: Main Python script for class name minification
- **`run_transform_classname.sh`**: Bash wrapper with argument parsing and dependency management
- **`test_sample.json`**: Sample test data
- **`README.md`**: This documentation

## Installation

### Dependencies

The scripts require the following Python packages:

```bash
pip install beautifulsoup4 lxml htmlmin
```

The bash wrapper script will automatically check and install missing dependencies.

## Usage

### Quick Start

```bash
# Using the bash wrapper (recommended)
bash transform_from_json_classname/run_transform_classname.sh \
  --input input.json \
  --output output.json

# Using Python directly
python3 transform_from_json_classname/transform_classname.py \
  --input input.json \
  --output output.json
```

### Command-Line Options

#### Bash Wrapper (`run_transform_classname.sh`)

```bash
bash run_transform_classname.sh [OPTIONS]

Options:
  --input PATH          Input JSON file path
  --output PATH         Output JSON file path
  --safelist CLASSES    Comma-separated list of class/ID names to preserve
  --no-html-minify      Disable HTML minification (keep formatting)
  --help                Show help message
```

#### Python Script (`transform_classname.py`)

```bash
python3 transform_classname.py [OPTIONS]

Options:
  --input PATH          Input JSON file path (required)
  --output PATH         Output JSON file path (required)
  --safelist CLASSES    Comma-separated class/ID names to preserve
  --no-html-minify      Disable HTML minification
```

## Examples

### Example 1: Basic Usage

```bash
bash transform_from_json_classname/run_transform_classname.sh \
  --input dataset.json \
  --output dataset_minified.json
```

### Example 2: Preserve Specific Classes

```bash
bash transform_from_json_classname/run_transform_classname.sh \
  --input dataset.json \
  --output dataset_minified.json \
  --safelist "active,selected,highlight"
```

### Example 3: Disable HTML Minification

```bash
bash transform_from_json_classname/run_transform_classname.sh \
  --input dataset.json \
  --output dataset_minified.json \
  --no-html-minify
```

### Example 4: Test with Sample Data

```bash
# Run test
bash transform_from_json_classname/run_transform_classname.sh \
  --input transform_from_json_classname/test_sample.json \
  --output transform_from_json_classname/test_output.json

# Check results
cat transform_from_json_classname/test_output.json
```

## Input Format

The script expects a JSON array with conversation objects:

```json
[
  {
    "id": "unique_id",
    "image": "path/to/image.png",
    "conversations": [
      {
        "from": "human",
        "value": "User prompt..."
      },
      {
        "from": "gpt",
        "value": "<!DOCTYPE html>...</html>"
      }
    ]
  }
]
```

## Output

The script produces:

1. **Transformed JSON file** (`output.json`) with minified class names in GPT responses
2. **Skip report file** (`output.json.skipped.json`) if any entries were skipped
3. **Statistics summary** showing:
   - Total entries processed
   - Number of entries skipped with reason breakdown
   - Number of classes/IDs minified
   - Total bytes removed
   - Processing details

### Example Output

```
============================================================
TRANSFORMATION SUMMARY
============================================================
Total entries:          100
Entries processed:      98
Entries skipped:        2

Skip Reason Breakdown:
  - No Style Tags: 1
  - No Css Content: 1

Classes minified:       450
IDs minified:           120
Total bytes removed:    15,234

Output written to:      dataset_minified.json
Skip report written to: dataset_minified.json.skipped.json
============================================================
```

### Skip Report Format

If entries are skipped, a detailed report is generated at `{output}.skipped.json`:

```json
{
  "total_skipped": 2,
  "skip_reasons": {
    "no_style_tags": 1,
    "no_css_content": 1
  },
  "skipped_entries": [
    {
      "id": "entry_123",
      "reason": "no_style_tags",
      "message": "No <style> tags found"
    },
    {
      "id": "entry_456",
      "reason": "no_css_content",
      "message": "No CSS content found in <style> tags"
    }
  ]
}
```

## Skip Reasons Explained

Entries may be skipped for the following reasons:

### 1. `no_gpt_response`
- The entry has no `conversations` field
- The conversations array is empty
- There's no conversation with `"from": "gpt"`
- The GPT response value is empty or missing

### 2. `no_style_tags`
- The HTML content doesn't contain any `<style>` tags
- The GPT generated HTML without embedded CSS (maybe using external CSS or inline styles only)

### 3. `no_css_content`
- `<style>` tags exist but are empty
- `<style>` tags contain only whitespace

### 4. `error`
- Malformed HTML that BeautifulSoup can't parse
- Unexpected data structure
- Any other runtime exceptions
- Error details are included in the skip report

## How It Works

1. **Parse JSON**: Loads the input JSON array
2. **Extract HTML/CSS**: Finds GPT responses containing HTML/CSS
3. **Extract Names**: Identifies all class names and IDs from HTML and CSS
4. **Build Mappings**: Creates minified names (a, b, c, aa, ab, ...)
5. **Transform CSS**: Replaces class/ID names in CSS selectors
6. **Transform HTML**: Replaces class/ID names in HTML attributes
7. **Minify HTML**: Optionally minifies HTML structure
8. **Update JSON**: Replaces GPT response with transformed HTML
9. **Track Skips**: Records any entries that couldn't be processed
10. **Save Results**: Writes transformed JSON and skip report to output files

## Comparison with `purgecss-html.js`

This script implements similar functionality to `purgecss-html.js` but:

- ✅ Works with JSON conversation datasets instead of individual HTML files
- ✅ Written in Python instead of JavaScript
- ✅ Processes batch data instead of single files
- ✅ Maintains same name generation algorithm (a-z, aa-zz, ...)
- ✅ Avoids same reserved keywords

## Reserved Keywords

The following keywords are avoided in minified names to prevent issues with ad-blockers and reserved words:

- `ad`
- `ads`
- `banner`
- `if`
- `do`
- `for`

## Safelist

The safelist feature allows you to preserve specific class or ID names that should not be minified. This is useful for:

- Framework-specific classes (e.g., Bootstrap, Tailwind)
- Accessibility classes
- Third-party integration classes
- Debugging during development

```bash
--safelist "navbar,container,row,col,active,disabled"
```

## Performance

- Processes ~100 entries per minute (depends on HTML complexity)
- Typical size reduction: 15-30% for CSS class/ID names
- Additional 10-20% with HTML minification enabled

## Troubleshooting

### Missing Dependencies

If you get import errors, install dependencies:

```bash
pip install beautifulsoup4 lxml htmlmin
```

### Permission Denied

Make scripts executable:

```bash
chmod +x transform_from_json_classname/transform_classname.py
chmod +x transform_from_json_classname/run_transform_classname.sh
```

### Invalid JSON

Ensure input file is valid JSON:

```bash
python3 -m json.tool input.json > /dev/null
```

## License

This script is part of the webpage vision dataset transformation toolkit.

## See Also

- `transform_from_json/` - Token-based filtering scripts
- `purgecss-html.js` - HTML file minification script
- `transform_from_tar/` - Webdataset transformation scripts
