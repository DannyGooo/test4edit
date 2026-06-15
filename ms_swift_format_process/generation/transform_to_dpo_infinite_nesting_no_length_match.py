#!/usr/bin/env python3
"""
Transform ms_swift JSONL dataset to DPO format with rejected samples covering BOTH
major failure modes observed in real Qwen3-VL inference output:

A) COMPLETION-FAILURE patterns (5 sub-types) — model never reaches </html>
   Simulates the dominant real-world failure mode observed in 39.1% of failed
   predictions: model emits opening tags without ever closing them, eventually
   truncated by max_token.

  1. cf_cycling          — rotates 3-5 unique class names (row_002)
  2. cf_incrementing     — same base name + incrementing counter (row_089)
  3. cf_pure             — single tag repeated identically (row_116)
  4. cf_css_rule_cycling — same selector cycling 4-5 different rule bodies (row_047)
  5. cf_enumeration      — list/select/table children with sequential values (row_127)

B) INLINE-REPETITION patterns (13 sub-types) — model loops mid-document but still closes </html>
   Covers the legacy DPO concern: model gets stuck repeating something inline
   but eventually completes the document.

  HTML: inline_char, inline_tag, inline_section, inline_incrementing,
        inline_closing_tag, inline_self_closing, inline_deeply_nested
  CSS:  inline_css_rule, inline_css_property, inline_css_selector,
        inline_css_incrementing, inline_css_value, inline_css_multi_rule

Length is NOT matched: rejected length is drawn from a random fraction of
len(chosen), mirroring the variance profile of transform_to_dpo.py. Mode A vs
Mode B is still chosen with equal weight per entry.

Input format (JSONL, one JSON per line):
  {
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ],
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Output format (DPO JSONL, one JSON per line):
  {
    "query": "<image>\\nUser prompt...",
    "response": "<!DOCTYPE html>...",
    "rejected_response": "<!DOCTYPE html>... [infinite nesting failure]",
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Usage:
  python3 transform_to_dpo_infinite_nesting_no_length_match.py --input data.jsonl --output data_dpo.jsonl [--limit N] [--seed S]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List


# =============================================================================
# Infinite nesting rejection generator
# =============================================================================

# =============================================================================
# Shared utilities (used by both completion-failure and inline-repetition modes)
# =============================================================================

def get_leading_indent(content: str, pos: int) -> str:
    """
    Extract the leading whitespace (indentation) of the line containing `pos`.
    Returns the whitespace prefix including the preceding newline so that
    repeated units stay on their own indented lines.
    """
    line_start = content.rfind('\n', 0, pos)
    if line_start == -1:
        indent = content[:pos]
        if indent and indent.strip() == '':
            return indent
        return ''
    after_newline = line_start + 1
    indent = ''
    for ch in content[after_newline:pos]:
        if ch in (' ', '\t'):
            indent += ch
        else:
            break
    if indent:
        return '\n' + indent
    return '\n'


def fill_to_length(unit: str, remaining: int) -> str:
    """Repeat `unit` and trim to exactly `remaining` characters."""
    if not unit or remaining <= 0:
        return ''
    reps = (remaining // len(unit)) + 1
    return (unit * reps)[:remaining]


def is_in_css_region(content: str, pos: int) -> bool:
    """Check if position is within a <style> block."""
    style_open_pattern = re.compile(r'<style[^>]*>', re.IGNORECASE)
    style_close_pattern = re.compile(r'</style>', re.IGNORECASE)

    style_regions = []
    for match in style_open_pattern.finditer(content):
        start = match.end()
        close_match = style_close_pattern.search(content, start)
        if close_match:
            style_regions.append((start, close_match.start()))

    for start, end in style_regions:
        if start <= pos < end:
            return True
    return False


def extract_nearest_tag(content: str, pos: int) -> Optional[str]:
    """Extract the nearest HTML tag at or after the given position."""
    tag_pattern = re.compile(r'</?[a-zA-Z][a-zA-Z0-9]*(?:\s+[^>]*)?\s*/?>')

    match = tag_pattern.search(content, pos)
    if match:
        return match.group()

    for i in range(pos, -1, -1):
        match = tag_pattern.match(content, i)
        if match:
            return match.group()

    return None


def extract_html_section(content: str, pos: int) -> Optional[str]:
    """Extract a complete HTML section (element with content) near the position."""
    tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)[^>]*>')

    search_start = max(0, pos - 200)
    matches = list(tag_pattern.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    tag_name = closest_match.group(1)
    start = closest_match.start()

    if closest_match.group().endswith('/>'):
        return closest_match.group()

    close_pattern = re.compile(rf'</{tag_name}>', re.IGNORECASE)
    open_pattern = re.compile(rf'<{tag_name}[^>]*>', re.IGNORECASE)

    depth = 1
    search_pos = closest_match.end()

    while depth > 0 and search_pos < len(content):
        close_match = close_pattern.search(content, search_pos)
        open_match = open_pattern.search(content, search_pos)

        if not close_match:
            return content[start:min(start + 100, len(content))]

        if open_match and open_match.start() < close_match.start():
            depth += 1
            search_pos = open_match.end()
        else:
            depth -= 1
            if depth == 0:
                return content[start:close_match.end()]
            search_pos = close_match.end()

    return content[start:min(start + 100, len(content))]


def extract_css_rule(content: str, pos: int) -> Optional[str]:
    """Extract a complete CSS rule block near the position."""
    css_rule_pattern = re.compile(r'[^{}]+\{[^{}]*\}', re.DOTALL)

    search_start = max(0, pos - 500)
    matches = list(css_rule_pattern.finditer(content, search_start, min(pos + 500, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    return closest_match.group().strip()


def extract_class_from_tag(content: str, pos: int) -> Tuple[Optional[str], Optional[str]]:
    """Extract the tag and class name from the nearest tag with a single class."""
    tag_with_class = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)\s+class="([^"]+)"[^>]*>')

    search_start = max(0, pos - 200)
    matches = list(tag_with_class.finditer(content, search_start, min(pos + 200, len(content))))

    if not matches:
        return None, None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    tag_name = closest_match.group(1)
    class_name = closest_match.group(2)
    full_tag = closest_match.group()

    return full_tag, class_name


def extract_css_property(content: str, pos: int) -> Optional[str]:
    """Extract a single CSS property near the position."""
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
    """Extract a CSS selector near the position."""
    selector_pattern = re.compile(r'([.#]?[\w-]+(?:\s+[.#]?[\w-]+)*)\s*\{')

    search_start = max(0, pos - 300)
    matches = list(selector_pattern.finditer(content, search_start, min(pos + 300, len(content))))

    if not matches:
        return None

    closest_match = min(matches, key=lambda m: abs(m.start() - pos))
    return closest_match.group(1).strip()


def extract_css_rules_group(content: str, pos: int, num_rules: int = 2) -> Optional[str]:
    """Extract multiple consecutive CSS rules as a group."""
    css_rule_pattern = re.compile(r'[^{}]+\{[^{}]*\}', re.DOTALL)

    all_matches = list(css_rule_pattern.finditer(content))

    if len(all_matches) < num_rules:
        return None

    closest_idx = 0
    min_dist = float('inf')
    for i, match in enumerate(all_matches):
        dist = abs(match.start() - pos)
        if dist < min_dist:
            min_dist = dist
            closest_idx = i

    start_idx = min(closest_idx, len(all_matches) - num_rules)
    start_idx = max(0, start_idx)

    rules = []
    for i in range(start_idx, min(start_idx + num_rules, len(all_matches))):
        rules.append(all_matches[i].group().strip())

    if len(rules) < 2:
        return None

    return '\n'.join(rules)


# =============================================================================
# Inline repetition generators (length-matched, 13 sub-patterns)
# Each generator is length-matched: len(rejected) == len(chosen).
# These produce loops that DO eventually close </html> properly — they only
# corrupt a middle region with repetition.
# =============================================================================

def generate_char_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating 1-2 characters."""
    prefix = content[:start_pos]
    num_chars = random.choice([1, 2])
    if start_pos < len(content):
        loop_chars = content[start_pos:min(start_pos + num_chars, len(content))]
    else:
        loop_chars = 'a'
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    suffix = fill_to_length(loop_chars, remaining)
    return prefix + suffix


