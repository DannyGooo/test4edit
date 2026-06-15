#!/usr/bin/env python3
"""
Transform ms_swift JSONL entries to HUMAN-READABLE format:
- Prettify HTML with proper indentation
- Prettify CSS with proper indentation and formatting
- Output as JSONL (one JSON object per line)

Usage: python3 transform_human_read.py --input data.jsonl --output data_readable.jsonl
"""

import json
import re
import argparse
from pathlib import Path
from typing import Dict, Tuple
from bs4 import BeautifulSoup
import sys


def prettify_css_content(css: str) -> str:
    """
    Prettify CSS content: add proper indentation, line breaks, and spacing.
    """
    # Remove existing excessive whitespace first
    css = re.sub(r'\s+', ' ', css).strip()

    # Add line breaks after opening braces
    css = re.sub(r'\{\s*', '{\n  ', css)

    # Add line breaks after semicolons (but not inside quotes)
    css = re.sub(r';\s*', ';\n  ', css)

    # Add line breaks before closing braces
    css = re.sub(r'\s*}', '\n}', css)

    # Add spacing around colons
    css = re.sub(r'\s*:\s*', ': ', css)

    # Add line breaks after closing braces
    css = re.sub(r'}\s*', '}\n\n', css)

    # Remove trailing whitespace on empty property lines
    css = re.sub(r'\n\s+\n', '\n\n', css)

    # Fix indentation for nested rules (media queries, etc.)
    lines = css.split('\n')
    formatted_lines = []
    indent_level = 0

    for line in lines:
        stripped = line.strip()

        if not stripped:
            formatted_lines.append('')
            continue

        if stripped.startswith('}'):
            indent_level = max(0, indent_level - 1)

        if stripped:
            formatted_lines.append('  ' * indent_level + stripped)

        if stripped.endswith('{'):
            indent_level += 1

    result = '\n'.join(formatted_lines)

    # Clean up any trailing semicolons before closing braces
    result = re.sub(r';\s*\n(\s*})', r'\n\1', result)

    # Remove excessive blank lines
    result = re.sub(r'\n\n\n+', '\n\n', result)

    return result.strip()


def transform_html_entry(html_content: str) -> Tuple[str, Dict]:
    """
    Transform HTML to human-readable format with prettified HTML and CSS.
    """
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        style_tags = soup.find_all('style')

        style_tags_found = len(style_tags)
        css_formatted = 0

        for style_tag in style_tags:
            css = style_tag.string or ''
            if css.strip():
                prettified_css = prettify_css_content(css)
                style_tag.string = '\n' + prettified_css + '\n'
                css_formatted += 1

        output_html = soup.prettify()

        stats = {
            'processed': True,
            'style_tags_found': style_tags_found,
            'css_blocks_formatted': css_formatted,
            'message': 'HTML and CSS formatted successfully'
        }

        return output_html, stats

    except Exception as e:
        return html_content, {
            'processed': False,
            'reason': 'error',
            'message': f'Error during formatting: {str(e)}',
            'error': str(e)
        }


def get_assistant_response(entry: dict) -> str:
    """Extract the assistant response from a ms_swift messages entry."""
    messages = entry.get('messages', [])
    for msg in messages:
        if msg.get('role') == 'assistant':
            return msg.get('content', '')
    return ''


