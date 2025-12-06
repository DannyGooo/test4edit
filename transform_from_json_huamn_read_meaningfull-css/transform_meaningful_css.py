#!/usr/bin/env python3
"""
Transform HTML/CSS with minified class/ID names to use semantic, meaningful names.

This script analyzes CSS properties and HTML element context to generate semantically
appropriate class and ID names, then prettifies the output.
"""

import json
import re
import argparse
from pathlib import Path
from bs4 import BeautifulSoup
from collections import defaultdict


class SemanticNameGenerator:
    """Generates semantic CSS class and ID names based on CSS properties and HTML context."""

    # CSS property patterns for semantic inference
    LAYOUT_PATTERNS = {
        'flex-container': ['display: *flex', 'flex-direction'],
        'grid-container': ['display: *grid', 'grid-template'],
        'flex-row': ['flex-direction: *row'],
        'flex-column': ['flex-direction: *column'],
        'grid': ['display: *grid'],
    }

    SPACING_PATTERNS = {
        'spacing': ['padding', 'margin'],
        'padded': ['padding:'],
        'spaced': ['margin:'],
    }

    TYPOGRAPHY_PATTERNS = {
        'text': ['font-', 'text-', 'line-height'],
        'heading': ['font-weight: *bold', 'font-size:.*[2-9]'],
        'large-text': ['font-size:.*[2-9]'],
        'bold': ['font-weight: *bold'],
    }

    COLOR_PATTERNS = {
        'primary': ['background.*#[0-9a-fA-F]{3,6}', 'color.*#[0-9a-fA-F]{3,6}'],
        'blue': ['(background|color).*#0{2,4}[0-9a-fA-F]{2}'],
        'red': ['(background|color).*#[0-9a-fA-F]{2}0{2,4}'],
        'green': ['(background|color).*#0{2}[0-9a-fA-F]{2}0{2}'],
    }

    BUTTON_PATTERNS = {
        'button': ['cursor: *pointer', 'padding.*px'],
        'btn': ['border-radius', 'padding'],
    }

    # HTML element to semantic name mapping
    HTML_ELEMENT_MAPPING = {
        'nav': 'navbar',
        'header': 'header',
        'footer': 'footer',
        'article': 'article',
        'section': 'section',
        'aside': 'sidebar',
        'main': 'main-content',
        'button': 'btn',
        'a': 'link',
        'form': 'form',
        'input': 'input',
        'textarea': 'textarea',
        'select': 'select',
        'ul': 'list',
        'ol': 'list',
        'li': 'list-item',
        'div': 'container',
        'span': 'text',
        'p': 'paragraph',
        'h1': 'heading',
        'h2': 'heading',
        'h3': 'heading',
        'h4': 'heading',
        'h5': 'heading',
        'h6': 'heading',
        'img': 'image',
        'table': 'table',
        'tr': 'row',
        'td': 'cell',
        'th': 'header-cell',
    }

    def __init__(self):
        self.name_counter = defaultdict(int)
        self.used_names = set()

    def analyze_css_properties(self, css_rules):
        """Analyze CSS properties to infer semantic categories."""
        css_text = ' '.join(css_rules).lower()
        categories = []

        # Check layout patterns
        for name, patterns in self.LAYOUT_PATTERNS.items():
            if any(re.search(pattern, css_text) for pattern in patterns):
                categories.append(name)

        # Check typography patterns
        for name, patterns in self.TYPOGRAPHY_PATTERNS.items():
            if any(re.search(pattern, css_text) for pattern in patterns):
                categories.append(name)

        # Check button patterns
        for name, patterns in self.BUTTON_PATTERNS.items():
            if any(re.search(pattern, css_text) for pattern in patterns):
                categories.append(name)

        # Check color patterns
        for name, patterns in self.COLOR_PATTERNS.items():
            if any(re.search(pattern, css_text) for pattern in patterns):
                categories.append(name)

        return categories

    def analyze_html_context(self, soup, class_or_id, is_class=True):
        """Analyze HTML elements that use this class/ID to infer semantic meaning."""
        if is_class:
            elements = soup.find_all(class_=lambda x: x and class_or_id in x.split())
        else:
            elements = soup.find_all(id=class_or_id)

        element_types = [elem.name for elem in elements]

        # Count most common element type
        if element_types:
            most_common = max(set(element_types), key=element_types.count)
            return self.HTML_ELEMENT_MAPPING.get(most_common, most_common)

        return None

    def generate_semantic_name(self, original_name, css_rules, html_context, is_class=True):
        """Generate a semantic name based on CSS properties and HTML context."""
        css_categories = self.analyze_css_properties(css_rules)

        # Build semantic name parts
        name_parts = []

        # Prioritize HTML context
        if html_context:
            name_parts.append(html_context)

        # Add CSS-derived categories
        if css_categories:
            # Prefer specific categories over generic ones
            priority_order = ['navbar', 'header', 'footer', 'button', 'btn', 'heading',
                             'flex-container', 'grid-container', 'primary']

            for cat in priority_order:
                if cat in css_categories:
                    if cat not in name_parts:
                        name_parts.append(cat)
                    break
            else:
                # Use first category if no priority match
                if css_categories[0] not in name_parts:
                    name_parts.append(css_categories[0])

        # Fallback to generic names if no context found
        if not name_parts:
            name_parts = ['element' if is_class else 'item']

        # Create base name
        base_name = '-'.join(name_parts)

        # Ensure uniqueness
        if base_name in self.used_names:
            self.name_counter[base_name] += 1
            final_name = f"{base_name}-{self.name_counter[base_name]}"
        else:
            final_name = base_name

        self.used_names.add(final_name)
        return final_name


