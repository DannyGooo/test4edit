#!/usr/bin/env python3
"""
Transform JSON entries with MAXIMUM compression:
- Minify CSS class names and IDs
- Remove unused CSS (PurgeCSS)
- Minify CSS content (whitespace, comments, colors)
- Minify HTML structure
- Compact JSON output (no indentation)
- Inline small CSS blocks

Ultra-simple usage: only --input and --output required.
All compression features enabled by default for maximum size reduction.
"""

import json
import re
import argparse
from pathlib import Path
from typing import Dict, Set, Tuple, List
from bs4 import BeautifulSoup
import sys

# Reserved keywords to avoid in minified names
RESERVED_NAMES = {'ad', 'ads', 'banner', 'if', 'do', 'for'}

# Threshold for inlining CSS (characters)
INLINE_CSS_THRESHOLD = 100


def generate_short_name(index: int) -> str:
    """Generate a short name from an index (a-z, aa-zz, aaa-zzz, etc.)"""
    name = ''
    num = index
    length = 1
    threshold = 26

    while num >= threshold:
        num -= threshold
        length += 1
        threshold = 26 ** length

    for i in range(length):
        name = chr(97 + (num % 26)) + name
        num = num // 26

    if name in RESERVED_NAMES:
        return generate_short_name(index + 1)

    return name


def minify_css_content(css: str) -> str:
    """
    Minify CSS content: remove comments, whitespace, compress colors, etc.

    Args:
        css: Original CSS content

    Returns:
        Minified CSS content
    """
    # Remove CSS comments
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.DOTALL)

    # Remove whitespace around braces, colons, semicolons
    css = re.sub(r'\s*{\s*', '{', css)
    css = re.sub(r'\s*}\s*', '}', css)
    css = re.sub(r'\s*:\s*', ':', css)
    css = re.sub(r'\s*;\s*', ';', css)
    css = re.sub(r'\s*,\s*', ',', css)

    # Remove trailing semicolons before }
    css = re.sub(r';\s*}', '}', css)

    # Compress hex colors (#ffffff -> #fff)
    css = re.sub(r'#([0-9a-fA-F])\1([0-9a-fA-F])\2([0-9a-fA-F])\3\b', r'#\1\2\3', css)

    # Remove unnecessary whitespace
    css = re.sub(r'\s+', ' ', css)
    css = css.strip()

    return css


def extract_classes_and_ids(soup: BeautifulSoup, css_content: str) -> Tuple[Set[str], Set[str]]:
    """Extract all classes and IDs from HTML and CSS"""
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

    # Extract from CSS
    class_matches = re.finditer(r'\.([a-zA-Z_][\w-]*)', css_content)
    for match in class_matches:
        classes.add(match.group(1))

    id_matches = re.finditer(r'#([a-zA-Z_][\w-]*)', css_content)
    for match in id_matches:
        ids.add(match.group(1))

    return classes, ids


def build_minification_mapping(names: Set[str]) -> Dict[str, str]:
    """Build mapping from original names to minified names"""
    mapping = {}
    names_to_minify = sorted(names, key=lambda x: len(x), reverse=True)

    for index, name in enumerate(names_to_minify):
        mapping[name] = generate_short_name(index)

    return mapping


def purge_unused_css(css_content: str, html_soup: BeautifulSoup,
                     class_mapping: Dict[str, str], id_mapping: Dict[str, str]) -> str:
    """
    Remove unused CSS rules by checking if selectors exist in HTML.
    This is a simplified PurgeCSS implementation.
    """
    # Get all used classes and IDs (after mapping)
    used_classes = set()
    used_ids = set()

    for elem in html_soup.find_all(class_=True):
        class_attr = elem.get('class', [])
        if isinstance(class_attr, list):
            used_classes.update(class_attr)
        else:
            used_classes.update(str(class_attr).split())

    for elem in html_soup.find_all(id=True):
        id_attr = elem.get('id', '')
        if id_attr:
            used_ids.add(id_attr)

    # Parse CSS rules and keep only those that match used selectors
    # Simple implementation: split by } and filter rules
    rules = []
    for rule in css_content.split('}'):
        rule = rule.strip()
        if not rule:
            continue

        # Check if rule contains any used class or ID
        keep_rule = False

        # Check for used classes
        for cls in used_classes:
            if f'.{cls}' in rule or f'[class' in rule:
                keep_rule = True
                break

        # Check for used IDs
        if not keep_rule:
            for id_val in used_ids:
                if f'#{id_val}' in rule or f'[id' in rule:
                    keep_rule = True
                    break

        # Keep element selectors (div, body, etc.)
        if not keep_rule:
            # Check if it's an element selector (no . or # at start)
            selector_part = rule.split('{')[0].strip() if '{' in rule else ''
            if selector_part and not selector_part.startswith('.') and not selector_part.startswith('#'):
                keep_rule = True

        if keep_rule:
            rules.append(rule + '}')

    return ''.join(rules)


