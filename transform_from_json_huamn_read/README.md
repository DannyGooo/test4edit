# Human-Readable HTML/CSS Formatter for JSON Datasets

Ultra-simple JSON transformation that converts **minified/compressed HTML/CSS** to **human-readable format**. No configuration needed - just input and output paths.

## Overview

This tool is the **opposite** of `transfrom_from_json_miniToken`. While mini_token compresses HTML/CSS to save space, this tool prettifies it for human readability, making it ideal for debugging, development, and code review.

### ✅ All Features Enabled by Default

1. **HTML Prettification** - Proper indentation and line breaks for HTML structure
2. **CSS Prettification** - Formatted CSS rules with proper spacing and indentation
3. **Pretty JSON Output** - 2-space indentation for readable JSON

## Quick Start

### Ultra-Simple Usage

```bash
bash transform_from_json_huamn_read/run_transform_human_read.sh \
  --input data_compressed.json \
  --output data_readable.json
```

That's it! All formatting applied automatically.

## Installation

### Dependencies

```bash
pip install beautifulsoup4 lxml
```

The bash wrapper automatically installs missing dependencies.

## Formatting Features Explained

### 1. HTML Prettification

**Before (minified):**
```html
<!DOCTYPE html><html><head><style>.a{padding:20px}.b{display:flex}</style></head><body><div class=a><h1>Welcome</h1><nav class=b><a href=#>Home</a><a href=#>About</a></nav></div></body></html>
```

**After (human-readable):**
```html
<!DOCTYPE html>
<html>
 <head>
  <style>
   .a {
     padding: 20px;
   }
   .b {
     display: flex;
   }
  </style>
 </head>
 <body>
  <div class="a">
   <h1>
    Welcome
   </h1>
   <nav class="b">
    <a href="#">
     Home
    </a>
    <a href="#">
     About
    </a>
   </nav>
  </div>
 </body>
</html>
```

### 2. CSS Prettification

**Before (minified):**
```css
.header-container{background-color:#fff;color:#000;padding:20px;margin-bottom:10px}.navigation-menu{display:flex;gap:10px}
```

**After (human-readable):**
```css
.header-container {
  background-color: #fff;
  color: #000;
  padding: 20px;
  margin-bottom: 10px;
}

.navigation-menu {
  display: flex;
  gap: 10px;
}
```

Features:
- Proper indentation (2 spaces)
- One property per line
- Spacing around braces and colons
- Line breaks between rules
- Clean, readable structure

### 3. Pretty JSON Output

**Before (compact):**
```json
{"id":"entry_1","conversations":[{"from":"gpt","value":"..."}]}
```

**After (pretty-printed):**
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

## Use Cases

### ✅ Use Human-Readable Format When:
- **Debugging** - Need to inspect HTML/CSS structure
- **Development** - Working on transformations or testing
- **Code Review** - Manually reviewing generated code
- **Documentation** - Creating examples or tutorials
- **Learning** - Understanding webpage structure

### ⚠️ Don't Use If:
- **Production deployment** - Use minified version for storage/serving
- **Training datasets** - Minified version saves storage and tokens
- **Transfer optimization** - Compressed version is faster to upload/download

For production, use `transfrom_from_json_miniToken` instead.

## Output Files

### 1. Formatted JSON (`output.json`)
- Pretty-printed JSON format (2-space indentation)
- All HTML/CSS content formatted for readability
- Ready for debugging/development

### 2. Skip Report (`output.json.skipped.json`)
- Only generated if entries are skipped
- Lists entry IDs and skip reasons
- Formatted JSON (readable)

## Skip Reasons

Entries may be skipped for:

- **no_gpt_response** - No GPT response in conversations
- **no_html_content** - No HTML content in GPT response
- **error** - Processing errors (logged with details)

## Command-Line Options

```bash
bash run_transform_human_read.sh [OPTIONS]

Options:
  --input PATH          Input JSON file path
  --output PATH         Output JSON file path
  --help                Show help message
```