def generate_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating an HTML tag."""
    prefix = content[:start_pos]
    tag = extract_nearest_tag(content, start_pos)
    if not tag:
        return generate_char_loop(content, start_pos, target_length)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_section_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating an HTML section."""
    prefix = content[:start_pos]
    section = extract_html_section(content, start_pos)
    if not section:
        return generate_tag_loop(content, start_pos, target_length)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + section
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_incrementing_tag_loop_inline(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content with incrementing class numbers (inline mode)."""
    prefix = content[:start_pos]
    tag_template, class_name = extract_class_from_tag(content, start_pos)
    if not tag_template or not class_name:
        return generate_tag_loop(content, start_pos, target_length)
    tag_match = re.match(r'<([a-zA-Z][a-zA-Z0-9]*)', tag_template)
    if not tag_match:
        return generate_tag_loop(content, start_pos, target_length)
    tag_name = tag_match.group(1)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    suffix = ""
    counter = 1
    while len(suffix) < remaining:
        new_tag = f'{indent}<{tag_name} class="{class_name}-{counter}">'
        suffix += new_tag
        counter += 1
    suffix = suffix[:remaining]
    return prefix + suffix


def generate_closing_tag_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating closing tags."""
    prefix = content[:start_pos]
    closing_tag_pattern = re.compile(r'</[a-zA-Z][a-zA-Z0-9]*>')
    match = closing_tag_pattern.search(content, max(0, start_pos - 50))
    if match:
        closing_tag = match.group()
    else:
        closing_tag = '</div>'
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + closing_tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_self_closing_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating self-closing tags."""
    prefix = content[:start_pos]
    self_closing_pattern = re.compile(r'<(br|hr|img|input|meta|link)[^>]*/?>', re.IGNORECASE)
    match = self_closing_pattern.search(content, max(0, start_pos - 100))
    if match:
        self_closing_tag = match.group()
    else:
        self_closing_tag = random.choice(['<br/>', '<hr/>', '<input/>', '<img src=""/>'])
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + self_closing_tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_deeply_nested_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content with deep nesting (opening tags only)."""
    prefix = content[:start_pos]
    opening_tag_pattern = re.compile(r'<([a-zA-Z][a-zA-Z0-9]*)(?:\s+[^>]*)?>(?!/)')
    match = opening_tag_pattern.search(content, max(0, start_pos - 50))
    if match:
        tag_name = match.group(1)
        opening_tag = f'<{tag_name}>'
    else:
        opening_tag = '<div>'
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + opening_tag
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating a CSS rule."""
    prefix = content[:start_pos]
    css_rule = extract_css_rule(content, start_pos)
    if not css_rule:
        return generate_char_loop(content, start_pos, target_length)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + css_rule + '\n'
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_multi_rule_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating multiple CSS rules together."""
    prefix = content[:start_pos]
    num_rules = random.choice([2, 3])
    rules_group = extract_css_rules_group(content, start_pos, num_rules)
    if not rules_group:
        return generate_css_loop(content, start_pos, target_length)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    unit = indent + rules_group + '\n'
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_property_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating a CSS property."""
    prefix = content[:start_pos]
    css_property = extract_css_property(content, start_pos)
    if not css_property:
        css_property = 'color: red;'
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    unit = css_property + ' '
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_selector_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating CSS selectors."""
    prefix = content[:start_pos]
    selector = extract_css_selector(content, start_pos)
    if not selector:
        selector = '.class'
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    unit = selector + ' '
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_css_incrementing_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content with incrementing CSS selectors."""
    prefix = content[:start_pos]
    selector = extract_css_selector(content, start_pos)
    if not selector:
        selector = '.item'
    base_selector = re.sub(r'-?\d+$', '', selector)
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    indent = get_leading_indent(content, start_pos)
    suffix = ""
    counter = 1
    while len(suffix) < remaining:
        rule = f'{indent}{base_selector}-{counter} {{ }}\n'
        suffix += rule
        counter += 1
    suffix = suffix[:remaining]
    return prefix + suffix


def generate_css_value_loop(content: str, start_pos: int, target_length: int) -> str:
    """Generate loopy content by repeating CSS values."""
    prefix = content[:start_pos]
    value_pattern = re.compile(r':\s*([\d.]+(?:px|em|rem|%|vh|vw)?|\w+)')
    match = value_pattern.search(content, max(0, start_pos - 100))
    if match:
        value = match.group(1)
    else:
        value = '10px'
    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length]
    unit = value + ' '
    suffix = fill_to_length(unit, remaining)
    return prefix + suffix


def generate_inline_repetition(content: str) -> Tuple[str, str]:
    """
    Inline-repetition dispatcher (NO length match).
    Picks one of 13 loop types based on whether the random start position
    falls inside a CSS region or HTML region. target_length is drawn from a
    random fraction of len(content) so rejected length varies.
    """
    base_len = len(content)
    target_length = random.randint(max(64, base_len // 3), base_len) if base_len else 0
    # Anchor start_pos inside target_length so the loop has room to grow,
    # otherwise the generators would return a plain-prefix truncation.
    start_pct = random.uniform(0.01, 0.95)
    start_pos = int(target_length * start_pct)

    if is_in_css_region(content, start_pos):
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
        else:
            result = generate_css_multi_rule_loop(content, start_pos, target_length)
    else:
        html_choices = ["char", "tag", "section", "incrementing", "closing_tag", "self_closing", "deeply_nested"]
        loop_type = random.choice(html_choices)
        if loop_type == "char":
            result = generate_char_loop(content, start_pos, target_length)
        elif loop_type == "tag":
            result = generate_tag_loop(content, start_pos, target_length)
        elif loop_type == "section":
            result = generate_section_loop(content, start_pos, target_length)
        elif loop_type == "incrementing":
            result = generate_incrementing_tag_loop_inline(content, start_pos, target_length)
        elif loop_type == "closing_tag":
            result = generate_closing_tag_loop(content, start_pos, target_length)
        elif loop_type == "self_closing":
            result = generate_self_closing_loop(content, start_pos, target_length)
        else:
            result = generate_deeply_nested_loop(content, start_pos, target_length)

    return result, f"inline_{loop_type}"


# =============================================================================
# Completion-failure helpers (used by infinite-nesting / css / enumeration modes)
# =============================================================================

CANDIDATE_TAG_PATTERN = re.compile(r'<(div|input|span|p|li|a|section|article)\s+class="([\w\s-]+)"[^>]*>')
SIMPLE_TAG_PATTERN = re.compile(r'<(div|input|span|p|li|a|section|article)>')


def extract_candidate_tags(content: str, around_pos: int) -> List[Tuple[str, str]]:
    """
    Extract candidate (tag, class) pairs from content around `around_pos`.
    Returns up to ~50 unique pairs.
    """
    window_start = max(0, around_pos - 2000)
    window_end = min(len(content), around_pos + 2000)
    nearby = content[window_start:window_end]

    pairs = []
    seen = set()
    for m in CANDIDATE_TAG_PATTERN.finditer(nearby):
        tag = m.group(1)
        cls = m.group(2).strip()
        # Use first class only
        cls = cls.split()[0] if cls else ''
        if not cls:
            continue
        key = (tag, cls)
        if key not in seen:
            seen.add(key)
            pairs.append(key)
        if len(pairs) >= 50:
            break

    if not pairs:
        # Fallback: simple tags without class
        for m in SIMPLE_TAG_PATTERN.finditer(nearby):
            tag = m.group(1)
            key = (tag, '')
            if key not in seen:
                seen.add(key)
                pairs.append(key)
            if len(pairs) >= 10:
                break

    if not pairs:
        pairs = [('div', 'item')]

    return pairs


# Common CSS property bodies for the cycling-rule generator (matches row_047 pattern)
DEFAULT_CSS_BODIES = [
    'color: #fff',
    'background-color: #002386',
    'width: 40px;\n  height: 40px;\n  line-height: 40px',
    'font-size: 16px',
    'display: block',
    'margin: 0;\n  padding: 0',
    'text-align: center',
]

# Wrapper/child pairs for the enumeration-loop generator (matches row_127 pattern)
ENUMERATION_WRAPPERS = [
    ('select', 'option'),
    ('ul', 'li'),
    ('ol', 'li'),
    ('tbody', 'tr'),
    ('table', 'tr'),
    ('dl', 'dd'),
]


def generate_css_rule_cycling(content: str) -> Optional[Tuple[str, str]]:
    """
    Generate a CSS-rule-cycling failure (matches row_047 pattern).

    Finds an existing <style> block, extracts a deeply-nested selector from it,
    then fills the remainder with that same selector cycling through 4-5 different
    rule bodies. Never closes </style>, </body>, or </html>.

    Returns (rejected_content, 'css_rule_cycling') or None if no <style> block found.
    """
    target_length = len(content)

    # 1. Find a <style> block
    style_match = re.search(r'<style[^>]*>(.*?)</style>', content, re.DOTALL | re.IGNORECASE)
    if not style_match:
        return None
    style_inner_start = style_match.start(1)
    style_inner_end = style_match.end(1)
    if style_inner_end - style_inner_start < 200:
        return None  # too small to be useful

    # 2. Extract existing rules to find a deeply-nested selector
    style_inner = content[style_inner_start:style_inner_end]
    rule_matches = list(re.finditer(r'([^{}\n]+)\{([^{}]*)\}', style_inner))
    if len(rule_matches) < 3:
        return None

    # Prefer the longest selector (most "deeply nested" feel like row_047)
    selectors = [m.group(1).strip() for m in rule_matches]
    selectors.sort(key=len, reverse=True)
    selector = selectors[0]
    if len(selector) < 5:
        return None

    # 3. Pick cut point inside the style block (40-80% through it)
    cut_pct = random.uniform(0.40, 0.80)
    cut_pos = style_inner_start + int((style_inner_end - style_inner_start) * cut_pct)
    # Snap forward to end of nearest complete rule
    rule_end = content.find('}', cut_pos, style_inner_end)
    if rule_end != -1:
        cut_pos = rule_end + 1
        # Include trailing newlines
        while cut_pos < len(content) and content[cut_pos] == '\n':
            cut_pos += 1
    prefix = content[:cut_pos]

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length], 'css_rule_cycling'

    # 4. Sample 4-5 rule bodies. Prefer ones found in the existing CSS, fall back to defaults.
    existing_bodies = [m.group(2).strip() for m in rule_matches if m.group(2).strip()]
    n_bodies = random.randint(4, 5)
    if len(existing_bodies) >= n_bodies:
        bodies = random.sample(existing_bodies, n_bodies)
    else:
        bodies = existing_bodies + random.sample(DEFAULT_CSS_BODIES, n_bodies - len(existing_bodies))

    # 5. Fill with cycling rules
    suffix_parts: List[str] = []
    suffix_len = 0
    i = 0
    while suffix_len < remaining:
        body = bodies[i % len(bodies)]
        rule = f'\n{selector}{{\n  {body}\n}}\n'
        suffix_parts.append(rule)
        suffix_len += len(rule)
        i += 1

    full = prefix + ''.join(suffix_parts)
    return full[:target_length], 'css_rule_cycling'


def _detect_value_template(samples: List[str]) -> Tuple[str, Optional[int], int]:
    """
    Detect if a list of sample child contents follows a numeric pattern.

    Returns (template, start_value, step):
    - template: string with '{n}' as placeholder where the number goes
    - start_value: starting integer for the next value (None if no number found)
    - step: increment step (negative for decrementing, like row_127's year sequence)
    """
    if not samples:
        return '{n}', 1, 1

    # Try to find a number in the first sample
    first = samples[0].strip()
    nums = list(re.finditer(r'\d+', first))
    if not nums:
        return f'{first} {{n}}', 1, 1

    # Use the FIRST number that varies across samples
    for m in nums:
        position = m.start()
        try:
            extracted = []
            for s in samples[:5]:
                s = s.strip()
                # Find the number at the same approximate position
                local_match = re.search(r'\d+', s[max(0, position - 5):position + 10])
                if local_match:
                    extracted.append(int(local_match.group()))
            if len(extracted) >= 2 and len(set(extracted)) > 1:
                step = extracted[1] - extracted[0]
                if step == 0:
                    step = -1  # default to decrementing
                template = first[:m.start()] + '{n}' + first[m.end():]
                return template, extracted[-1] + step, step
        except (ValueError, IndexError):
            continue

    # Fallback: replace first number, default step -1 (matches row_127)
    first_num = nums[0]
    template = first[:first_num.start()] + '{n}' + first[first_num.end():]
    try:
        start = int(first_num.group()) - 1
    except ValueError:
        start = 1
    return template, start, -1


def generate_enumeration_loop(content: str) -> Optional[Tuple[str, str]]:
    """
    Generate an enumeration-loop failure (matches row_127 pattern).

    Finds a wrapper element with multiple same-tag children (e.g., <select><option>...,
    <ul><li>..., <table><tr>...), then fills the remainder with sequential children
    whose content follows a monotonic pattern (e.g., decrementing dates). Each child
    is properly opened/closed but the wrapper never closes.

    Returns (rejected_content, 'enumeration') or None if no suitable wrapper found.
    """
    target_length = len(content)

    candidates: List[Tuple[str, str, re.Match]] = []
    for parent, child in ENUMERATION_WRAPPERS:
        for m in re.finditer(
            rf'<{parent}[^>]*>(.*?)</{parent}>',
            content, re.DOTALL | re.IGNORECASE
        ):
            wrapper_inner = m.group(1)
            child_count = len(re.findall(rf'<{child}\b', wrapper_inner, re.IGNORECASE))
            if child_count >= 2:
                candidates.append((parent, child, m))

    if not candidates:
        return None

    # Pick a random qualifying wrapper
    parent, child, wrapper_match = random.choice(candidates)
    wrapper_inner = wrapper_match.group(1)

    # Extract existing children's text contents
    child_texts = re.findall(
        rf'<{child}[^>]*>\s*(.*?)\s*</{child}>',
        wrapper_inner, re.DOTALL | re.IGNORECASE
    )
    if not child_texts:
        return None

    # Cut just before the closing wrapper tag, after the last child
    last_child_close = content.rfind(f'</{child}>', 0, wrapper_match.end())
    if last_child_close == -1:
        return None
    cut_pos = last_child_close + len(f'</{child}>')
    # Include trailing newline if present
    if cut_pos < len(content) and content[cut_pos] == '\n':
        cut_pos += 1
    prefix = content[:cut_pos]

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length], 'enumeration'

    # Detect indentation of child elements from the last existing child
    last_child_open = content.rfind(f'<{child}', 0, last_child_close)
    if last_child_open != -1:
        line_start = content.rfind('\n', 0, last_child_open) + 1
        child_indent = content[line_start:last_child_open]
        # Inner indent (where the text value sits) is one level deeper
        if child_indent.endswith(' '):
            inner_indent = child_indent + ' '
        else:
            inner_indent = child_indent + ' '
    else:
        child_indent = ' ' * 8
        inner_indent = ' ' * 9

    # Detect the sequential value template from existing children
    template, start_value, step = _detect_value_template(child_texts)

    # Generate sequential children until we fill `remaining`
    suffix_parts: List[str] = []
    suffix_len = 0
    counter = start_value if start_value is not None else 1
    safety_limit = 10000  # avoid infinite loop on degenerate input
    iters = 0
    while suffix_len < remaining and iters < safety_limit:
        value = template.replace('{n}', str(counter))
        new_child = f'{child_indent}<{child}>\n{inner_indent}{value}\n{child_indent}</{child}>\n'
        suffix_parts.append(new_child)
        suffix_len += len(new_child)
        counter += step
        iters += 1

    full = prefix + ''.join(suffix_parts)
    return full[:target_length], 'enumeration'


# =============================================================================
# Additional completion-failure generators (whitespace, css bloat, section, etc.)
# Based on analysis of 1387 real failing predictions across all checkpoints
# =============================================================================

SECTION_PATTERN = re.compile(r'<(div|article|section|li)\s+class="[^"]+"[^>]*>.*?</\1>', re.DOTALL)


def _cut_at_line_boundary(content: str, cut_pct: float) -> int:
    """Pick a cut position then snap forward to nearest newline + 1."""
    pos = int(len(content) * cut_pct)
    nl = content.rfind('\n', 0, pos)
    return nl + 1 if nl != -1 else 0


def generate_whitespace_runaway(content: str) -> Optional[Tuple[str, str]]:
    """
    Whitespace-runaway failure: cut at 50-80%, fill rest with long whitespace
    runs (40-200 spaces per chunk) interspersed with rare orphan tags.
    Mimics row_123 pattern (522K chars of mostly whitespace, no </html>).
    """
    target = len(content)
    if target < 100:
        return None

    cut_pct = random.uniform(0.50, 0.80)
    cut_pos = _cut_at_line_boundary(content, cut_pct)
    prefix = content[:cut_pos]
    remaining = target - len(prefix)
    if remaining <= 0:
        return prefix[:target], 'whitespace_runaway'

    indent = get_leading_indent(content, cut_pos) or '\n'
    # Find an orphan tag to occasionally inject (matches the rare partial tag at row_123 tail)
    orphan = extract_nearest_tag(content, cut_pos) or '<div>'

    suffix_parts: List[str] = []
    suffix_len = 0
    safety = 50000
    iters = 0
    while suffix_len < remaining and iters < safety:
        n_spaces = random.randint(40, 200)
        chunk = indent + ' ' * n_spaces
        suffix_parts.append(chunk)
        suffix_len += len(chunk)
        # Rare orphan tag (~5% of chunks) — matches the trailing `<font>` / `<div class="...">`
        # observed at the end of real whitespace_runaway samples
        if random.random() < 0.05:
            suffix_parts.append(orphan)
            suffix_len += len(orphan)
        iters += 1

    full = prefix + ''.join(suffix_parts)
    return full[:target], 'whitespace_runaway'


def generate_css_bloat(content: str) -> Optional[Tuple[str, str]]:
    """
    CSS bloat failure: cut inside an existing <style> block, fill the remainder
    with VARIED CSS rules (each looks unique - selectors slightly mutated, bodies
    sampled from existing pool). Different from cf_css_rule_cycling which uses
    one selector cycling — here each rule looks distinct.

    Two variants are randomly produced:
      - HIGH-VOLUME (cf_css_bloat): many rules (10+), mimics row_127 (168 rules).
      - LOW-VOLUME (cf_css_truncated_other): only 3-8 rules then padded with
        whitespace, mimics the ~4% of failures where the model emits a few CSS
        rules then runs out of token budget without entering a clear loop.
    """
    target = len(content)
    style_match = re.search(r'<style[^>]*>(.*?)</style>', content, re.DOTALL | re.IGNORECASE)
    if not style_match:
        return None
    style_inner_start = style_match.start(1)
    style_inner_end = style_match.end(1)
    if style_inner_end - style_inner_start < 300:
        return None

    rule_matches = list(re.finditer(
        r'([^{}\n]+)\{([^{}]*)\}',
        content[style_inner_start:style_inner_end]
    ))
    if len(rule_matches) < 3:
        return None

    selectors_pool = [m.group(1).strip() for m in rule_matches if m.group(1).strip()]
    bodies_pool = [m.group(2).strip() for m in rule_matches if m.group(2).strip()]
    if not selectors_pool or not bodies_pool:
        return None

    # Cut after a complete rule, in the second half of the style block
    cut_pct = random.uniform(0.40, 0.75)
    cut_pos = style_inner_start + int((style_inner_end - style_inner_start) * cut_pct)
    rule_end = content.find('}', cut_pos, style_inner_end)
    if rule_end != -1:
        cut_pos = rule_end + 1
        while cut_pos < len(content) and content[cut_pos] == '\n':
            cut_pos += 1
    prefix = content[:cut_pos]
    remaining = target - len(prefix)
    if remaining <= 0:
        return prefix[:target], 'css_bloat'

    # 30% chance: low-volume variant (3-8 rules then whitespace pad).
    # This mimics the cf_css_truncated_other pattern (~4% of real failures where
    # the model emits a few CSS rules then runs out of tokens without looping).
    low_volume = random.random() < 0.30
    max_rules = random.randint(3, 8) if low_volume else None

    modifiers = ['', ':hover', ':focus', ' span', ' a', '>li', '>p', ' img', '.active']
    suffix_parts: List[str] = []
    suffix_len = 0
    safety = 50000
    iters = 0
    rules_added = 0
    while suffix_len < remaining and iters < safety:
        if max_rules is not None and rules_added >= max_rules:
            break
        sel = random.choice(selectors_pool)
        modifier = random.choice(modifiers)
        body = random.choice(bodies_pool)
        rule = f'\n{sel}{modifier}{{\n  {body}\n}}\n'
        suffix_parts.append(rule)
        suffix_len += len(rule)
        rules_added += 1
        iters += 1

    # Pad remaining space with whitespace (mimics token-budget exhaustion in
    # the low-volume case; in high-volume case the loop already filled it).
    if suffix_len < remaining:
        pad_chunk = '\n' + ' ' * 30
        while suffix_len < remaining:
            suffix_parts.append(pad_chunk)
            suffix_len += len(pad_chunk)

    full = prefix + ''.join(suffix_parts)
    label = 'css_truncated_other' if low_volume else 'css_bloat'
    return full[:target], label


def generate_truncated_padded(content: str) -> Optional[Tuple[str, str]]:
    """
    Clean truncation failure: model produces valid HTML, then runs out of token
    budget mid-content without entering any loop. Cut at 70-90% (later than
    other generators), pad with sparse whitespace (less dense than cf_whitespace_runaway).
    Mimics the ~5% of real failures classified as cf_truncated_clean.
    """
    target = len(content)
    if target < 100:
        return None

    # Cut later than other generators (70-90% through) so most of chosen is preserved
    cut_pct = random.uniform(0.70, 0.90)
    cut_pos = _cut_at_line_boundary(content, cut_pct)
    prefix = content[:cut_pos]
    remaining = target - len(prefix)
    if remaining <= 0:
        return prefix[:target], 'truncated_padded'

    indent = get_leading_indent(content, cut_pos) or '\n'
    # Sparse whitespace: each chunk is one indent + ~20 spaces (vs 40-200 for whitespace_runaway)
    pad_chunk = indent + ' ' * random.randint(15, 25)

    suffix_parts: List[str] = []
    suffix_len = 0
    safety = 50000
    iters = 0
    while suffix_len < remaining and iters < safety:
        suffix_parts.append(pad_chunk)
        suffix_len += len(pad_chunk)
        iters += 1

    full = prefix + ''.join(suffix_parts)
    return full[:target], 'truncated_padded'


def generate_section_repetition(content: str) -> Optional[Tuple[str, str]]:
    """
    Section-repetition failure: find a complete <div class="X">...</div> block
    in the chosen, repeat it many times to fill the remainder. No closing parent
    or </html>. Mimics row_102 pattern (entire section blocks repeated).
    """
    target = len(content)

    # Find class-bearing block elements with reasonable inner content size
    matches = []
    for m in SECTION_PATTERN.finditer(content):
        size = m.end() - m.start()
        if 100 <= size <= 2000:
            matches.append(m)
    if not matches:
        return None

    chosen_match = random.choice(matches)
    section = chosen_match.group()

    # Cut at end of this section
    cut_pos = chosen_match.end()
    if cut_pos < len(content) and content[cut_pos] == '\n':
        cut_pos += 1
    prefix = content[:cut_pos]
    remaining = target - len(prefix)
    if remaining <= 0:
        return prefix[:target], 'section_repetition'

    indent = get_leading_indent(content, cut_pos) or '\n'
    unit = indent + section
    suffix = fill_to_length(unit, remaining)
    return (prefix + suffix)[:target], 'section_repetition'


def generate_self_closing_spam(content: str) -> Optional[Tuple[str, str]]:
    """
    Self-closing spam failure: cut at 50-80%, find a self-closing tag (br/hr/img/
    input/meta/link), repeat with consistent indent. No closing structure.
    Mimics row_088 pattern (50+ <br/> repeated).
    """
    target = len(content)
    cut_pct = random.uniform(0.50, 0.80)
    cut_pos = _cut_at_line_boundary(content, cut_pct)
    prefix = content[:cut_pos]
    remaining = target - len(prefix)
    if remaining <= 0:
        return prefix[:target], 'self_closing_spam'

    sc_match = re.search(r'<(br|hr|img|input|meta|link)[^>]*/?>', content, re.IGNORECASE)
    if not sc_match:
        return None
    tag = sc_match.group()
    # Normalize to self-closing form if missing trailing /
    if not tag.endswith('/>'):
        tag = tag.rstrip('>') + '/>'

    indent = get_leading_indent(content, cut_pos) or '\n'
    unit = indent + tag
    suffix = fill_to_length(unit, remaining)
    return (prefix + suffix)[:target], 'self_closing_spam'


def generate_closing_tag_spam(content: str) -> Optional[Tuple[str, str]]:
    """
    Closing-tag spam failure: cut at 50-80%, find a closing tag (</div>, </span>),
    repeat many times. Mimics row_106 pattern (50+ </div> repeated).
    """
    target = len(content)
    cut_pct = random.uniform(0.50, 0.80)
    cut_pos = _cut_at_line_boundary(content, cut_pct)
    prefix = content[:cut_pos]
    remaining = target - len(prefix)
    if remaining <= 0:
        return prefix[:target], 'closing_tag_spam'

    close_matches = re.findall(r'</[a-zA-Z][a-zA-Z0-9]*>', prefix)
    if not close_matches:
        return None
    # Prefer recent closing tags (more likely to be plausible at the cut point)
    closing_tag = random.choice(close_matches[-10:])

    indent = get_leading_indent(content, cut_pos) or '\n'
    unit = indent + closing_tag
    suffix = fill_to_length(unit, remaining)
    return (prefix + suffix)[:target], 'closing_tag_spam'


def generate_infinite_nesting_rejected(content: str) -> Tuple[str, str]:
    """
    Generate a rejected sample covering both major DPO failure modes:

    A) COMPLETION-FAILURE patterns (12 sub-types) — model never reaches </html>
       Existing 5 (covering ~12% of real failures):
       - cf_cycling           (HTML)  : rotate 3-5 unique class names with growing indent (row_002)
       - cf_incrementing      (HTML)  : same base name + incrementing counter (row_089)
       - cf_pure              (HTML)  : single tag repeated identically with growing indent (row_116)
       - cf_css_rule_cycling  (CSS)   : same selector cycling 4-5 different rule bodies (row_047)
       - cf_enumeration       (HTML)  : list/select/table children with sequential values (row_127)
       NEW 7 (covering ~85% of real failures across Qwen3-VL-8B/4B and Qwen2.5-VL-3B):
       - cf_whitespace_runaway (~37%) : mostly whitespace runs after cut (row_123)
       - cf_css_bloat         (~12%)  : varied CSS rules in <style> until truncation (row_127)
       - cf_section_repetition (~10%) : complete <div class> blocks repeated (row_102)
       - cf_self_closing_spam  (~8%)  : <br/>/<img/>/<input/> repeated many times (row_088)
       - cf_closing_tag_spam   (~6%)  : </div>/</span> repeated many times (row_106)
       - cf_truncated_padded   (~5%)  : clean truncation with sparse whitespace padding
       - cf_css_truncated_other (~4%) : low-volume CSS bloat (3-8 rules then padded)

    B) INLINE-REPETITION patterns (13 sub-types) — model loops mid-document but still closes </html>
       - inline_char, inline_tag, inline_section, inline_incrementing,
         inline_closing_tag, inline_self_closing, inline_deeply_nested
       - inline_css_rule, inline_css_property, inline_css_selector,
         inline_css_incrementing, inline_css_value, inline_css_multi_rule

    Length is NOT matched: target_length is drawn as a random fraction of
    len(chosen), so rejected samples vary in length (comparable in spirit to
    transform_to_dpo.py, whose randomized repetition counts also produce
    variable-length output).

    The mode (A or B) is chosen randomly with equal weight; within each mode the
    specific sub-pattern is also random. CSS/enumeration completion patterns may
    require structures not present in the chosen content; in that case they fall
    back to the always-applicable HTML completion patterns.

    Returns:
        Tuple of (rejected_content, sub_pattern_used)
    """
    base_len = len(content)
    if base_len == 0:
        return content, 'cf_pure'
    # Randomize target length so rejected samples have variable length
    # (matches the variance behavior of transform_to_dpo.py rather than the
    # strict length-match of the sibling length-matched script).
    target_length = random.randint(max(64, base_len // 3), base_len)

    # ----- Top-level mode dispatch: completion failure vs inline repetition -----
    mode = random.choice(['completion_failure', 'inline_repetition'])

    if mode == 'inline_repetition':
        return generate_inline_repetition(content)

    # ----- Completion-failure mode -----
    # Pick a sub-pattern with frequency-weighted distribution matching real failure rates
    # observed across ~3800 failing predictions in 86 inference output dirs across
    # Qwen3-VL-8B, Qwen3-VL-4B, and Qwen2.5-VL-3B models.
    # Prerequisite-dependent generators (css/section/enumeration) may return None;
    # in that case we fall back to an always-applicable HTML pattern.
    chosen_pattern = random.choices(
        [
            'cycling', 'incrementing', 'pure',                  # always applicable (~0.5% real)
            'css_rule_cycling', 'enumeration',                  # existing structured (~12% + ~2%)
            'whitespace_runaway', 'css_bloat', 'section_repetition',  # NEW (~37% + ~12% + ~10%)
            'self_closing_spam', 'closing_tag_spam',            # NEW (~8% + ~6%)
            'truncated_padded',                                  # NEW (~5% — clean truncation)
        ],
        weights=[2, 2, 2, 12, 2, 37, 12, 10, 8, 6, 5],
        k=1,
    )[0]

    # Try the prerequisite-dependent patterns first; fall back if they return None.
    if chosen_pattern == 'css_rule_cycling':
        result = generate_css_rule_cycling(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'enumeration':
        result = generate_enumeration_loop(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'whitespace_runaway':
        result = generate_whitespace_runaway(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'css_bloat':
        result = generate_css_bloat(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'section_repetition':
        result = generate_section_repetition(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'self_closing_spam':
        result = generate_self_closing_spam(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'closing_tag_spam':
        result = generate_closing_tag_spam(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    if chosen_pattern == 'truncated_padded':
        result = generate_truncated_padded(content)
        if result is not None:
            rej, name = result
            return rej, f'cf_{name}'
        chosen_pattern = random.choice(['cycling', 'incrementing', 'pure'])

    # ----- HTML completion-failure sub-patterns (always applicable) -----
    # 1. Pick cut point: 30%-70% through (substantial correct prefix)
    cut_pct = random.uniform(0.30, 0.70)
    cut_pos = int(target_length * cut_pct)
    # Snap to nearest preceding newline
    nl = content.rfind('\n', 0, cut_pos)
    cut_pos = nl + 1 if nl != -1 else 0
    prefix = content[:cut_pos]

    # 2. Detect current indent level at the cut point
    last_line = prefix.rstrip('\n').split('\n')[-1] if prefix else ''
    base_indent = len(last_line) - len(last_line.lstrip(' '))

    # 3. Extract candidate tags
    candidates = extract_candidate_tags(content, cut_pos)

    sub_pattern = chosen_pattern
    suffix_parts: List[str] = []
    suffix_len = 0
    indent = base_indent + 1

    remaining = target_length - len(prefix)
    if remaining <= 0:
        return prefix[:target_length], f'cf_{sub_pattern}'

    if sub_pattern == 'cycling':
        n = min(random.randint(3, 5), len(candidates))
        cycle = random.sample(candidates, n) if len(candidates) >= n else candidates
        i = 0
        while suffix_len < remaining:
            tag, cls = cycle[i % len(cycle)]
            if cls:
                line = ' ' * indent + f'<{tag} class="{cls}">\n'
            else:
                line = ' ' * indent + f'<{tag}>\n'
            suffix_parts.append(line)
            suffix_len += len(line)
            indent += 1
            i += 1

    elif sub_pattern == 'incrementing':
        tag, base_cls = random.choice(candidates)
        base_cls = re.sub(r'\d+$', '', base_cls) or 'item'
        counter = random.randint(1, 100)
        while suffix_len < remaining:
            line = ' ' * indent + f'<{tag} class="{base_cls}{counter}">\n'
            suffix_parts.append(line)
            suffix_len += len(line)
            counter += 1
            # Indent grows slower in incrementing pattern (matches row_089)
            if random.random() < 0.5:
                indent += 1

    else:  # pure
        tag, cls = random.choice(candidates)
        while suffix_len < remaining:
            if cls:
                line = ' ' * indent + f'<{tag} class="{cls}">\n'
            else:
                line = ' ' * indent + f'<{tag}>\n'
            suffix_parts.append(line)
            suffix_len += len(line)
            indent += 1

    suffix = ''.join(suffix_parts)
    full = prefix + suffix
    # No length-match trim: return natural generator output.
    return full, f'cf_{sub_pattern}'


# =============================================================================
# ms_swift entry processing
# =============================================================================

def process_entry(entry: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """
    Process a single ms_swift entry to DPO format.

    Returns:
        Tuple of (dpo_entry, skip_reason, sub_pattern)
    """
    user_msg = None
    assistant_msg = None

    for msg in entry.get("messages", []):
        role = msg.get("role", "")
        if role == "user":
            user_msg = msg.get("content", "")
        elif role == "assistant":
            assistant_msg = msg.get("content", "")

    if not user_msg:
        return None, "no_user_message", None

    if not assistant_msg:
        return None, "no_assistant_response", None

    try:
        rejected_content, sub_pattern = generate_infinite_nesting_rejected(assistant_msg)
    except Exception as e:
        return None, f"error: {str(e)}", None

    dpo_entry = {
        "query": user_msg,
        "response": assistant_msg,
        "rejected_response": rejected_content,
        "images": entry.get("images", []),
    }

    return dpo_entry, None, sub_pattern


def main():
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL dataset to DPO format with infinite-nesting rejected samples (NO length-match guarantee)"
    )
    parser.add_argument("--input", "-i", required=True, help="Input JSONL file path (ms_swift format)")
    parser.add_argument("--output", "-o", required=True, help="Output JSONL file path (DPO format)")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit number of entries (default: all)")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    total = 0
    written = 0
    skipped = 0
    length_mismatches = 0
    skip_reasons: Dict[str, List[str]] = {}
    sub_pattern_counts: Dict[str, int] = {}

    print(f"Loading input file: {args.input}")
    print(f"Output file: {args.output}")
    if args.limit:
        print(f"Limit: {args.limit} entries")
    print(f"Seed: {args.seed}")
    print("\nProcessing entries...")

    with open(args.input, "r", encoding="utf-8") as fin, \
         open(args.output, "w", encoding="utf-8") as fout:

        for i, line in enumerate(fin):
            if args.limit and written >= args.limit:
                break

            line = line.strip()
            if not line:
                continue

            total += 1

            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"Warning: Skipping line {i + 1} (invalid JSON): {e}", file=sys.stderr)
                skipped += 1
                skip_reasons.setdefault("invalid_json", []).append(f"line_{i + 1}")
                continue

            dpo_entry, skip_reason, sub_pattern = process_entry(entry)

            if skip_reason:
                skipped += 1
                skip_reasons.setdefault(skip_reason, []).append(f"line_{i + 1}")
            else:
                if len(dpo_entry["rejected_response"]) != len(dpo_entry["response"]):
                    length_mismatches += 1
                fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
                written += 1
                if sub_pattern:
                    sub_pattern_counts[sub_pattern] = sub_pattern_counts.get(sub_pattern, 0) + 1

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries...")

    if skip_reasons:
        skip_file = args.output + ".skipped.json"
        print(f"\nSaving skip report to: {skip_file}")
        with open(skip_file, "w", encoding="utf-8") as f:
            json.dump(skip_reasons, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 60)
    print("TRANSFORMATION STATISTICS (INFINITE NESTING + INLINE — NO LENGTH MATCH)")
    print("=" * 60)
    print(f"Total lines processed: {total}")
    print(f"Successfully transformed: {written}")
    print(f"Skipped: {skipped}")
    print(f"Length mismatches: {length_mismatches}")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, entries in skip_reasons.items():
            print(f"  {reason}: {len(entries)}")

    # Group sub-patterns by mode for clearer reporting
    cf_total = sum(c for k, c in sub_pattern_counts.items() if k.startswith('cf_'))
    inline_total = sum(c for k, c in sub_pattern_counts.items() if k.startswith('inline_'))

    print(f"\nMode distribution:")
    print(f"  Completion failure (cf_*): {cf_total} ({cf_total/written*100:.1f}%)" if written else "  Completion failure: 0")
    print(f"  Inline repetition (inline_*): {inline_total} ({inline_total/written*100:.1f}%)" if written else "  Inline repetition: 0")

    print("\nSub-pattern distribution:")
    for sub_pattern, count in sorted(sub_pattern_counts.items()):
        if count > 0:
            pct = (count / written * 100) if written else 0
            print(f"  {sub_pattern}: {count} ({pct:.1f}%)")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
