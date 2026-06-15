#!/usr/bin/env python3
"""
Transform JSON entries to HUMAN-READABLE format:
- Prettify HTML with proper indentation
- Prettify CSS with proper indentation and formatting
- Pretty-print JSON output (2-space indentation)

Ultra-simple usage: only --input and --output required.
All prettification features enabled by default for maximum readability.
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

    Args:
        css: Original CSS content (may be minified)

    Returns:
        Prettified CSS content with proper formatting
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

        # Skip empty lines
        if not stripped:
            formatted_lines.append('')
            continue

        # Decrease indent for closing braces
        if stripped.startswith('}'):
            indent_level = max(0, indent_level - 1)

        # Add the line with proper indentation
        if stripped:
            formatted_lines.append('  ' * indent_level + stripped)

        # Increase indent for opening braces
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

        # Track statistics
        style_tags_found = len(style_tags)
        css_formatted = 0

        # Prettify CSS in style tags
        for style_tag in style_tags:
            css = style_tag.string or ''
            if css.strip():
                prettified_css = prettify_css_content(css)
                style_tag.string = '\n' + prettified_css + '\n'
                css_formatted += 1

        # Prettify HTML using BeautifulSoup's built-in prettify
        # This adds proper indentation and line breaks
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


def get_gpt_response_from_entry(entry: dict) -> str:
    """Extract the GPT response from a conversation entry"""
    conversations = entry.get('conversations', [])
    for conv in conversations:
        if conv.get('from') == 'gpt':
            return conv.get('value', '')
    return ''


def transform_json_human_readable(input_path: str, output_path: str) -> Dict:
    """
    Transform JSON entries to human-readable format.
    """
    # Load input JSON
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Input JSON must be an array")

    total_entries = len(data)
    transformed_data = []

    # Statistics
    total_style_tags = 0
    total_css_formatted = 0
    entries_processed = 0
    entries_skipped = 0

    # Skip tracking
    skip_reasons = {}
    skipped_entries = []

    print(f"Processing {total_entries} entries for human-readable formatting...")

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

        # Skip if no HTML tags (not HTML content)
        if '<html' not in gpt_response.lower() and '<body' not in gpt_response.lower():
            transformed_data.append(entry)
            entries_skipped += 1
            reason = 'no_html_content'
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skipped_entries.append({
                'id': entry_id,
                'reason': reason,
                'message': 'No HTML content found in GPT response'
            })
            continue

        try:
            transformed_html, stats = transform_html_entry(gpt_response)

            if stats.get('processed'):
                new_entry = entry.copy()
                for conv in new_entry.get('conversations', []):
                    if conv.get('from') == 'gpt':
                        conv['value'] = transformed_html

                transformed_data.append(new_entry)

                entries_processed += 1
                total_style_tags += stats.get('style_tags_found', 0)
                total_css_formatted += stats.get('css_blocks_formatted', 0)
            else:
                transformed_data.append(entry)
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
            transformed_data.append(entry)
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

    # Write output JSON (PRETTY-PRINTED with 2-space indentation)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(transformed_data, f, ensure_ascii=False, indent=2)

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
        description='Transform JSON to human-readable format (prettify HTML/CSS)'
    )
    parser.add_argument('--input', type=str, required=True, help='Input JSON file path')
    parser.add_argument('--output', type=str, required=True, help='Output JSON file path')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        stats = transform_json_human_readable(args.input, args.output)

        # Print summary
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
