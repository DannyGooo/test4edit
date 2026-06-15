#!/usr/bin/env python3
"""
Transform JSON entries with whitespace-only minification:
- Remove whitespace between HTML tags (make HTML single-line)
- Collapse CSS whitespace inside <style> tags
- Compact JSON output (no indentation)

NO content alteration: class names, IDs, hex colors, text content all preserved.
Only uses stdlib: json, re, argparse, pathlib, sys.
"""

import json
import re
import argparse
from pathlib import Path
import sys

BLOCK_TAGS = frozenset({
    'address', 'article', 'aside', 'blockquote', 'body', 'br',
    'dd', 'details', 'dialog', 'div', 'dl', 'dt',
    'fieldset', 'figcaption', 'figure', 'footer', 'form',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'head', 'header', 'hr', 'html',
    'li', 'link', 'main', 'meta', 'nav', 'ol',
    'p', 'pre', 'script', 'section', 'style', 'summary',
    'table', 'tbody', 'td', 'tfoot', 'th', 'thead', 'title', 'tr', 'ul',
})


def minify_css_content(css: str) -> str:
    """Collapse CSS whitespace only. No hex color compression or renaming."""
    # Remove CSS comments
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)
    # Remove whitespace around braces, colons, semicolons, commas
    css = re.sub(r'\s*{\s*', '{', css)
    css = re.sub(r'\s*}\s*', '}', css)
    css = re.sub(r'\s*:\s*', ':', css)
    css = re.sub(r'\s*;\s*', ';', css)
    css = re.sub(r'\s*,\s*', ',', css)
    # Remove trailing semicolons before }
    css = re.sub(r';\s*}', '}', css)
    # Collapse remaining whitespace
    css = re.sub(r'\s+', ' ', css)
    return css.strip()


def minify_html_whitespace(html: str) -> str:
    """
    Minify HTML by:
    1. Collapsing CSS whitespace inside <style> tags
    2. Removing whitespace between tags (preserves text content whitespace)
    """
    # Minify content inside <style> tags
    def minify_style_match(match):
        opening_tag = match.group(1)
        css_content = match.group(2)
        return opening_tag + minify_css_content(css_content) + '</style>'

    html = re.sub(r'(<style[^>]*>)(.*?)(</style>)', minify_style_match, html, flags=re.DOTALL)

    # Handle <!DOCTYPE ...> whitespace separately
    html = re.sub(r'(<!DOCTYPE[^>]*>)\s+', r'\1', html, flags=re.IGNORECASE)

    # Remove whitespace between tags only when at least one is block-level
    def _strip_block_ws(m):
        tag1 = m.group(1).lower()
        tag2 = m.group(3).lower()
        if tag1 in BLOCK_TAGS or tag2 in BLOCK_TAGS:
            return m.group(0)[:-len(m.group(2))]  # strip the whitespace
        return m.group(0)  # preserve

    html = re.sub(r'</?([\w]+)[^>]*>(\s+)(?=</?([\w]+))', _strip_block_ws, html)

    # Collapse leading/trailing whitespace
    html = html.strip()

    return html


def transform_html_entry(html_content: str):
    """Minify HTML whitespace and return (minified_html, stats)."""
    original_size = len(html_content)
    minified = minify_html_whitespace(html_content)
    minified_size = len(minified)

    stats = {
        'processed': True,
        'original_size': original_size,
        'minified_size': minified_size,
        'bytes_removed': original_size - minified_size,
    }
    return minified, stats


def get_gpt_response_from_entry(entry: dict) -> str:
    """Extract the GPT response from a conversation entry."""
    conversations = entry.get('conversations', [])
    for conv in conversations:
        if conv.get('from') == 'gpt':
            return conv.get('value', '')
    return ''


def transform_json(input_path: str, output_path: str) -> dict:
    """Main loop: load JSON, minify whitespace in GPT responses, write compact JSON."""
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Input JSON must be an array")

    total_entries = len(data)
    transformed_data = []

    entries_processed = 0
    entries_skipped = 0
    total_bytes_removed = 0
    skip_reasons = {}
    skipped_entries = []

    print(f"Processing {total_entries} entries (whitespace-only minification)...")

    for i, entry in enumerate(data):
        if (i + 1) % 100 == 0:
            print(f"Progress: {i + 1}/{total_entries} entries processed")

        entry_id = entry.get('id', f'entry_{i}')
        gpt_response = get_gpt_response_from_entry(entry)

        if not gpt_response:
            transformed_data.append(entry)
            entries_skipped += 1
            reason = 'no_gpt_response'
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skipped_entries.append({
                'id': entry_id,
                'reason': reason,
                'message': 'No GPT response found in conversations'
            })
            continue

        try:
            transformed_html, stats = transform_html_entry(gpt_response)

            new_entry = entry.copy()
            for conv in new_entry.get('conversations', []):
                if conv.get('from') == 'gpt':
                    conv['value'] = transformed_html

            transformed_data.append(new_entry)
            entries_processed += 1
            total_bytes_removed += stats.get('bytes_removed', 0)

        except Exception as e:
            transformed_data.append(entry)
            entries_skipped += 1
            reason = 'error'
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skipped_entries.append({
                'id': entry_id,
                'reason': reason,
                'message': f'Processing error: {e}'
            })

    # Write compact JSON output
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(transformed_data, f, ensure_ascii=False, separators=(',', ':'))

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
        'total_bytes_removed': total_bytes_removed,
        'input_path': input_path,
        'output_path': output_path,
        'skip_report_path': skip_report_path if entries_skipped > 0 else None
    }


def main():
    parser = argparse.ArgumentParser(
        description='Transform JSON with whitespace-only HTML minification (no content alteration)'
    )
    parser.add_argument('--input', type=str, required=True, help='Input JSON file path')
    parser.add_argument('--output', type=str, required=True, help='Output JSON file path')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        stats = transform_json(args.input, args.output)

        print("\n" + "=" * 60)
        print("WHITESPACE MINIFICATION SUMMARY")
        print("=" * 60)
        print(f"Total entries:          {stats['total_entries']}")
        print(f"Entries processed:      {stats['entries_processed']}")
        print(f"Entries skipped:        {stats['entries_skipped']}")

        if stats['entries_skipped'] > 0 and stats.get('skip_reasons'):
            print("\nSkip Reason Breakdown:")
            for reason, count in sorted(stats['skip_reasons'].items(), key=lambda x: x[1], reverse=True):
                print(f"  - {reason.replace('_', ' ').title()}: {count}")

        print(f"\nTotal bytes removed:    {stats['total_bytes_removed']:,}")
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
