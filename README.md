# PurgeCSS HTML Transformer

A Node.js script that transforms HTML files by processing their CSS through PurgeCSS, removing unused styles and optimizing file size.

## Features

- Processes inline `<style>` tags in HTML files
- **Merges multiple `<style>` tags into a single tag in `<head>`**
- **Moves styles from `<body>` to `<head>` for optimal rendering**
- Removes unused CSS based on actual HTML content
- Creates `<head>` element if missing
- Supports glob patterns for batch processing
- **Processes HTML files inside tar archives** (extract, purge, re-archive)
- Dry-run mode to preview changes
- Safelist support to preserve specific CSS classes
- Detailed reporting of size reductions
- Resume capability (skip already processed archives)

## Installation

Install the required dependencies:

```bash
npm install
```

## Usage

### Basic Usage

Process a single HTML file:

```bash
node purgecss-html.js index.html
```

### Glob Patterns

Process multiple files using glob patterns:

```bash
node purgecss-html.js "**/*.html"
node purgecss-html.js "src/**/*.html"
node purgecss-html.js page1.html page2.html page3.html
```

### Dry Run

Preview changes without modifying files:

```bash
node purgecss-html.js --dry-run index.html
```

### Safelist

Preserve specific CSS classes (useful for dynamically added classes):

```bash
node purgecss-html.js --safelist "active,selected,highlight" index.html
```

### Combined Options

```bash
node purgecss-html.js --dry-run --safelist "modal,dropdown" "dist/**/*.html"
```

### Quiet Mode

Process files with minimal output (only summary):

```bash
node purgecss-html.js --quiet "**/*.html"
```

## Command Line Options (purgecss-html.js)

| Option | Alias | Description |
|--------|-------|-------------|
| `--dry-run` | `-d` | Preview changes without modifying files |
| `--quiet` | `-q` | Minimal output (only summary) |
| `--safelist <classes>` | `-s` | Comma-separated list of CSS classes to preserve |
| `--help` | `-h` | Show help message |

---

## Processing Tar Archives

For large datasets stored in tar archives (e.g., webdataset format), use the `process-tar-archives.js` script to extract, process, and re-archive HTML files.

### Basic Tar Processing

Process all tar files in a directory:

```bash
node process-tar-archives.js /path/to/tar/files
```

This will:
1. Extract each tar file to a temporary directory
2. Process all HTML files with PurgeCSS
3. Create new tar files with `_purged.tar` suffix
4. Clean up temporary files

### Tar Processing Options

Specify output directory:

```bash
node process-tar-archives.js --output ./processed /path/to/tar/files
```

Skip already processed files (resume):

```bash
node process-tar-archives.js --skip-existing /path/to/tar/files
```

Custom temporary directory:

```bash
node process-tar-archives.js --temp /custom/tmp /path/to/tar/files
```

Process specific pattern:

```bash
node process-tar-archives.js --pattern "webdataset_*.tar" /path/to/tar/files
```

With safelist:

```bash
node process-tar-archives.js --safelist "modal,dropdown,active" /path/to/tar/files
```

### Command Line Options (process-tar-archives.js)

| Option | Alias | Description |
|--------|-------|-------------|
| `--output <dir>` | `-o` | Output directory for purged tar files (default: same as input) |
| `--temp <dir>` | `-t` | Temporary directory for extraction (default: /tmp/purgecss-temp) |
| `--safelist <classes>` | `-s` | Comma-separated list of CSS classes to preserve |
| `--skip-existing` | `-k` | Skip processing if output file already exists |
| `--pattern <pattern>` | `-p` | Tar file pattern to match (default: *.tar) |
| `--help` | `-h` | Show help message |

### Example: Process 100k HTML Files

```bash
# Process all tar archives in the dataset directory
node process-tar-archives.js \
  --output ./processed_dataset \
  --skip-existing \
  /home/user/dataset/screenshots_with_html_100k

# Output:
# Found 17 tar file(s) to process
# [1/17] Processing webdataset_chunk_00000.tar...
#   Extracting webdataset_chunk_00000.tar...
#   Processing 5000 HTML files...
#   Creating purged archive...
#   ✓ Complete
#   Files with styles: 4850/5000
#   CSS removed: 245678 bytes (45.23%)
#   Output: webdataset_chunk_00000_purged.tar
# ...
```

### NPM Scripts

```bash
# Process individual HTML files
npm run purge -- index.html

# Process tar archives
npm run purge-tar -- /path/to/tar/files
```

## How It Works

1. **Parse HTML**: The script reads your HTML file and parses it using Cheerio
2. **Extract CSS**: It extracts CSS from all `<style>` tags (wherever they are in the document)
3. **Merge CSS**: All CSS from multiple `<style>` tags is merged into a single block
4. **Analyze Usage**: PurgeCSS analyzes which CSS rules are actually used in the HTML
5. **Remove Unused CSS**: Unused CSS rules are removed
6. **Ensure `<head>` exists**: Creates a `<head>` element if it doesn't exist
7. **Move to `<head>`**: Places a single `<style>` tag with purged CSS in the `<head>` element
8. **Report**: A detailed summary shows size reductions

**Key Feature**: All `<style>` tags (even those in `<body>`) are merged and moved to `<head>` for optimal browser rendering.

## Example

### Before

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    .header { color: blue; }
    .unused-class { color: red; }
  </style>
</head>
<body>
  <div class="header">Header</div>

  <style>
    .content { padding: 20px; }
    .another-unused { font-size: 99px; }
  </style>

  <div class="content">Content</div>
</body>
</html>
```

### After

```html
<!DOCTYPE html>
<html>
<head>
  <style>
    .header { color: blue; }
    .content { padding: 20px; }
  </style>
</head>
<body>
  <div class="header">Header</div>



  <div class="content">Content</div>
</body>
</html>
```

**Changes**:
- Both `<style>` tags merged into one in `<head>`
- Unused classes (`.unused-class`, `.another-unused`) removed
- `<style>` tag removed from `<body>`

### Output

```
Processing 1 file(s)...

Results:
========

✓ /path/to/index.html
  Styles processed: 1
  Original size: 142 bytes
  Purged size: 35 bytes
  Removed: 107 bytes (75.35%)

Summary:
========
Files processed: 1/1
Total CSS removed: 107 bytes
```

## Important Notes

- **Inline styles** (`style="..."` attributes) are not processed as they are already "used" by definition
- The script **modifies files in place** unless `--dry-run` is specified
- Always test with `--dry-run` first to preview changes
- Consider version control before running on important files

## NPM Script

You can also use the npm script defined in package.json:

```bash
npm run purge -- index.html
npm run purge -- --dry-run "**/*.html"
```

## Requirements

- Node.js 14 or higher
- Dependencies (auto-installed with `npm install`):
  - purgecss
  - cheerio
  - glob
  - tar (for tar archive processing)

## License

MIT
