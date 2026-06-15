# DPO Dataset Transformation

Transform HTML/CSS conversation dataset to DPO (Direct Preference Optimization) format with synthetic "loopy" rejected samples.

## Overview

This tool generates DPO training data where:
- **chosen**: Original ground truth HTML/CSS response
- **rejected**: Synthetic loopy generation that simulates a model getting stuck in repetition

## Usage

```bash
# Basic usage
./run_transform_dpo.sh --input <input.json> --output <output.json>

# Process first 1000 entries for testing
./run_transform_dpo.sh -i input.json -o output_dpo.json --limit 1000

# With custom random seed
./run_transform_dpo.sh -i input.json -o output_dpo.json --seed 12345
```

## Arguments

| Argument | Short | Description | Required |
|----------|-------|-------------|----------|
| `--input` | `-i` | Input JSON file with conversation data | Yes |
| `--output` | `-o` | Output JSON file for DPO data | Yes |
| `--limit` | `-l` | Limit to first N entries | No |
| `--seed` | `-s` | Random seed (default: 42) | No |

## Input Format

```json
[
  {
    "id": "entry_id",
    "image": "path/to/image.png",
    "conversations": [
      {"from": "human", "value": "User prompt..."},
      {"from": "gpt", "value": "<!DOCTYPE html>..."}
    ]
  }
]
```

## Output Format

```json
[
  {
    "id": "unique-uuid",
    "image": "path/to/image.png",
    "prompt": "<image>\nUser prompt...",
    "chosen": "<!DOCTYPE html>...",
    "rejected": "<!DOCTYPE html>... [loopy content]"
  }
]
```

## Loop Types

The tool generates 5 types of loopy content:

### 1. Character Loop
Repeats 1-2 characters from the starting point:
```html
<!DOCTYPE html><htmlttttttttttttttttttttttttttt
```

### 2. Tag Loop
Repeats HTML tags:
```html
<!DOCTYPE html><html><body><div><div><div><div><div>
```

### 3. HTML Section Loop
Repeats complete HTML elements with content:
```html
<div><p>hello</p></div><div><p>hello</p></div><div><p>hello</p></div>
```

### 4. Incrementing Tag Loop
Generates tags with incrementing class numbers:
```html
<div class="header-1"><div class="header-2"><div class="header-3">...
```

### 5. CSS Rule Loop
Repeats CSS rule blocks (when starting point is in `<style>`):
```css
.selector { color: red }
.selector { color: red }
.selector { color: red }
```

## Loop Generation Logic

1. **Starting Point**: Random position between 5% and 75% of content length
2. **Context Detection**:
   - If in `<style>` block → CSS rule loop
   - If in HTML region → randomly choose from 4 HTML loop types
3. **Fill Behavior**: Loop unit repeats until matching original content length

## Output Files

- `output.json` - DPO formatted data
- `output.json.skipped.json` - Skip report with reasons (if any entries skipped)

## Statistics

The tool outputs statistics including:
- Total entries processed
- Successfully transformed entries
- Skipped entries with reasons
- Loop type distribution (character, tag, section, incrementing, CSS)

## Example

```bash
# Transform first 1000 entries
./run_transform_dpo.sh \
  --input /path/to/coco_webdataset_human_read_8000.json \
  --output /path/to/dpo_dataset.json \
  --limit 1000

# Or use Python directly
python3 transform_to_dpo.py \
  --input /path/to/input.json \
  --output /path/to/output.json \
  --limit 1000 \
  --seed 42
```