def minify_css_selectors(css_content: str, class_mapping: Dict[str, str], id_mapping: Dict[str, str]) -> str:
    """Minify CSS selectors by replacing class and ID names"""
    result = css_content

    # Replace class selectors
    for original, minified in sorted(class_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = r'\.' + re.escape(original) + r'\b'
        replacement = '.' + minified
        result = re.sub(pattern, replacement, result)

        attr_pattern = r'(\[class[~*^$|]?=["\']?)' + re.escape(original) + r'\b'
        attr_replacement = r'\g<1>' + minified
        result = re.sub(attr_pattern, attr_replacement, result)

    # Replace ID selectors
    for original, minified in sorted(id_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        pattern = r'#' + re.escape(original) + r'\b'
        replacement = '#' + minified
        result = re.sub(pattern, replacement, result)

        attr_pattern = r'(\[id[~*^$|]?=["\']?)' + re.escape(original) + r'\b'
        attr_replacement = r'\g<1>' + minified
        result = re.sub(attr_pattern, attr_replacement, result)

    return result


def minify_html_attributes(soup: BeautifulSoup, class_mapping: Dict[str, str], id_mapping: Dict[str, str]) -> None:
    """Minify class and id attributes in HTML"""
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


def css_to_inline_styles(soup: BeautifulSoup, css_content: str) -> BeautifulSoup:
    """
    Convert CSS rules to inline styles for very small CSS blocks.
    Only applies simple class-based styles.
    """
    # Parse simple CSS rules (class: {prop: value})
    class_styles = {}

    # Very basic CSS parser for simple cases
    rules = css_content.split('}')
    for rule in rules:
        if '{' not in rule:
            continue
        selector, properties = rule.split('{', 1)
        selector = selector.strip()

        # Only handle simple class selectors (.classname)
        if selector.startswith('.') and ' ' not in selector and '>' not in selector:
            class_name = selector[1:]  # Remove the dot
            class_styles[class_name] = properties.strip()

    # Apply styles to elements
    for elem in soup.find_all(class_=True):
        class_attr = elem.get('class', [])
        if isinstance(class_attr, list):
            classes = class_attr
        else:
            classes = str(class_attr).split()

        # Collect all styles for this element
        inline_styles = []
        for cls in classes:
            if cls in class_styles:
                inline_styles.append(class_styles[cls])

        if inline_styles:
            existing_style = elem.get('style', '')
            combined_style = ';'.join(inline_styles)
            if existing_style:
                combined_style = existing_style + ';' + combined_style
            elem['style'] = combined_style
            # Remove class attribute
            del elem['class']

    return soup


def minify_html_content(html: str) -> str:
    """Minify HTML structure"""
    try:
        import htmlmin
        return htmlmin.minify(
            html,
            remove_comments=True,
            remove_empty_space=True,
            reduce_boolean_attributes=True
        )
    except ImportError:
        # Fallback minification
        html = re.sub(r'<!--.*?-->', '', html, flags=re.DOTALL)
        html = re.sub(r'\s+', ' ', html)
        html = re.sub(r'>\s+<', '><', html)
        return html.strip()


def transform_html_entry(html_content: str) -> Tuple[str, Dict]:
    """
    Transform HTML with MAXIMUM compression enabled.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    style_tags = soup.find_all('style')

    if not style_tags:
        return html_content, {
            'processed': False,
            'reason': 'no_style_tags',
            'message': 'No <style> tags found'
        }

    # Collect all CSS
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

    merged_css = '\n\n'.join(all_css)

    # Extract classes and IDs
    classes, ids = extract_classes_and_ids(soup, merged_css)

    # Build minification mappings
    class_mapping = build_minification_mapping(classes)
    id_mapping = build_minification_mapping(ids)

    # Calculate original name lengths
    original_names_length = sum(len(c) for c in classes) + sum(len(i) for i in ids)

    # Minify CSS selectors
    minified_css = minify_css_selectors(merged_css, class_mapping, id_mapping)

    # Minify HTML attributes
    minify_html_attributes(soup, class_mapping, id_mapping)

    # Purge unused CSS
    css_before_purge = len(minified_css)
    minified_css = purge_unused_css(minified_css, soup, class_mapping, id_mapping)
    css_purged = css_before_purge - len(minified_css)

    # Minify CSS content (remove whitespace, comments, etc.)
    css_before_minify = len(minified_css)
    minified_css = minify_css_content(minified_css)
    css_minified = css_before_minify - len(minified_css)

    # Calculate minified name lengths
    minified_names_length = sum(len(v) for v in class_mapping.values()) + sum(len(v) for v in id_mapping.values())
    name_bytes_removed = original_names_length - minified_names_length

    # Remove all existing style tags
    for style_tag in style_tags:
        style_tag.decompose()

    # Inline CSS if it's very small, otherwise add as <style> tag
    if len(minified_css) <= INLINE_CSS_THRESHOLD and minified_css:
        # Inline the CSS
        soup = css_to_inline_styles(soup, minified_css)
        css_inlined = True
    else:
        # Add minified CSS as style tag
        body = soup.find('body')
        if not body:
            html_tag = soup.find('html')
            if not html_tag:
                html_tag = soup.new_tag('html')
                soup.append(html_tag)
            body = soup.new_tag('body')
            html_tag.append(body)

        new_style = soup.new_tag('style')
        new_style.string = minified_css
        body.append(new_style)
        css_inlined = False

    # Get HTML and minify structure
    output_html = str(soup)
    html_before_minify = len(output_html)
    output_html = minify_html_content(output_html)
    html_minified = html_before_minify - len(output_html)

    # Calculate total savings
    css_bytes_removed = original_css_size - len(minified_css)
    total_bytes_removed = css_bytes_removed + name_bytes_removed + html_minified

    stats = {
        'processed': True,
        'classes_minified': len(class_mapping),
        'ids_minified': len(id_mapping),
        'original_css_size': original_css_size,
        'minified_css_size': len(minified_css),
        'css_bytes_removed': css_bytes_removed,
        'css_purged_bytes': css_purged,
        'css_minified_bytes': css_minified,
        'name_bytes_removed': name_bytes_removed,
        'html_minified_bytes': html_minified,
        'css_inlined': css_inlined,
        'total_bytes_removed': total_bytes_removed
    }

    return output_html, stats


def get_gpt_response_from_entry(entry: dict) -> str:
    """Extract the GPT response from a conversation entry"""
    conversations = entry.get('conversations', [])
    for conv in conversations:
        if conv.get('from') == 'gpt':
            return conv.get('value', '')
    return ''


def transform_json_maximum_compression(input_path: str, output_path: str) -> Dict:
    """
    Transform JSON entries with MAXIMUM compression.
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
    total_css_purged = 0
    total_css_minified = 0
    total_html_minified = 0
    total_inlined = 0
    entries_processed = 0
    entries_skipped = 0

    # Skip tracking
    skip_reasons = {}
    skipped_entries = []

    print(f"Processing {total_entries} entries with MAXIMUM compression...")

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

            if stats.get('processed'):
                new_entry = entry.copy()
                for conv in new_entry.get('conversations', []):
                    if conv.get('from') == 'gpt':
                        conv['value'] = transformed_html

                transformed_data.append(new_entry)

                entries_processed += 1
                total_classes_minified += stats.get('classes_minified', 0)
                total_ids_minified += stats.get('ids_minified', 0)
                total_bytes_removed += stats.get('total_bytes_removed', 0)
                total_css_purged += stats.get('css_purged_bytes', 0)
                total_css_minified += stats.get('css_minified_bytes', 0)
                total_html_minified += stats.get('html_minified_bytes', 0)
                if stats.get('css_inlined'):
                    total_inlined += 1
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

    # Write output JSON (COMPACT - no indentation)
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
        'total_classes_minified': total_classes_minified,
        'total_ids_minified': total_ids_minified,
        'total_bytes_removed': total_bytes_removed,
        'total_css_purged': total_css_purged,
        'total_css_minified': total_css_minified,
        'total_html_minified': total_html_minified,
        'total_inlined': total_inlined,
        'input_path': input_path,
        'output_path': output_path,
        'skip_report_path': skip_report_path if entries_skipped > 0 else None
    }


def main():
    parser = argparse.ArgumentParser(
        description='Transform JSON with MAXIMUM compression (all features enabled)'
    )
    parser.add_argument('--input', type=str, required=True, help='Input JSON file path')
    parser.add_argument('--output', type=str, required=True, help='Output JSON file path')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    try:
        stats = transform_json_maximum_compression(args.input, args.output)

        # Print summary
        print("\n" + "=" * 60)
        print("MAXIMUM COMPRESSION SUMMARY")
        print("=" * 60)
        print(f"Total entries:          {stats['total_entries']}")
        print(f"Entries processed:      {stats['entries_processed']}")
        print(f"Entries skipped:        {stats['entries_skipped']}")

        if stats['entries_skipped'] > 0 and stats.get('skip_reasons'):
            print("\nSkip Reason Breakdown:")
            for reason, count in sorted(stats['skip_reasons'].items(), key=lambda x: x[1], reverse=True):
                reason_display = reason.replace('_', ' ').title()
                print(f"  - {reason_display}: {count}")

        print(f"\nClasses minified:       {stats['total_classes_minified']}")
        print(f"IDs minified:           {stats['total_ids_minified']}")
        print(f"CSS inlined:            {stats['total_inlined']} entries")
        print(f"\nBytes removed breakdown:")
        print(f"  - CSS purged:         {stats['total_css_purged']:,}")
        print(f"  - CSS minified:       {stats['total_css_minified']:,}")
        print(f"  - HTML minified:      {stats['total_html_minified']:,}")
        print(f"  - TOTAL REMOVED:      {stats['total_bytes_removed']:,}")

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