def extract_css_rules(css_content):
    """Extract CSS rules and their properties."""
    rules = {}

    # Match CSS rules: selector { properties }
    rule_pattern = r'([^{]+)\{([^}]+)\}'
    matches = re.finditer(rule_pattern, css_content, re.MULTILINE)

    for match in matches:
        selector = match.group(1).strip()
        properties = match.group(2).strip()

        # Extract class selectors
        class_matches = re.findall(r'\.([\w-]+)', selector)
        for class_name in class_matches:
            if class_name not in rules:
                rules[class_name] = []
            rules[class_name].append(properties)

        # Extract ID selectors
        id_matches = re.findall(r'#([\w-]+)', selector)
        for id_name in id_matches:
            if id_name not in rules:
                rules[id_name] = []
            rules[id_name].append(properties)

    return rules


def extract_all_classes_and_ids(soup, css_content):
    """Extract all unique class and ID names from HTML and CSS."""
    classes = set()
    ids = set()

    # From HTML
    for element in soup.find_all(True):
        if element.get('class'):
            classes.update(element.get('class'))
        if element.get('id'):
            ids.add(element.get('id'))

    # From CSS - only extract from selectors, not from property values
    # Match CSS rules: selector { properties }
    rule_pattern = r'([^{]+)\{([^}]+)\}'
    matches = re.finditer(rule_pattern, css_content, re.MULTILINE)

    for match in matches:
        selector = match.group(1).strip()

        # Extract class selectors only from selector part
        class_matches = re.findall(r'\.([\w-]+)', selector)
        classes.update(class_matches)

        # Extract ID selectors only from selector part
        # Use negative lookbehind to exclude hex colors (which have only hex digits)
        id_matches = re.findall(r'#([\w-]+)', selector)
        # Filter out hex color codes (3 or 6 hex digits)
        for id_match in id_matches:
            if not re.match(r'^[0-9a-fA-F]{3}$|^[0-9a-fA-F]{6}$', id_match):
                ids.add(id_match)

    return classes, ids


