# CSS-First Human-Readable HTML/CSS Formatter for JSON Datasets

Ultra-simple JSON transformation that converts HTML/CSS to **human-readable format** with **all CSS consolidated into a single `<style>` tag in `<head>`**. No configuration needed - just input and output paths.

## Overview

This tool is a variant of `transform_from_json_huamn_read` with a critical difference: **all CSS is extracted from wherever it appears in the HTML and merged into a single `<style>` tag positioned in the `<head>` section**.

### ✅ All Features Enabled by Default

1. **CSS Consolidation** - All `<style>` tags merged into one in `<head>`
2. **CSS Prettification** - Formatted CSS rules with proper spacing and indentation
3. **HTML Prettification** - Proper indentation and line breaks for HTML structure
4. **Pretty JSON Output** - 2-space indentation for readable JSON

## Quick Start

### Ultra-Simple Usage

```bash
bash transform_from_json_human_read_cssFirst/run_transform_cssFirst.sh \
  --input data.json \
  --output data_cssFirst.json
```

That's it! All CSS consolidated to `<head>` automatically.

## Installation

### Dependencies

```bash
pip install beautifulsoup4 lxml
```

The bash wrapper automatically installs missing dependencies.

## Key Feature: CSS Consolidation

### What This Tool Does Differently

**Before (Multiple `<style>` tags scattered in document):**
```html
<!DOCTYPE html>
<html>
<head>
  <style>.a{padding:20px}</style>
</head>
<body>
  <style>.b{display:flex}</style>
  <div class=a>
    <nav class=b>
      <a href=#>Home</a>
    </nav>
  </div>
  <style>.c{color:#333}</style>
</body>
</html>
```

**After (Single `<style>` tag in `<head>`, prettified):**
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
   .c {
     color: #333;
   }
  </style>
 </head>
 <body>
  <div class="a">
   <nav class="b">
    <a href="#">
     Home
    </a>
   </nav>
  </div>
 </body>
