# Transform HTML/CSS with Meaningful Semantic Names

Transform HTML/CSS code by replacing minified or generic class and ID names with semantically meaningful names based on CSS properties and HTML element context.

## Purpose

This tool addresses the problem of minified or cryptic CSS class/ID names (like `a`, `b`, `c1`, `div1`) by:

1. **Analyzing CSS properties** to understand the purpose of each class/ID
2. **Examining HTML context** to see where and how classes/IDs are used
3. **Generating semantic names** that reflect the actual purpose and usage
4. **Prettifying output** for better readability

## Features

- **Intelligent Name Generation**: Combines CSS property analysis and HTML element context
- **CSS Property Analysis**: Detects layout (flex/grid), typography, colors, buttons, spacing patterns
- **HTML Context Analysis**: Considers element types (nav, header, button, etc.) where classes are used
- **Comprehensive Replacement**: Updates both CSS selectors and HTML attributes
- **Prettified Output**: Properly indented HTML and CSS
- **Skip Tracking**: Reports entries that couldn't be processed with detailed reasons
- **Unique Name Generation**: Ensures no naming conflicts with automatic numbering

## Algorithm

### 1. CSS Property Analysis

The script analyzes CSS properties to infer semantic categories:

| Category | CSS Property Patterns | Example Semantic Name |
|----------|----------------------|----------------------|
| Layout | `display: flex`, `flex-direction` | `flex-container`, `flex-row` |
| Grid | `display: grid`, `grid-template` | `grid-container` |
| Typography | `font-*`, `text-*`, `line-height` | `text`, `heading`, `bold` |
| Buttons | `cursor: pointer`, `border-radius` | `button`, `btn` |
| Colors | `background: #color`, `color: #color` | `primary`, `blue`, `red` |
| Spacing | `padding`, `margin` | `padded`, `spaced` |

### 2. HTML Context Analysis

The script examines which HTML elements use each class/ID:

| HTML Element | Base Semantic Name |
|--------------|-------------------|
| `<nav>` | `navbar` |
| `<header>` | `header` |
| `<footer>` | `footer` |
| `<button>` | `btn` |
| `<aside>` | `sidebar` |
| `<main>` | `main-content` |
| `<div>` | `container` |
| `<span>` | `text` |

### 3. Name Generation Strategy

The script combines insights from both analyses:

```
Semantic Name = HTML Context + CSS Category
```

**Examples:**

1. **Minified class `a`**:
   - CSS: `display: flex; flex-direction: row;`
   - HTML: Used in `<nav>` element
   - Result: `navbar-flex-container`

2. **Minified class `b`**:
   - CSS: `background: #0066ff; padding: 10px; border-radius: 5px;`
   - HTML: Used in `<button>` element
   - Result: `btn-primary`

3. **Minified class `c`**:
   - CSS: `font-size: 2rem; font-weight: bold;`
   - HTML: Used in `<h1>` element
   - Result: `heading-large-text`

### 4. Uniqueness Handling

If a generated name already exists, the script appends a number:
- `container` → `container-2`, `container-3`, etc.

## Usage

### Basic Usage

```bash
./run_transform_meaningful_css.sh --input input.json --output output.json
```

### Options

- `--input FILE`: Input JSON file containing HTML/CSS conversations (required)
- `--output FILE`: Output JSON file with transformed content (required)
- `--help`: Display help message

### Examples

#### Transform test file
```bash
./run_transform_meaningful_css.sh --input test_sample.json --output test_output.json
```

#### Transform production data
```bash
./run_transform_meaningful_css.sh \
    --input ../data/conversations.json \
    --output ../data/conversations_semantic.json
```

## Input Format

The script expects a JSON array of conversation objects:

```json
[
  {
    "id": "example_1",
    "conversations": [
      {
        "from": "human",
        "value": "Create a navigation bar"
      },
      {
        "from": "gpt",
        "value": "<!DOCTYPE html>\n<html>\n<head>\n<style>\n.a{display:flex;}\n.b{color:#00f;}\n</style>\n</head>\n<body>\n<nav class=\"a b\">Navigation</nav>\n</body>\n</html>"
      }
    ]
  }
]
```

## Output Format

The script produces:

1. **Main output file**: Transformed JSON with semantic names and prettified HTML/CSS
2. **Skip report** (`.skipped.json`): Details about entries that couldn't be processed

### Example Output

**Transformed HTML/CSS:**

```html
<!DOCTYPE html>
<html>
 <head>
  <style>
.navbar-flex-container {
  display: flex;
}

.navbar-primary {
  color: #00f;
}
  </style>
 </head>
 <body>
  <nav class="navbar-flex-container navbar-primary">
   Navigation
  </nav>
 </body>
</html>
```

**Skip Report:**

```json
{
  "total_skipped": 2,
  "skip_reasons": {
    "no_gpt_response": 1,
    "no_css": 1
  },
  "skipped_entries": [
    {
      "id": "entry_5",
      "reason": "no_gpt_response",
      "message": "No GPT response found in conversations"
    },
    {
      "id": "entry_7",
      "reason": "no_css",
      "message": "No CSS content to transform"
    }
  ]
}
```