## Examples

### Example 1: Basic Usage

```bash
bash transform_from_json_huamn_read/run_transform_human_read.sh \
  --input dataset_compressed.json \
  --output dataset_readable.json
```

### Example 2: Converting Mini Token Output

```bash
# First, compress with mini_token
bash transfrom_from_json_miniToken/run_transform_mini_token.sh \
  --input original.json \
  --output compressed.json

# Then, convert to human-readable for debugging
bash transform_from_json_huamn_read/run_transform_human_read.sh \
  --input compressed.json \
  --output readable.json
```

### Example 3: Review Skip Report

```bash
# After running formatting
cat dataset_readable.json.skipped.json

# Count skipped entries
jq '.total_skipped' dataset_readable.json.skipped.json
```

## Output Example

```
========================================
Human-Readable Formatting Script
========================================
Input file:  data_compressed.json
Output file: data_readable.json

Formatting features (ALL ENABLED):
  ✓ HTML prettification
  ✓ CSS prettification
  ✓ Pretty JSON output

Processing 1000 entries for human-readable formatting...
Progress: 1000/1000 entries processed

============================================================
HUMAN-READABLE FORMATTING SUMMARY
============================================================
Total entries:          1000
Entries processed:      998
Entries skipped:        2

Skip Reason Breakdown:
  - No Html Content: 2

Style tags found:       998
CSS blocks formatted:   998

Output written to:      data_readable.json
Skip report written to: data_readable.json.skipped.json
============================================================
```

## Comparison with Other Tools

| Tool | Purpose | Output Format | JSON Format |
|------|---------|---------------|-------------|
| `transform_from_json` | Token filtering | N/A (filters) | Pretty |
| `transform_from_json_classname` | Class minification | Minified (~25%) | Pretty |
| `transfrom_from_json_miniToken` | Maximum compression | Minified (~40-50%) | Compact |
| **`transform_from_json_huamn_read`** | **Human-readable** | **Prettified** | **Pretty** |

## Workflow Integration

### Development Workflow

```bash
# 1. Start with raw data
original.json

# 2. Compress for production
bash transfrom_from_json_miniToken/run_transform_mini_token.sh \
  --input original.json \
  --output production.json

# 3. Format for debugging (if needed)
bash transform_from_json_huamn_read/run_transform_human_read.sh \
  --input production.json \
  --output debug.json

# 4. Review debug version in text editor
code debug.json
```

## Performance

- **Speed**: ~100-200 entries/second (varies by HTML complexity)
- **Memory**: Processes one entry at a time (low memory usage)
- **CPU**: Single-threaded (can run multiple instances in parallel)

## Troubleshooting

### Issue: "No module named 'bs4'"
```bash
pip install beautifulsoup4
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

### Issue: Output looks weird
BeautifulSoup's prettify adds extra whitespace - this is intentional for readability. For production, use the minified version.

## Comparison: Before & After

### Input (from mini_token)
```json
{"id":"test_1","conversations":[{"from":"gpt","value":"<!DOCTYPE html><html><head><style>.a{padding:20px}.b{display:flex}</style></head><body><div class=a><h1>Welcome</h1></div></body></html>"}]}
```

### Output (human-readable)
```json
{
  "id": "test_1",
  "conversations": [
    {
      "from": "gpt",
      "value": "<!DOCTYPE html>\n<html>\n <head>\n  <style>\n   .a {\n     padding: 20px;\n   }\n   .b {\n     display: flex;\n   }\n  </style>\n </head>\n <body>\n  <div class=\"a\">\n   <h1>\n    Welcome\n   </h1>\n  </div>\n </body>\n</html>"
    }
  ]
}
```

## License

Part of the webpage vision dataset transformation toolkit.

## See Also

- `transform_from_json/` - Token filtering scripts
- `transform_from_json_classname/` - Class minification (readable output)
- `transfrom_from_json_miniToken/` - Maximum compression (opposite of this tool)