</html>
```

## Formatting Features Explained

### 1. CSS Consolidation to `<head>`

**Process:**
1. Extract CSS from **all** `<style>` tags throughout the document
2. Merge all CSS into a single block
3. Remove all original `<style>` tags
4. Create/update a single `<style>` tag in `<head>` with merged CSS
5. Prettify the merged CSS

**Benefits:**
- **Clean structure** - All styles in one predictable location
- **Better organization** - Easier to find and edit CSS
- **Standard practice** - Follows web development conventions
- **Reduced duplication** - Multiple style tags consolidated

### 2. CSS Prettification

**Before (minified):**
```css
.header{background-color:#fff;color:#000;padding:20px}.menu{display:flex;gap:10px}
```

**After (human-readable):**
```css
.header {
  background-color: #fff;
  color: #000;
  padding: 20px;
}

.menu {
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

### 3. HTML Prettification

- Proper element nesting and indentation
- Clean, readable structure
- Attributes properly formatted
- Line breaks for readability

### 4. Pretty JSON Output

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

### ✅ Use CSS-First Format When:
- **Standard web structure** - Need CSS in `<head>` per best practices
- **Browser compatibility** - Some tools expect styles in `<head>`
- **CSS editing** - All styles in one place for easy modification
- **Code review** - Reviewers expect standard HTML structure
- **Template creation** - Converting to standard webpage templates
- **Framework integration** - Some frameworks expect styles in `<head>`

### ⚠️ Difference from `transform_from_json_huamn_read`:
- **This tool**: Consolidates all CSS to `<head>` (CSS-first approach)
- **Original tool**: Prettifies CSS in-place wherever style tags exist

## Output Files

### 1. Formatted JSON (`output.json`)
- Pretty-printed JSON format (2-space indentation)
- All HTML/CSS content formatted for readability
- CSS consolidated to `<head><style>` tags
- Ready for debugging/development

### 2. Skip Report (`output.json.skipped.json`)
- Only generated if entries are skipped
- Lists entry IDs and skip reasons
- Formatted JSON (readable)

## Statistics Tracked

The tool provides detailed statistics:
- **Original style tags found** - Count of all `<style>` tags before merging
- **CSS blocks merged** - Number of CSS blocks consolidated
- **Entries with CSS in `<head>`** - Successfully processed entries

## Skip Reasons

Entries may be skipped for:

- **no_gpt_response** - No GPT response in conversations
- **no_html_content** - No HTML content in GPT response
- **error** - Processing errors (logged with details)

## Command-Line Options

```bash
bash run_transform_cssFirst.sh [OPTIONS]

Options:
  --input PATH          Input JSON file path
  --output PATH         Output JSON file path
  --help                Show help message
```

## Examples

### Example 1: Basic Usage

```bash
bash transform_from_json_human_read_cssFirst/run_transform_cssFirst.sh \
  --input dataset.json \
  --output dataset_cssFirst.json
```

### Example 2: Processing Multiple Style Tags

**Input HTML:**
```html
<!DOCTYPE html><html><head><style>.a{margin:0}</style></head><body><style>.b{padding:10px}</style><div class=a><p class=b>Text</p></div><style>.c{color:red}</style></body></html>
```

**Output HTML:**
```html
<!DOCTYPE html>
<html>
 <head>
  <style>
   .a {
     margin: 0;
   }
   .b {
     padding: 10px;
   }
   .c {
     color: red;
   }
  </style>
 </head>
 <body>
  <div class="a">
   <p class="b">
    Text
   </p>
  </div>
 </body>
</html>
```

### Example 3: Review Processing Statistics

```bash
# After running the transformation
# Check the summary output for:
# - Original style tags found: 3
# - CSS blocks merged: 3
# - Entries with CSS in <head>: 1
```

## Output Example

```
========================================
CSS-First Formatting Script
========================================
Input file:  data.json
Output file: data_cssFirst.json

Formatting features (ALL ENABLED):
  ✓ CSS consolidation to <head>
  ✓ CSS prettification
  ✓ HTML prettification
  ✓ Pretty JSON output

Processing 1000 entries for CSS-FIRST formatting...
Progress: 1000/1000 entries processed

============================================================
CSS-FIRST FORMATTING SUMMARY
============================================================
Total entries:                1000
Entries processed:            998
Entries skipped:              2

Skip Reason Breakdown:
  - No Html Content: 2

Original style tags found:    2156
CSS blocks merged:            2156
Entries with CSS in <head>:   998

Output written to:            data_cssFirst.json
Skip report written to:       data_cssFirst.json.skipped.json
============================================================
```

## Comparison with Other Tools

| Tool | Purpose | CSS Location | Output Format |
|------|---------|--------------|---------------|
| `transform_from_json_huamn_read` | Prettify in-place | Original locations | Prettified |
| **`transform_from_json_human_read_cssFirst`** | **CSS to `<head>`** | **Single `<style>` in `<head>`** | **Prettified** |
| `transfrom_from_json_miniToken` | Maximum compression | Scattered (minified) | Minified |
| `transform_from_json_classname` | Class minification | Original locations | Minified |

## Workflow Integration

### Standard Web Development Workflow

```bash
# Convert minified HTML with scattered styles to standard format
bash transform_from_json_human_read_cssFirst/run_transform_cssFirst.sh \
  --input dataset_minified.json \
  --output dataset_standard.json

# Now all CSS is in <head> per web standards
# Ready for:
# - Browser rendering
# - Framework integration
# - Template systems
# - CSS editing
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

### Issue: CSS order changed after merge
This is expected - CSS from multiple `<style>` tags is merged in document order. If cascade specificity is important, verify that the merged order maintains the correct CSS precedence.

## Complete Example: Before & After

### Input JSON
```json
{
  "id": "test_1",
  "conversations": [
    {
      "from": "gpt",
      "value": "<!DOCTYPE html><html><head><style>.a{padding:20px}</style></head><body><style>.b{display:flex}</style><div class=a><nav class=b><a href=#>Home</a></nav></div></body></html>"
    }
  ]
}
```

### Output JSON
```json
{
  "id": "test_1",
  "conversations": [
    {
      "from": "gpt",
      "value": "<!DOCTYPE html>\n<html>\n <head>\n  <style>\n   .a {\n     padding: 20px;\n   }\n   .b {\n     display: flex;\n   }\n  </style>\n </head>\n <body>\n  <div class=\"a\">\n   <nav class=\"b\">\n    <a href=\"#\">\n     Home\n    </a>\n   </nav>\n  </div>\n </body>\n</html>"
    }
  ]
}
```

## License

Part of the webpage vision dataset transformation toolkit.

## See Also

- `transform_from_json/` - Token filtering scripts
- `transform_from_json_classname/` - Class minification (readable output)
- `transform_from_json_huamn_read/` - Prettify in-place (CSS stays where it is)
- `transfrom_from_json_miniToken/` - Maximum compression (opposite of this tool)