## Before & After Examples

### Example 1: Navigation Bar

**Before:**
```html
<style>
.a{display:flex;justify-content:space-between;background:#333;}
.b{color:#fff;text-decoration:none;}
</style>
<nav class="a">
  <a href="#" class="b">Home</a>
  <a href="#" class="b">About</a>
</nav>
```

**After:**
```html
<style>
.navbar-flex-container {
  display: flex;
  justify-content: space-between;
  background: #333;
}

.link-text {
  color: #fff;
  text-decoration: none;
}
</style>
<nav class="navbar-flex-container">
  <a href="#" class="link-text">Home</a>
  <a href="#" class="link-text">About</a>
</nav>
```

### Example 2: Button Components

**Before:**
```html
<style>
.x{padding:10px 20px;background:#0066ff;color:#fff;border-radius:5px;cursor:pointer;}
.y{padding:10px 20px;background:#ccc;color:#333;border-radius:5px;cursor:pointer;}
</style>
<button class="x">Primary Action</button>
<button class="y">Secondary Action</button>
```

**After:**
```html
<style>
.btn-primary {
  padding: 10px 20px;
  background: #0066ff;
  color: #fff;
  border-radius: 5px;
  cursor: pointer;
}

.btn-button {
  padding: 10px 20px;
  background: #ccc;
  color: #333;
  border-radius: 5px;
  cursor: pointer;
}
</style>
<button class="btn-primary">Primary Action</button>
<button class="btn-button">Secondary Action</button>
```

### Example 3: Grid Layout

**Before:**
```html
<style>
.g{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;}
.i{padding:20px;background:#f5f5f5;}
</style>
<div class="g">
  <div class="i">Item 1</div>
  <div class="i">Item 2</div>
  <div class="i">Item 3</div>
</div>
```

**After:**
```html
<style>
.container-grid-container {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
}

.container-padded {
  padding: 20px;
  background: #f5f5f5;
}
</style>
<div class="container-grid-container">
  <div class="container-padded">Item 1</div>
  <div class="container-padded">Item 2</div>
  <div class="container-padded">Item 3</div>
</div>
```

## Dependencies

- Python 3.6+
- beautifulsoup4
- lxml

Dependencies are automatically installed by the wrapper script.

## Skip Reasons

Entries may be skipped for the following reasons:

| Reason | Description |
|--------|-------------|
| `no_gpt_response` | No GPT response found in conversations |
| `no_html_content` | GPT response doesn't contain HTML |
| `no_css` | No CSS content found to transform |
| `error` | Processing error occurred |

## Implementation Details

### Key Components

1. **SemanticNameGenerator**: Core class for analyzing and generating semantic names
   - `analyze_css_properties()`: Detects CSS patterns
   - `analyze_html_context()`: Examines HTML usage
   - `generate_semantic_name()`: Combines analyses to create meaningful names

2. **CSS Processing**:
   - `extract_css_rules()`: Parses CSS to extract rules and properties
   - `replace_css_selectors()`: Updates class/ID names in CSS

3. **HTML Processing**:
   - `extract_all_classes_and_ids()`: Finds all classes/IDs in HTML
   - `replace_html_attributes()`: Updates class/ID attributes in HTML

4. **Formatting**:
   - `prettify_css()`: Adds proper indentation to CSS
   - BeautifulSoup's `prettify()`: Formats HTML structure

## Comparison with Related Tools

| Tool | Purpose | Output Size | Use Case |
|------|---------|-------------|----------|
| `transform_human_read.py` | Prettify HTML/CSS | Same | Debugging, development |
| `transform_classname.py` | Minify class/ID names | -25% | Production deployment |
| `transform_mini_token.py` | Maximum compression | -40-50% | Training datasets |
| `transform_cssFirst.py` | Consolidate CSS | Same | Best practices |
| **transform_meaningful_css.py** | **Semantic naming** | **Same** | **Code understanding, education** |

## Limitations

1. **Heuristic-Based**: Name generation uses pattern matching, not true semantic understanding
2. **Context-Dependent**: Same CSS properties may get different names based on HTML context
3. **Generic Fallbacks**: Some classes may receive generic names if no clear pattern is detected
4. **English Names Only**: Generated names are in English

## Future Enhancements

Potential improvements:

- [ ] Machine learning model for better semantic inference
- [ ] Support for custom naming conventions (BEM, SMACSS, etc.)
- [ ] Configuration file for pattern customization
- [ ] Preserve safelist of important class names
- [ ] Support for CSS preprocessor syntax (SCSS, LESS)
- [ ] Multi-language semantic names

## License

This tool is part of the webpage vision dataset transformation pipeline.

## Related Scripts

- `../transform_from_json_huamn_read/`: Prettify HTML/CSS for readability
- `../transform_from_json_classname/`: Minify class/ID names for compression
- `../transfrom_from_json_miniToken/`: Maximum compression with CSS purging
- `../transform_from_json_human_read_cssFirst/`: CSS consolidation to `<head>`
