#!/usr/bin/env python3
"""
Transform JSON entries by minifying CSS class names and IDs in HTML/CSS content.

This script processes JSON files containing HTML/CSS conversations and minifies
class names and IDs to reduce file size, similar to purgecss-html.js functionality.
"""

import json
import re
import argparse
from pathlib import Path
from typing import Dict, Set, Tuple, List
from bs4 import BeautifulSoup, Comment
import sys

# Reserved keywords to avoid in minified names (ad-blockers, HTML/CSS reserved words)
RESERVED_NAMES = {'ad', 'ads', 'banner', 'if', 'do', 'for'}


def generate_short_name(index: int) -> str:
    """
    Generate a short name from an index (a-z, then aa-zz, then aaa-zzz, etc.)

    Args:
        index: The index to convert

    Returns:
        The short name string
    """
    name = ''
    num = index

    # Determine length (1 char, 2 char, 3 char, etc.)
    length = 1
    threshold = 26

    while num >= threshold:
        num -= threshold
        length += 1
        threshold = 26 ** length

    # Convert to base-26 letters
    for i in range(length):
        name = chr(97 + (num % 26)) + name
        num = num // 26

    # Skip reserved names
    if name in RESERVED_NAMES:
        return generate_short_name(index + 1)

    return name


def extract_classes_and_ids(soup: BeautifulSoup, css_content: str) -> Tuple[Set[str], Set[str]]:
    """
    Extract all classes and IDs from HTML and CSS.

    Args:
        soup: BeautifulSoup object of the HTML
        css_content: CSS content as string

    Returns:
        Tuple of (classes_set, ids_set)
    """
    classes = set()
    ids = set()

    # Extract from HTML class attributes
    for elem in soup.find_all(class_=True):
        class_attr = elem.get('class', [])
        if isinstance(class_attr, list):
            classes.update(c.strip() for c in class_attr if c.strip())
        else:
            classes.update(c.strip() for c in str(class_attr).split() if c.strip())

    # Extract from HTML id attributes
    for elem in soup.find_all(id=True):
        id_attr = elem.get('id', '')
        if id_attr and id_attr.strip():
            ids.add(id_attr.strip())

    # Extract from CSS using regex
    # Class selectors: .classname
    class_matches = re.finditer(r'\\.([a-zA-Z_][\\w-]*)', css_content)
    for match in class_matches:
        classes.add(match.group(1))

    # ID selectors: #idname
    id_matches = re.finditer(r'#([a-zA-Z_][\\w-]*)', css_content)
    for match in id_matches:
        ids.add(match.group(1))

    return classes, ids


def build_minification_mapping(names: Set[str], safelist: List[str] = None) -> Dict[str, str]:
    """
    Build mapping from original names to minified names.

    Args:
        names: Set of original names
        safelist: Names to exclude from minification

    Returns:
        Dictionary mapping original -> minified names
    """
    mapping = {}
    safelist_set = set(safelist) if safelist else set()

    # Filter out safelisted names and sort by length (longer names first for better compression)
    names_to_minify = sorted(
        [name for name in names if name not in safelist_set],
        key=lambda x: len(x),
        reverse=True
    )

    # Generate short names
    for index, name in enumerate(names_to_minify):
        mapping[name] = generate_short_name(index)

    return mapping