def build_semantic_mappings(soup, css_content, css_rules):
    """Build mappings from original names to semantic names."""
    generator = SemanticNameGenerator()

    classes, ids = extract_all_classes_and_ids(soup, css_content)

    class_mapping = {}
    id_mapping = {}

    # Generate semantic names for classes
    for class_name in classes:
        html_context = generator.analyze_html_context(soup, class_name, is_class=True)
        rules = css_rules.get(class_name, [])
        semantic_name = generator.generate_semantic_name(
            class_name, rules, html_context, is_class=True
        )
        class_mapping[class_name] = semantic_name

    # Generate semantic names for IDs
    for id_name in ids:
        html_context = generator.analyze_html_context(soup, id_name, is_class=False)
        rules = css_rules.get(id_name, [])
        semantic_name = generator.generate_semantic_name(
            id_name, rules, html_context, is_class=False
        )
        id_mapping[id_name] = semantic_name

    return class_mapping, id_mapping


def replace_css_selectors(css_content, class_mapping, id_mapping):
    """Replace class and ID names in CSS selectors."""
    result = css_content

    # Replace class selectors (sorted by length to avoid partial replacements)
    for original, semantic in sorted(class_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        # Match .classname with word boundary
        pattern = r'\.' + re.escape(original) + r'\b'
        replacement = '.' + semantic
        result = re.sub(pattern, replacement, result)

    # Replace ID selectors
    for original, semantic in sorted(id_mapping.items(), key=lambda x: len(x[0]), reverse=True):
        # Match #idname with word boundary
        pattern = r'#' + re.escape(original) + r'\b'
        replacement = '#' + semantic
        result = re.sub(pattern, replacement, result)

    return result


def replace_html_attributes(soup, class_mapping, id_mapping):
    """Replace class and ID names in HTML attributes."""
    for element in soup.find_all(True):
        # Replace class attributes
        if element.get('class'):
            new_classes = []
            for class_name in element.get('class'):
                new_classes.append(class_mapping.get(class_name, class_name))
            element['class'] = new_classes

        # Replace ID attributes
        if element.get('id'):
            id_name = element.get('id')
            element['id'] = id_mapping.get(id_name, id_name)

    return soup


def prettify_css(css_content):
    """Prettify CSS with proper indentation and formatting."""
    # Remove extra whitespace
    css = re.sub(r'\s+', ' ', css_content).strip()

    # Add newlines and indentation
    css = re.sub(r'\{', ' {\n  ', css)
    css = re.sub(r'\}', '\n}\n\n', css)
    css = re.sub(r';', ';\n  ', css)
    css = re.sub(r',', ',\n', css)

    # Clean up extra spaces
    css = re.sub(r'  \n', '\n', css)
    css = re.sub(r'\n\n+', '\n\n', css)

    return css.strip()


def transform_html_with_semantic_names(html_content):
    """Transform HTML by replacing class/ID names with semantic names and prettifying."""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')

        # Extract all CSS content
        style_tags = soup.find_all('style')
        all_css = '\n'.join([tag.string or '' for tag in style_tags])

        if not all_css.strip():
            # No CSS, just prettify HTML
            return soup.prettify(), {'transformed': False, 'reason': 'no_css'}

        # Extract CSS rules
        css_rules = extract_css_rules(all_css)

        # Build semantic mappings
        class_mapping, id_mapping = build_semantic_mappings(soup, all_css, css_rules)

        # Replace CSS selectors in style tags
        for style_tag in style_tags:
            if style_tag.string:
                css_content = style_tag.string
                transformed_css = replace_css_selectors(css_content, class_mapping, id_mapping)
                prettified_css = prettify_css(transformed_css)
                style_tag.string = '\n' + prettified_css + '\n'

        # Replace HTML attributes
        soup = replace_html_attributes(soup, class_mapping, id_mapping)

        # Prettify final HTML
        result = soup.prettify()

        return result, {
            'transformed': True,
            'classes_renamed': len(class_mapping),
            'ids_renamed': len(id_mapping),
            'class_mapping': class_mapping,
            'id_mapping': id_mapping
        }

    except Exception as e:
        return None, {'transformed': False, 'reason': 'error', 'error': str(e)}


def get_gpt_response_from_entry(entry):
    """Extract GPT response from conversation entry."""
    conversations = entry.get('conversations', [])
    for conv in conversations:
        if conv.get('from') == 'gpt':
            return conv.get('value', '')
    return None


def transform_json_file(input_path, output_path):
    """Transform a JSON file containing HTML/CSS conversations."""
    print(f"Reading input file: {input_path}")

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    total_entries = len(data)
    print(f"Total entries to process: {total_entries}")

    transformed_data = []
    skipped_entries = []
    skip_reasons = defaultdict(int)

    for i, entry in enumerate(data):
        entry_id = entry.get('id', f'entry_{i}')
        print(f"Processing entry {i+1}/{total_entries} (ID: {entry_id})...")

        # Get GPT response
        gpt_response = get_gpt_response_from_entry(entry)

        if not gpt_response:
            print(f"  ⚠ Skipping: No GPT response found")
            skip_reasons['no_gpt_response'] += 1
            skipped_entries.append({
                'id': entry_id,
                'reason': 'no_gpt_response',
                'message': 'No GPT response found in conversations'
            })
            continue

        if '<html' not in gpt_response.lower() and '<!doctype' not in gpt_response.lower():
            print(f"  ⚠ Skipping: No HTML content")
            skip_reasons['no_html_content'] += 1
            skipped_entries.append({
                'id': entry_id,
                'reason': 'no_html_content',
                'message': 'GPT response does not contain HTML content'
            })
            continue

        # Transform HTML
        transformed_html, stats = transform_html_with_semantic_names(gpt_response)

        if not stats.get('transformed'):
            reason = stats.get('reason', 'unknown')
            print(f"  ⚠ Skipping: {reason}")
            skip_reasons[reason] += 1
            skipped_entries.append({
                'id': entry_id,
                'reason': reason,
                'message': stats.get('error', f'Failed to transform: {reason}')
            })
            continue

        # Update entry with transformed content
        new_entry = entry.copy()
        for conv in new_entry.get('conversations', []):
            if conv.get('from') == 'gpt':
                conv['value'] = transformed_html

        transformed_data.append(new_entry)

        print(f"  ✓ Transformed: {stats['classes_renamed']} classes, {stats['ids_renamed']} IDs")

    # Write output
    print(f"\nWriting output to: {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(transformed_data, f, ensure_ascii=False, indent=2)

    # Write skip report
    if skipped_entries:
        skip_report_path = str(output_path) + '.skipped.json'
        skip_report = {
            'total_skipped': len(skipped_entries),
            'skip_reasons': dict(skip_reasons),
            'skipped_entries': skipped_entries
        }
        print(f"Writing skip report to: {skip_report_path}")
        with open(skip_report_path, 'w', encoding='utf-8') as f:
            json.dump(skip_report, f, ensure_ascii=False, indent=2)

    # Summary
    print("\n" + "="*50)
    print("TRANSFORMATION SUMMARY")
    print("="*50)
    print(f"Total entries processed: {total_entries}")
    print(f"Successfully transformed: {len(transformed_data)}")
    print(f"Skipped entries: {len(skipped_entries)}")

    if skip_reasons:
        print("\nSkip reasons breakdown:")
        for reason, count in skip_reasons.items():
            percentage = (count / total_entries) * 100
            print(f"  - {reason}: {count} ({percentage:.1f}%)")
    else:
        print("\nAll entries were successfully transformed!")

    print("\nOutput statistics:")
    print(f"  - Main output: {output_path}")
    if skipped_entries:
        print(f"  - Skip report: {str(output_path)}.skipped.json")
    print("="*50)


def main():
    parser = argparse.ArgumentParser(
        description='Transform HTML/CSS with semantic, meaningful class and ID names'
    )
    parser.add_argument('--input', required=True, help='Input JSON file path')
    parser.add_argument('--output', required=True, help='Output JSON file path')

    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    transform_json_file(input_path, output_path)
    return 0


if __name__ == '__main__':
    exit(main())