def transform_jsonl_human_readable(input_path: str, output_path: str, num_samples: int = 0) -> Dict:
    """
    Transform ms_swift JSONL entries to human-readable format.
    """
    total_entries = 0
    entries_processed = 0
    entries_skipped = 0
    total_style_tags = 0
    total_css_formatted = 0
    skip_reasons = {}
    skipped_entries = []

    # Count total lines first for progress reporting
    with open(input_path, 'r', encoding='utf-8') as f:
        for _ in f:
            total_entries += 1

    if num_samples > 0:
        total_entries = min(total_entries, num_samples)
    print(f"Processing {total_entries} entries for human-readable formatting...")

    with open(input_path, 'r', encoding='utf-8') as fin, \
         open(output_path, 'w', encoding='utf-8') as fout:

        for i, line in enumerate(fin):
            if num_samples > 0 and i >= num_samples:
                break

            line = line.strip()
            if not line:
                continue

            if (i + 1) % 100 == 0:
                print(f"Progress: {i + 1}/{total_entries} entries processed")

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                entries_skipped += 1
                reason = 'invalid_json'
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    'line': i + 1,
                    'reason': reason,
                    'message': f'Invalid JSON: {str(e)}'
                })
                continue

            entry_id = entry.get('id', f'line_{i}')
            assistant_response = get_assistant_response(entry)

            if not assistant_response:
                fout.write(json.dumps(entry, ensure_ascii=False) + '\n')
                entries_skipped += 1
                reason = 'no_assistant_response'
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    'id': entry_id,
                    'reason': reason,
                    'message': 'No assistant response found in messages'
                })
                continue

            if '<html' not in assistant_response.lower() and '<body' not in assistant_response.lower():
                fout.write(json.dumps(entry, ensure_ascii=False) + '\n')
                entries_skipped += 1
                reason = 'no_html_content'
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    'id': entry_id,
                    'reason': reason,
                    'message': 'No HTML content found in assistant response'
                })
                continue

            try:
                transformed_html, stats = transform_html_entry(assistant_response)

                if stats.get('processed'):
                    new_entry = entry.copy()
                    for msg in new_entry.get('messages', []):
                        if msg.get('role') == 'assistant':
                            msg['content'] = transformed_html

                    fout.write(json.dumps(new_entry, ensure_ascii=False) + '\n')

                    entries_processed += 1
                    total_style_tags += stats.get('style_tags_found', 0)
                    total_css_formatted += stats.get('css_blocks_formatted', 0)
                else:
                    fout.write(json.dumps(entry, ensure_ascii=False) + '\n')
                    entries_skipped += 1
                    reason = stats.get('reason', 'unknown')
                    message = stats.get('message', 'Unknown error')
                    skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                    skipped_entries.append({
                        'id': entry_id,
                        'reason': reason,
                        'message': message
                    })
            except Exception as e:
                fout.write(json.dumps(entry, ensure_ascii=False) + '\n')
                entries_skipped += 1
                reason = 'error'
                error_msg = str(e)
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    'id': entry_id,
                    'reason': reason,
                    'message': f'Processing error: {error_msg}',
                    'error': error_msg
                })

    # Write skip report if needed
    skip_report_path = output_path + '.skipped.json'
    if entries_skipped > 0:
        skip_report = {
            'total_skipped': entries_skipped,
            'skip_reasons': skip_reasons,
            'skipped_entries': skipped_entries
        }
        with open(skip_report_path, 'w', encoding='utf-8') as f:
            json.dump(skip_report, f, ensure_ascii=False, indent=2)

    return {
        'total_entries': total_entries,
        'entries_processed': entries_processed,
        'entries_skipped': entries_skipped,
        'skip_reasons': skip_reasons,
        'total_style_tags': total_style_tags,
        'total_css_formatted': total_css_formatted,
        'input_path': input_path,
        'output_path': output_path,
        'skip_report_path': skip_report_path if entries_skipped > 0 else None
    }


def main():
    parser = argparse.ArgumentParser(
        description='Transform ms_swift JSONL to human-readable format (prettify HTML/CSS)'
    )
    parser.add_argument('--input', type=str, required=True, help='Input JSONL file path')
    parser.add_argument('--output', type=str, required=True, help='Output JSONL file path')
    parser.add_argument('--num_samples', type=int, default=0, help='Number of samples to process (0 = all)')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        stats = transform_jsonl_human_readable(args.input, args.output, args.num_samples)

        print("\n" + "=" * 60)
        print("HUMAN-READABLE FORMATTING SUMMARY")
        print("=" * 60)
        print(f"Total entries:          {stats['total_entries']}")
        print(f"Entries processed:      {stats['entries_processed']}")
        print(f"Entries skipped:        {stats['entries_skipped']}")

        if stats['entries_skipped'] > 0 and stats.get('skip_reasons'):
            print("\nSkip Reason Breakdown:")
            for reason, count in sorted(stats['skip_reasons'].items(), key=lambda x: x[1], reverse=True):
                reason_display = reason.replace('_', ' ').title()
                print(f"  - {reason_display}: {count}")

        print(f"\nStyle tags found:       {stats['total_style_tags']}")
        print(f"CSS blocks formatted:   {stats['total_css_formatted']}")

        print(f"\nOutput written to:      {stats['output_path']}")
        if stats.get('skip_report_path'):
            print(f"Skip report written to: {stats['skip_report_path']}")

        print("=" * 60)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