def minify_css_selectors(css_content: str, class_mapping: Dict[str, str], id_mapping: Dict[str, str]) -> str:
    """
    Minify CSS selectors by replacing class and ID names.

    Args:
        css_content: Original CSS content
        class_mapping: Dictionary mapping original class names to minified names
        id_mapping: Dictionary mapping original ID names to minified names

    Returns:
        CSS content with minified selectors
    """
    result = css_content

    # Replace class selectors (sort by length descending to avoid partial replacements)
    for original, minified in sorted(class_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        # Match .classname with word boundary
        pattern = r'\.' + re.escape(original) + r'\b'
        replacement = '.' + minified
        result = re.sub(pattern, replacement, result)

        # Also handle attribute selectors like [class~="classname"]
        attr_pattern = r'(\[class[~*^$|]?=["\']?)' + re.escape(original) + r'\b'
        attr_replacement = r'\g<1>' + minified
        result = re.sub(attr_pattern, attr_replacement, result)

    # Replace ID selectors
    for original, minified in sorted(id_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        # Match #idname with word boundary
        pattern = r'#' + re.escape(original) + r'\b'
        replacement = '#' + minified
        result = re.sub(pattern, replacement, result)

        # Also handle attribute selectors like [id="idname"]
        attr_pattern = r'(\[id[~*^$|]?=["\']?)' + re.escape(original) + r'\b'
        attr_replacement = r'\g<1>' + minified
        result = re.sub(attr_pattern, attr_replacement, result)

    return result


def minify_html_attributes(soup: BeautifulSoup, class_mapping: Dict[str, str], id_mapping: Dict[str, str]) -> None:
    """
    Minify class and id attributes in HTML (modifies soup in-place).

    Args:
        soup: BeautifulSoup object to modify
        class_mapping: Dictionary mapping original class names to minified names
        id_mapping: Dictionary mapping original ID names to minified names
    """
    # Minify class attributes
    for elem in soup.find_all(class_=True):
        class_attr = elem.get('class', [])
        if isinstance(class_attr, list):
            classes = class_attr
        else:
            classes = str(class_attr).split()

        minified_classes = [
            class_mapping.get(cls, cls) for cls in classes if cls.strip()
        ]

        if minified_classes:
            elem['class'] = minified_classes

    # Minify id attributes
    for elem in soup.find_all(id=True):
        id_attr = elem.get('id', '')
        if id_attr and id_attr.strip():
            if id_attr in id_mapping:
                elem['id'] = id_mapping[id_attr]


def minify_html_content(html: str) -> str:
    """
    Minify HTML by removing whitespace and comments.

    Args:
        html: Original HTML content

    Returns:
        Minified HTML content
    """
    try:
        import htmlmin
        return htmlmin.minify(
            html,
            remove_comments=True,
            remove_empty_space=True,
            reduce_boolean_attributes=True
        )
    except ImportError:
        # Fallback: basic minification without htmlmin
        # Remove HTML comments
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        # Remove multiple whitespaces
        html = re.sub(r'\\s+', ' ', html)
        # Remove whitespace around tags
        html = re.sub(r'>\\s+<', '><', html)
        return html.strip()


def transform_html_entry(html_content: str, safelist: List[str] = None, minify_html: bool = True) -> Tuple[str, Dict]:
    """
    Transform HTML content by minifying class names and IDs.

    Args:
        html_content: Original HTML content
        safelist: Class/ID names to preserve
        minify_html: Whether to minify HTML structure

    Returns:
        Tuple of (transformed_html, statistics_dict)
    """
    # Parse HTML
    soup = BeautifulSoup(html_content, 'html.parser')

    # Find all style tags
    style_tags = soup.find_all('style')

    if not style_tags:
        return html_content, {
            'processed': False,
            'reason': 'no_style_tags',
            'message': 'No <style> tags found'
        }

    # Collect all CSS from style tags
    all_css = []
    original_css_size = 0

    for style_tag in style_tags:
        css = style_tag.string or ''
        if css.strip():
            all_css.append(css)
            original_css_size += len(css)

    if not all_css:
        return html_content, {
            'processed': False,
            'reason': 'no_css_content',
            'message': 'No CSS content found in <style> tags'
        }

    # Merge all CSS
    merged_css = '\\n\\n'.join(all_css)

    # Extract classes and IDs
    classes, ids = extract_classes_and_ids(soup, merged_css)

    # Build minification mappings
    class_mapping = build_minification_mapping(classes, safelist)
    id_mapping = build_minification_mapping(ids, safelist)

    # Calculate original name lengths
    original_class_length = sum(len(c) for c in classes)
    original_id_length = sum(len(i) for i in ids)
    original_names_length = original_class_length + original_id_length

    # Minify CSS selectors
    minified_css = minify_css_selectors(merged_css, class_mapping, id_mapping)

    # Minify HTML attributes
    minify_html_attributes(soup, class_mapping, id_mapping)

    # Calculate minified name lengths
    minified_class_length = sum(len(v) for v in class_mapping.values())
    minified_id_length = sum(len(v) for v in id_mapping.values())
    minified_names_length = minified_class_length + minified_id_length
    name_bytes_removed = original_names_length - minified_names_length

    # Remove all existing style tags
    for style_tag in style_tags:
        style_tag.decompose()

    # Add minified CSS as a single style tag in body
    body = soup.find('body')
    if not body:
        # Create body if it doesn't exist
        html_tag = soup.find('html')
        if not html_tag:
            html_tag = soup.new_tag('html')
            soup.append(html_tag)
        body = soup.new_tag('body')
        html_tag.append(body)

    new_style = soup.new_tag('style')
    new_style.string = minified_css
    body.append(new_style)

    # Get the processed HTML
    output_html = str(soup)
    html_size_before_minify = len(output_html)
    html_bytes_removed = 0

    # Minify HTML if enabled
    if minify_html:
        output_html = minify_html_content(output_html)
        html_bytes_removed = html_size_before_minify - len(output_html)

    # Calculate CSS size change
    css_bytes_removed = original_css_size - len(minified_css)

    stats = {
        'processed': True,
        'classes_minified': len(class_mapping),
        'ids_minified': len(id_mapping),
        'original_css_size': original_css_size,
        'minified_css_size': len(minified_css),
        'css_bytes_removed': css_bytes_removed,
        'name_bytes_removed': name_bytes_removed,
        'html_bytes_removed': html_bytes_removed if minify_html else 0,
        'total_bytes_removed': css_bytes_removed + name_bytes_removed + html_bytes_removed
    }

    return output_html, stats


def get_gpt_response_from_entry(entry: dict) -> str:
    """
    Extract the GPT response from a conversation entry.

    Args:
        entry: JSON entry with conversations

    Returns:
        The GPT response text
    """
    conversations = entry.get('conversations', [])
    for conv in conversations:
        if conv.get('from') == 'gpt':
            return conv.get('value', '')
    return ''


def transform_json_by_classname(
    input_path: str,
    output_path: str,
    safelist: List[str] = None,
    minify_html: bool = True
) -> Dict:
    """
    Transform JSON entries by minifying class names in HTML/CSS content.

    Args:
        input_path: Path to input JSON file
        output_path: Path to output JSON file
        safelist: Class/ID names to preserve
        minify_html: Whether to minify HTML structure

    Returns:
        Dictionary with transformation statistics
    """
    # Load input JSON
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError("Input JSON must be an array")

    total_entries = len(data)
    transformed_data = []

    # Statistics
    total_classes_minified = 0
    total_ids_minified = 0
    total_bytes_removed = 0
    entries_processed = 0
    entries_skipped = 0

    # Skip tracking
    skip_reasons = {}
    skipped_entries = []

    print(f"Processing {total_entries} entries...")

    for i, entry in enumerate(data):
        if (i + 1) % 100 == 0:
            print(f"Progress: {i + 1}/{total_entries} entries processed")

        entry_id = entry.get('id', f'entry_{i}')

        # Extract GPT response
        gpt_response = get_gpt_response_from_entry(entry)

        if not gpt_response:
            transformed_data.append(entry)
            entries_skipped += 1

            # Track skip reason
            reason = 'no_gpt_response'
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skipped_entries.append({
                'id': entry_id,
                'reason': reason,
                'message': 'No GPT response found in conversations'
            })
            continue

        # Transform the HTML/CSS
        try:
            transformed_html, stats = transform_html_entry(gpt_response, safelist, minify_html)

            if stats.get('processed'):
                # Update the GPT response in the entry
                new_entry = entry.copy()
                for conv in new_entry.get('conversations', []):
                    if conv.get('from') == 'gpt':
                        conv['value'] = transformed_html

                transformed_data.append(new_entry)

                # Update statistics
                entries_processed += 1
                total_classes_minified += stats.get('classes_minified', 0)
                total_ids_minified += stats.get('ids_minified', 0)
                total_bytes_removed += stats.get('total_bytes_removed', 0)
            else:
                transformed_data.append(entry)
                entries_skipped += 1

                # Track skip reason
                reason = stats.get('reason', 'unknown')
                message = stats.get('message', 'Unknown error')
                skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
                skipped_entries.append({
                    'id': entry_id,
                    'reason': reason,
                    'message': message
                })
        except Exception as e:
            # Handle processing errors
            transformed_data.append(entry)
            entries_skipped += 1

            # Track skip reason
            reason = 'error'
            error_msg = str(e)
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skipped_entries.append({
                'id': entry_id,
                'reason': reason,
                'message': f'Processing error: {error_msg}',
                'error': error_msg
            })

    # Write output JSON
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(transformed_data, f, ensure_ascii=False, indent=2)

    # Write skip report if there are skipped entries
    skip_report_path = output_path + '.skipped.json'
    if entries_skipped > 0:
        skip_report = {
            'total_skipped': entries_skipped,
            'skip_reasons': skip_reasons,
            'skipped_entries': skipped_entries
        }
        with open(skip_report_path, 'w', encoding='utf-8') as f:
            json.dump(skip_report, f, ensure_ascii=False, indent=2)

    # Return statistics
    return {
        'total_entries': total_entries,
        'entries_processed': entries_processed,
        'entries_skipped': entries_skipped,
        'skip_reasons': skip_reasons,
        'total_classes_minified': total_classes_minified,
        'total_ids_minified': total_ids_minified,
        'total_bytes_removed': total_bytes_removed,
        'input_path': input_path,
        'output_path': output_path,
        'skip_report_path': skip_report_path if entries_skipped > 0 else None
    }


def main():
    parser = argparse.ArgumentParser(
        description='Transform JSON entries by minifying CSS class names and IDs'
    )
    parser.add_argument(
        '--input',
        type=str,
        required=True,
        help='Path to input JSON file'
    )
    parser.add_argument(
        '--output',
        type=str,
        required=True,
        help='Path to output JSON file'
    )
    parser.add_argument(
        '--safelist',
        type=str,
        default='',
        help='Comma-separated list of class/ID names to preserve (not minify)'
    )
    parser.add_argument(
        '--no-html-minify',
        action='store_true',
        help='Disable HTML minification (keep formatting)'
    )

    args = parser.parse_args()

    # Parse safelist
    safelist = [s.strip() for s in args.safelist.split(',') if s.strip()] if args.safelist else None

    # Check input file exists
    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        # Transform the JSON
        stats = transform_json_by_classname(
            args.input,
            args.output,
            safelist=safelist,
            minify_html=not args.no_html_minify
        )

        # Print summary
        print("\\n" + "=" * 60)
        print("TRANSFORMATION SUMMARY")
        print("=" * 60)
        print(f"Total entries:          {stats['total_entries']}")
        print(f"Entries processed:      {stats['entries_processed']}")
        print(f"Entries skipped:        {stats['entries_skipped']}")

        # Print skip reason breakdown if there are skipped entries
        if stats['entries_skipped'] > 0 and stats.get('skip_reasons'):
            print("\\nSkip Reason Breakdown:")
            for reason, count in sorted(stats['skip_reasons'].items(), key=lambda x: x[1], reverse=True):
                # Format reason for display
                reason_display = reason.replace('_', ' ').title()
                print(f"  - {reason_display}: {count}")

        print(f"\\nClasses minified:       {stats['total_classes_minified']}")
        print(f"IDs minified:           {stats['total_ids_minified']}")
        print(f"Total bytes removed:    {stats['total_bytes_removed']:,}")
        print(f"\\nOutput written to:      {stats['output_path']}")

        # Print skip report path if it exists
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
