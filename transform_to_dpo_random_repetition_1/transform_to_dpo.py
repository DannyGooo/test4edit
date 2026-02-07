#!/usr/bin/env python3
"""
Transform HTML/CSS dataset to DPO format with synthetic loopy rejected samples.

This script generates DPO (Direct Preference Optimization) training data where:
- chosen: Original ground truth HTML/CSS
- rejected: Synthetic "loopy" generation that simulates model getting stuck in repetition
"""

import argparse
import json
import random
import re
import uuid
from typing import Tuple, Optional, Dict, Any, List


def get_random_rep_count(remaining: int, unit_len: int) -> int:
    """
    Calculate random repetition count.
    - If unit < 5 chars: min 40 reps
    - If unit >= 5 chars: min 20 reps
    - Capped at max_reps that fit in remaining space
    """
    if unit_len <= 0:
        return 20

    # Determine minimum reps based on unit size
    min_reps = 40 if unit_len < 5 else 20

    # Calculate max reps that fit
    max_reps = max(1, remaining // unit_len)

    # Actual minimum is capped by what fits
    actual_min = min(min_reps, max_reps)

    return random.randint(actual_min, max_reps)


def is_in_css_region(content: str, pos: int) -> bool:
    """Check if position is within a <style> block."""
    # Find all style tag positions
    style_open_pattern = re.compile(r'<style[^>]*>', re.IGNORECASE)
    style_close_pattern = re.compile(r'</style>', re.IGNORECASE)

    style_regions = []
    for match in style_open_pattern.finditer(content):
        start = match.end()
        # Find corresponding closing tag
        close_match = style_close_pattern.search(content, start)
        if close_match:
            style_regions.append((start, close_match.start()))

    # Check if pos is within any style region
    for start, end in style_regions:
        if start <= pos < end:
            return True
    return False


def extract_nearest_tag(content: str, pos: int) -> Optional[str]:
    """
    Extract the nearest HTML tag at or after the given position.
    Returns tags like <div>, </div>, <br/>, <span class="foo">, etc.
    """
    # Look for tag starting at or after pos
    tag_pattern = re.compile(r'</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?\s*/?>')

    # Search forward from pos
    match = tag_pattern.search(content, pos)
    if match:
        return match.group()

    # If not found forward, search backward
    for i in range(pos, -1, -1):
        match = tag_pattern.match(content, i)
        if match:
            return match.group()

    return None


def extract_html_section(content: str, pos: int) -> Optional[str]:
    """
    Extract a complete HTML section (element with content) near the position.
    Returns something like <div><p>hello</p></div>
    """
    # Find opening tag at or near pos
    tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)[^>]*>')

    # Search backward for the nearest opening tag
    search_start = max(0, pos - 200)
    matches = list(tag_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    # Find the closest match to pos
    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    tag_name = closest_match.group(1)
    start = closest_match.start()

    # Find the closing tag
    # Handle self-closing tags
    if closest_match.group().endswith('/>'):
        return closest_match.group()

    # Find matching closing tag (accounting for nested tags)
    close_pattern = re.compile(rf'</{tag_name}>', re.IGNORECASE)
    open_pattern = re.compile(rf'<{tag_name}[^>]*>', re.IGNORECASE)

    depth = 1
    search_pos = closest_match.end()

    while depth > 0 and search_pos < len(content):
        close_match = close_pattern.search(content, search_pos)
        open_match = open_pattern.search(content, search_pos)

        if not close_match:
            # No closing tag found, return just the opening tag portion
            return content[start:min(start + 100, len(content))]

        if open_match and open_match.start() < close_match.start():
            depth += 1
            search_pos = open_match.end()
        else:
            depth -= 1
            if depth == 0:
                return content[start:close_match.end()]
            search_pos = close_match.end()

    # Return partial section if we couldn't find complete closing
    return content[start:min(start + 100, len(content))]


def extract_css_rule(content: str, pos: int) -> Optional[str]:
    """
    Extract a complete CSS rule block near the position.
    Returns something like .selector { property: value; }
    """
    # Find the CSS rule containing or near pos
    # CSS rule pattern: selector { properties }
    css_rule_pattern = re.compile(r'[^{}]+\{[^{}]*\}', re.DOTALL)

    # Search backward for start of CSS block
    search_start = max(0, pos - 500)
    matches = list(css_rule_pattern.finditer(content, search_start, min(pos + 500, len(content))))

    if not matches:
        return None

    # Find the closest match to pos
    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    return closest_match.group().strip()


def extract_class_from_tag(content: str, pos: int) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the tag and class name from the nearest tag with a single class.
    Returns (tag_template, class_name) or (None, None)
    """
    # Pattern to match tags with class attribute
    tag_with_class = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)\s+class="([^"]+)"[^>]*>')

    # Search around pos
    search_start = max(0, pos - 200)
    matches = list(tag_with_class.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None, None

    # Find closest match
    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    tag_name = closest_match.group(1)
    class_name = closest_match.group(2)

    # Get the full tag to use as template
    full_tag = closest_match.group()

    return full_tag, class_name


def generate_char_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating 1-2 characters.
    """
    prefix = content[:start_pos]

    # Get 1 or 2 characters at start position
    num_chars = random.choice([1, 2])
    if start_pos < len(content):
        loop_chars = content[start_pos:min(start_pos + num_chars, len(content))]
    else:
        loop_chars = 'a'  # fallback

    # Calculate how much we need to fill
    remaining = target_length - len(prefix)

    if remaining <= 0:
        return prefix[:target_length]

    # Repeat characters with random count
    if len(loop_chars) > 0:
        repeat_count = get_random_rep_count(remaining, len(loop_chars))
        suffix = loop_chars * repeat_count
    else:
        suffix = ''

    return prefix + suffix


def generate_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating an HTML tag.
    """
    prefix = content[:start_pos]

    # Extract nearest tag
    tag = extract_nearest_tag(content, start_pos)
    if not tag:
        # Fallback to character loop
        return generate_char_loop(content, start_pos, target_length)

    # Calculate how much we need to fill
    remaining = target_length - len(prefix)

    if remaining <= 0:
        return prefix[:target_length]

    # Repeat tag with random count
    repeat_count = get_random_rep_count(remaining, len(tag))
    suffix = tag * repeat_count

    return prefix + suffix


def generate_section_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating an HTML section.
    """
    prefix = content[:start_pos]

    # Extract HTML section
    section = extract_html_section(content, start_pos)
    if not section:
        # Fallback to tag loop
        return generate_tag_loop(content, start_pos, target_length)

    # Calculate how much we need to fill
    remaining = target_length - len(prefix)

    if remaining <= 0:
        return prefix[:target_length]

    # Repeat section with random count
    repeat_count = get_random_rep_count(remaining, len(section))
    suffix = section * repeat_count

    return prefix + suffix


def generate_incrementing_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content with incrementing class numbers.
    Example: <div class="header-1"><div class="header-2">...
    """
    prefix = content[:start_pos]

    # Extract tag with class
    tag_template, class_name = extract_class_from_tag(content, start_pos)

    if not tag_template or not class_name:
        # Fallback to tag loop
        return generate_tag_loop(content, start_pos, target_length)

    # Extract tag name from template
    tag_match = re.match(r'<([a-zA-Z][a-zA-Z0-9]*)', tag_template)
    if not tag_match:
        return generate_tag_loop(content, start_pos, target_length)

    tag_name = tag_match.group(1)

    # Generate incrementing tags
    remaining = target_length - len(prefix)

    if remaining <= 0:
        return prefix[:target_length]

    # Estimate tag length to calculate random rep count
    sample_tag = f'<{tag_name} class="{class_name}-1">'
    repeat_count = get_random_rep_count(remaining, len(sample_tag))

    suffix = ""
    for counter in range(1, repeat_count + 1):
        new_tag = f'<{tag_name} class="{class_name}-{counter}">'
        suffix += new_tag

    return prefix + suffix


def generate_css_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating a CSS rule.
    """
    prefix = content[:start_pos]

    # Extract CSS rule
    css_rule = extract_css_rule(content, start_pos)
    if not css_rule:
        # Fallback to character loop
        return generate_char_loop(content, start_pos, target_length)

    # Calculate how much we need to fill
    remaining = target_length - len(prefix)

    if remaining <= 0:
        return prefix[:target_length]

    # Repeat CSS rule with random count (with newlines for readability)
    css_with_newline = css_rule + '\n'
    repeat_count = get_random_rep_count(remaining, len(css_with_newline))
    suffix = css_with_newline * repeat_count

    return prefix + suffix


# ============== NEW HTML LOOP TYPES ==============

def generate_closing_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating closing tags.
    Example: </div></div></div></div>...
    """
    prefix = content[:start_pos]

    # Find nearest closing tag
    closing_tag_pattern = re.compile(r'</[a-zA-Z][a-zA-Z0-9]*>')
    match = closing_tag_pattern.search(content, max(0, start_pos - 50))

    if match:
        closing_tag = match.group()
    else:
        # Fallback to common closing tag
        closing_tag = '</div>'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    repeat_count = get_random_rep_count(remaining, len(closing_tag))
    suffix = closing_tag * repeat_count

    return prefix + suffix


def generate_self_closing_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating self-closing tags.
    Example: <br/><br/><br/>... or <input/><input/>...
    """
    prefix = content[:start_pos]

    # Find nearest self-closing tag or use common ones
    self_closing_pattern = re.compile(r'<(br|hr|img|input|meta|link)[^>]*/?>',re.IGNORECASE)
    match = self_closing_pattern.search(content, max(0, start_pos - 100))

    if match:
        self_closing_tag = match.group()
    else:
        # Choose random common self-closing tag
        self_closing_tag = random.choice(['<br/>', '<hr/>', '<input/>', '<img src=""/>'])

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    repeat_count = get_random_rep_count(remaining, len(self_closing_tag))
    suffix = self_closing_tag * repeat_count

    return prefix + suffix


def generate_deeply_nested_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content with deep nesting (opening tags only).
    Example: <div><div><div><div><div>...
    """
    prefix = content[:start_pos]

    # Find nearest opening tag
    opening_tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)(?:\s+[^>]*)?>(?!/)')
    match = opening_tag_pattern.search(content, max(0, start_pos - 50))

    if match:
        tag_name = match.group(1)
        # Create simple opening tag
        opening_tag = f'<{tag_name}>'
    else:
        opening_tag = '<div>'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    repeat_count = get_random_rep_count(remaining, len(opening_tag))
    suffix = opening_tag * repeat_count

    return prefix + suffix


# ============== STUTTERING LOOPS ==============

def generate_stuttering_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate content with stuttering pattern.
    Simulates a model "stuttering" at the start of a pattern before repeating.

    Examples:
    - Tag stutter: `<di <div><div><div><div>`
    - Closing tag stutter: `</d </di </div></div></div>`
    - CSS property stutter: `colo color: red; color: red; color: red;`
    - Character stutter: `di div div div div`
    """
    prefix = content[:start_pos]

    # Determine what kind of unit to stutter based on context
    unit = None

    # Try to find a tag first
    tag = extract_nearest_tag(content, start_pos)
    if tag:
        unit = tag
    else:
        # Try to find a word or identifier
        word_pattern = re.compile(r'[a-zA-Z][a-zA-Z0-9_-]*')
        match = word_pattern.search(content, start_pos)
        if match:
            unit = match.group()

    if not unit or len(unit) < 2:
        # Fallback to character loop
        return generate_char_loop(content, start_pos, target_length)

    # Generate stutter prefix (first 1-4 characters)
    stutter_len = random.randint(1, min(4, len(unit) - 1))
    stutter_prefix = unit[:stutter_len]

    # Calculate remaining space
    remaining = target_length - len(prefix) - len(stutter_prefix) - 1  # -1 for space after stutter

    if remaining <= 0:
        return prefix[:target_length]

    # Repeat the full unit
    unit_with_space = unit
    # Add space between units for readability (except for tags)
    if not unit.startswith('<'):
        unit_with_space = unit + ' '

    repeat_count = get_random_rep_count(remaining, len(unit_with_space))
    suffix = stutter_prefix + ' ' + unit_with_space * repeat_count

    return prefix + suffix


# ============== ATTRIBUTE LOOPS ==============

def extract_attribute(content: str, pos: int) -> Optional[Tuple[str, str, int, int]]:
    """
    Extract an attribute (key="value") from nearest tag.
    Returns (full_tag, attribute_string, tag_start, tag_end) or None
    """
    # Find tags with attributes
    tag_with_attr_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)\s+([^>]+)>')

    # Search around pos
    search_start = max(0, pos - 200)
    matches = list(tag_with_attr_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    # Find closest match
    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    full_tag = closest_match.group()
    attrs_str = closest_match.group(2)

    # Extract individual attributes (key="value" or key='value' or just key)
    attr_pattern = re.compile(r'([a-zA-Z][a-zA-Z0-9_-]*)\s*=\s*["\']([^"\']*)["\']')
    attr_matches = list(attr_pattern.finditer(attrs_str))

    if not attr_matches:
        return None

    # Pick the first attribute found
    attr_match = attr_matches[0]
    attribute_string = attr_match.group()

    return (full_tag, attribute_string, closest_match.start(), closest_match.end())


def generate_attribute_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate content with repeated attributes within tags.
    Example: <div class="header" class="header" class="header">
    """
    prefix = content[:start_pos]

    result = extract_attribute(content, start_pos)

    if not result:
        # Fallback to tag loop
        return generate_tag_loop(content, start_pos, target_length)

    full_tag, attribute_string, tag_start, tag_end = result

    # Parse the tag to insert repeated attributes
    tag_match = re.match(r'<([a-zA-Z][a-zA-Z0-9]*)\s+', full_tag)
    if not tag_match:
        return generate_tag_loop(content, start_pos, target_length)

    tag_name = tag_match.group(1)

    # Calculate how many attribute repetitions we need
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    # Create tag with repeated attributes
    attr_with_space = attribute_string + ' '
    repeat_count = get_random_rep_count(remaining, len(attr_with_space))

    # Build the malformed tag with repeated attributes
    repeated_attrs = (attr_with_space * repeat_count).strip()
    malformed_tag = f'<{tag_name} {repeated_attrs}>'

    # Continue with more repeated tags for longer output
    suffix = malformed_tag
    remaining_after_first = remaining - len(malformed_tag)

    if remaining_after_first > 0:
        # Add more repeated malformed tags
        additional_count = max(1, remaining_after_first // len(malformed_tag))
        suffix = malformed_tag * (additional_count + 1)

    return prefix + suffix


# ============== TEXT CONTENT LOOPS ==============

def extract_text_content(content: str, pos: int) -> Optional[Tuple[str, str, str]]:
    """
    Extract text content between HTML tags near position.
    Returns (text, opening_tag, closing_tag) or None
    """
    # Pattern to find text between tags: >text<
    text_pattern = re.compile(r'>([^<]+)<')

    # Search around pos
    search_start = max(0, pos - 200)
    matches = list(text_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    # Find closest match with non-whitespace text
    valid_matches = [(m, m.group(1).strip()) for m in matches if m.group(1).strip()]

    if not valid_matches:
        return None

    closest_match, text = min(valid_matches, key=lambda x: abs(x[0].start() - pos))

    # Try to find the surrounding tags
    text_start = closest_match.start()
    text_end = closest_match.end()

    # Find opening tag before text
    opening_tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)(?:\s+[^>]*)?>')
    opening_matches = list(opening_tag_pattern.finditer(content, max(0, text_start - 100), text_start + 1))

    opening_tag = ""
    closing_tag = ""

    if opening_matches:
        last_opening = opening_matches[-1]
        tag_name = last_opening.group(1)
        opening_tag = last_opening.group()
        closing_tag = f'</{tag_name}>'

    return (text, opening_tag, closing_tag)


def generate_text_content_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate content by repeating text found between tags.

    Examples:
    - `Hello World Hello World Hello World`
    - `Click here Click here Click here`
    - `<span>Submit</span><span>Submit</span><span>Submit</span>` (text with wrapper)
    """
    prefix = content[:start_pos]

    result = extract_text_content(content, start_pos)

    if not result:
        # Fallback to section loop
        return generate_section_loop(content, start_pos, target_length)

    text, opening_tag, closing_tag = result

    # Skip very short text (likely just whitespace or punctuation)
    if len(text) < 2:
        return generate_section_loop(content, start_pos, target_length)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    # Randomly choose between text-only or text-with-tags repetition
    use_tags = random.choice([True, False]) and opening_tag and closing_tag

    if use_tags:
        # Repeat text with surrounding tags
        unit = opening_tag + text + closing_tag
    else:
        # Repeat just the text with spaces
        unit = text + ' '

    repeat_count = get_random_rep_count(remaining, len(unit))
    suffix = unit * repeat_count

    return prefix + suffix


# ============== NEW CSS LOOP TYPES ==============

def extract_css_property(content: str, pos: int) -> Optional[str]:
    """
    Extract a single CSS property near the position.
    Returns something like "color: red;" or "margin: 10px;"
    """
    # CSS property pattern: property-name: value;
    property_pattern = re.compile(r'[\w-]+\s*:\s*[^;{}]+;?')

    search_start = max(0, pos - 200)
    matches = list(property_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    prop = closest_match.group().strip()
    if not prop.endswith(';'):
        prop += ';'
    return prop


def extract_css_selector(content: str, pos: int) -> Optional[str]:
    """
    Extract a CSS selector near the position.
    Returns something like ".class" or "#id" or "div.class"
    """
    # Find selector before the nearest {
    selector_pattern = re.compile(r'([.#]?[\w-]+(?:\s+[.#]?[\w-]+)*)\s*\{')

    search_start = max(0, pos - 300)
    matches = list(selector_pattern.finditer(content, search_start, min(pos + 300, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    return closest_match.group(1).strip()


def extract_css_rules_group(content: str, pos: int, num_rules: int = 2) -> Optional[str]:
    """
    Extract multiple consecutive CSS rules as a group.
    Returns 2-3 CSS rules combined, e.g.:
    ".class1 { color: red; }
     .class2 { color: blue; }"
    """
    css_rule_pattern = re.compile(r'[^{}]+\{[^{}]*\}', re.DOTALL)

    # Find all rules in the content
    all_matches = list(css_rule_pattern.finditer(content))

    if len(all_matches) < num_rules:
        return None

    # Find the rule closest to pos
    closest_idx = 0
    min_dist = float('inf')
    for i, match in enumerate(all_matches):
        dist = abs(match.start() - pos)
        if dist < min_dist:
            min_dist = dist
            closest_idx = i

    # Get num_rules consecutive rules starting from closest
    # Make sure we don't go past the end
    start_idx = min(closest_idx, len(all_matches) - num_rules)
    start_idx = max(0, start_idx)

    rules = []
    for i in range(start_idx, min(start_idx + num_rules, len(all_matches))):
        rules.append(all_matches[i].group().strip())

    if len(rules) < 2:
        return None

    return '\n'.join(rules)


def generate_css_multi_rule_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating multiple CSS rules together.
    Example:
        .class1 { color: red; }
        .class2 { margin: 10px; }
        .class1 { color: red; }
        .class2 { margin: 10px; }
    """
    prefix = content[:start_pos]

    # Randomly choose 2 or 3 rules to repeat together
    num_rules = random.choice([2, 3])
    rules_group = extract_css_rules_group(content, start_pos, num_rules)

    if not rules_group:
        # Fallback to single CSS rule loop
        return generate_css_loop(content, start_pos, target_length)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    rules_with_newline = rules_group + '\n'
    repeat_count = get_random_rep_count(remaining, len(rules_with_newline))
    suffix = rules_with_newline * repeat_count

    return prefix + suffix


def generate_css_property_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating a CSS property.
    Example: color: red; color: red; color: red;
    """
    prefix = content[:start_pos]

    css_property = extract_css_property(content, start_pos)
    if not css_property:
        css_property = 'color: red;'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    prop_with_space = css_property + ' '
    repeat_count = get_random_rep_count(remaining, len(prop_with_space))
    suffix = prop_with_space * repeat_count

    return prefix + suffix


def generate_css_selector_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating CSS selectors.
    Example: .class .class .class { }
    """
    prefix = content[:start_pos]

    selector = extract_css_selector(content, start_pos)
    if not selector:
        selector = '.class'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    selector_with_space = selector + ' '
    repeat_count = get_random_rep_count(remaining, len(selector_with_space))
    suffix = selector_with_space * repeat_count

    return prefix + suffix


def generate_css_incrementing_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content with incrementing CSS selectors.
    Example: .class-1 { } .class-2 { } .class-3 { }
    """
    prefix = content[:start_pos]

    selector = extract_css_selector(content, start_pos)
    if not selector:
        selector = '.item'

    # Remove any existing numbers from selector
    base_selector = re.sub(r'-?\d+$', '', selector)

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    # Estimate rule length to calculate random rep count
    sample_rule = f'{base_selector}-1 {{ }}\n'
    repeat_count = get_random_rep_count(remaining, len(sample_rule))

    suffix = ""
    for counter in range(1, repeat_count + 1):
        rule = f'{base_selector}-{counter} {{ }}\n'
        suffix += rule

    return prefix + suffix


def generate_css_value_loop(content: str, start_pos: int, target_length: int) -> str:
    """
    Generate loopy content by repeating CSS values.
    Example: padding: 10px 10px 10px 10px 10px...
    """
    prefix = content[:start_pos]

    # Find a CSS value (number with unit or keyword)
    value_pattern = re.compile(r':\s*([\d.]+(?:px|em|rem|%|vh|vw)?|\w+)')
    match = value_pattern.search(content, max(0, start_pos - 100))

    if match:
        value = match.group(1)
    else:
        value = '10px'

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]

    value_with_space = value + ' '
    repeat_count = get_random_rep_count(remaining, len(value_with_space))
    suffix = value_with_space * repeat_count

    return prefix + suffix


def generate_loopy_content(content: str) -> Tuple[str, str]:
    """
    Generate loopy rejected content from the given ground truth.

    Returns:
        Tuple of (loopy_content, loop_type_used)
    """
    # Generate random start percentage (1% to 95%)
    start_pct = random.uniform(0.01, 0.95)
    start_pos = int(len(content) * start_pct)
    target_length = len(content)

    # Check if we're in CSS region
    if is_in_css_region(content, start_pos):
        # CSS region - 6 loop types
        css_choices = ["css_rule", "css_property", "css_selector", "css_incrementing", "css_value", "css_multi_rule"]
        loop_type = random.choice(css_choices)

        if loop_type == "css_rule":
            result = generate_css_loop(content, start_pos, target_length)
        elif loop_type == "css_property":
            result = generate_css_property_loop(content, start_pos, target_length)
        elif loop_type == "css_selector":
            result = generate_css_selector_loop(content, start_pos, target_length)
        elif loop_type == "css_incrementing":
            result = generate_css_incrementing_loop(content, start_pos, target_length)
        elif loop_type == "css_value":
            result = generate_css_value_loop(content, start_pos, target_length)
        else:  # css_multi_rule
            result = generate_css_multi_rule_loop(content, start_pos, target_length)
    else:
        # HTML region - 10 loop types (7 original + 3 new)
        html_choices = [
            "char", "tag", "section", "incrementing",
            "closing_tag", "self_closing", "deeply_nested",
            "stuttering",      # NEW
            "attribute",       # NEW
            "text_content"     # NEW
        ]
        loop_type = random.choice(html_choices)

        if loop_type == "char":
            result = generate_char_loop(content, start_pos, target_length)
        elif loop_type == "tag":
            result = generate_tag_loop(content, start_pos, target_length)
        elif loop_type == "section":
            result = generate_section_loop(content, start_pos, target_length)
        elif loop_type == "incrementing":
            result = generate_incrementing_tag_loop(content, start_pos, target_length)
        elif loop_type == "closing_tag":
            result = generate_closing_tag_loop(content, start_pos, target_length)
        elif loop_type == "self_closing":
            result = generate_self_closing_loop(content, start_pos, target_length)
        elif loop_type == "deeply_nested":
            result = generate_deeply_nested_loop(content, start_pos, target_length)
        elif loop_type == "stuttering":
            result = generate_stuttering_loop(content, start_pos, target_length)
        elif loop_type == "attribute":
            result = generate_attribute_loop(content, start_pos, target_length)
        else:  # text_content
            result = generate_text_content_loop(content, start_pos, target_length)

    return result, loop_type


def process_entry(entry: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Process a single entry from the source dataset.

    Supports two input formats:
    1. DPO format: {id, image, prompt, chosen, rejected}
    2. Conversation format: {conversations: [{from: "human/gpt", value: ...}]}

    Returns:
        Tuple of (dpo_entry, skip_reason, loop_type)
    """
    human_msg = None
    gpt_msg = None

    # Check if input is already in DPO format
    if "prompt" in entry and "chosen" in entry:
        human_msg = entry.get("prompt", "")
        gpt_msg = entry.get("chosen", "")
    else:
        # Extract from conversations format
        conversations = entry.get("conversations", [])
        for conv in conversations:
            if conv.get("from") == "human":
                human_msg = conv.get("value", "")
            elif conv.get("from") == "gpt":
                gpt_msg = conv.get("value", "")

    if not human_msg:
        return None, "no_human_message", None

    if not gpt_msg:
        return None, "no_gpt_response", None

    # Generate loopy rejected content
    try:
        rejected_content, loop_type = generate_loopy_content(gpt_msg)
    except Exception as e:
        return None, f"error: {str(e)}", None

    # Create DPO entry
    dpo_entry = {
        "id": str(uuid.uuid4()),
        "image": entry.get("image", ""),
        "prompt": human_msg,
        "chosen": gpt_msg,
        "rejected": rejected_content
    }

    return dpo_entry, None, loop_type


def main():
    parser = argparse.ArgumentParser(
        description="Transform HTML/CSS dataset to DPO format with loopy rejected samples"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Input JSON file path"
    )
    parser.add_argument(
        "--output", "-o",
        required=True,
        help="Output JSON file path"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of entries to process (default: all)"
    )
    parser.add_argument(
        "--seed", "-s",
        type=int,
        default=42,
        help="Random seed for reproducibility (default: 42)"
    )

    args = parser.parse_args()

    # Set random seed
    random.seed(args.seed)

    # Load input data
    print(f"Loading input file: {args.input}")
    with open(args.input, 'r', encoding='utf-8') as f:
        data = json.load(f)

    print(f"Total entries in input: {len(data)}")

    # Apply limit if specified
    if args.limit:
        data = data[:args.limit]
        print(f"Processing first {args.limit} entries")

    # Process entries
    dpo_data = []
    skip_reasons: Dict[str, List[str]] = {}
    loop_type_counts = {
        # HTML loop types
        "char": 0,
        "tag": 0,
        "section": 0,
        "incrementing": 0,
        "closing_tag": 0,
        "self_closing": 0,
        "deeply_nested": 0,
        "stuttering": 0,      # NEW
        "attribute": 0,       # NEW
        "text_content": 0,    # NEW
        # CSS loop types
        "css_rule": 0,
        "css_property": 0,
        "css_selector": 0,
        "css_incrementing": 0,
        "css_value": 0,
        "css_multi_rule": 0,
    }

    print("Processing entries...")
    for i, entry in enumerate(data):
        if (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{len(data)} entries...")

        dpo_entry, skip_reason, loop_type = process_entry(entry)

        if skip_reason:
            if skip_reason not in skip_reasons:
                skip_reasons[skip_reason] = []
            skip_reasons[skip_reason].append(entry.get("id", f"entry_{i}"))
        else:
            dpo_data.append(dpo_entry)
            if loop_type:
                loop_type_counts[loop_type] += 1

    # Save output
    print(f"\nSaving output to: {args.output}")
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(dpo_data, f, indent=2, ensure_ascii=False)

    # Save skip report if there are skipped entries
    if skip_reasons:
        skip_file = args.output + ".skipped.json"
        print(f"Saving skip report to: {skip_file}")
        with open(skip_file, 'w', encoding='utf-8') as f:
            json.dump(skip_reasons, f, indent=2, ensure_ascii=False)

    # Print statistics
    print("\n" + "=" * 50)
    print("TRANSFORMATION STATISTICS")
    print("=" * 50)
    print(f"Total entries processed: {len(data)}")
    print(f"Successfully transformed: {len(dpo_data)}")
    print(f"Skipped: {len(data) - len(dpo_data)}")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, entries in skip_reasons.items():
            print(f"  {reason}: {len(entries)}")

    print("\nLoop type distribution:")
    for loop_type, count in loop_type_counts.items():
        if count > 0:
            pct = (count / len(dpo_data) * 100) if dpo_data else 0
            print(f"  {loop_type}: {count} ({pct:.1f}%)")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
