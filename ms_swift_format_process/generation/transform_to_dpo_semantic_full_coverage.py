#!/usr/bin/env python3
"""
Transform ms_swift JSONL dataset to DPO format with FULL-COVERAGE semantic
rejected samples. Sibling to transform_to_dpo_semantic_multi_axis.py that
extends coverage from 8 to 16 perturbation categories, reaching the long
tail of CSS / HTML visual semantics.

Perturbation categories (all default-on, toggleable via --categories):

  === Existing 8 axes (inherited from multi_axis) ===
  1.  colors          — hex / rgb / rgba / hsl / hsla / 31 named CSS colors
  2.  dimensions      — px/em/rem/%/vh/vw/pt/cm/mm/in/deg scaled by discrete factors
  3.  opacity         — opacity/fill-opacity/stroke-opacity unitless floats
  4.  fonts           — font-family replaced with visually distinct stacks
  5.  font_weight     — font-weight remapped via swap table
  6.  transform       — rotate/scale/translate/skew function args perturbed
  7.  display         — display/flex-direction/justify-content/align-items
  8.  img_dims        — <img width= height=> HTML attributes

  === New 8 axes (full_coverage only) ===
  9.  text_style      — text-align / text-decoration / text-transform / font-style
  10. position        — position / float / clear keyword swaps
  11. overflow        — overflow / visibility / white-space / box-sizing
  12. border_style    — border-style / outline-style / list-style-type / cursor
  13. filter          — CSS filter fn args (blur/brightness/grayscale/…) +
                        mix-blend-mode keyword swap
  14. background      — background-position / background-size / background-repeat
  15. unitless_number — z-index / order / flex-grow / flex-shrink / line-height /
                        tab-size / column-count (dimension-regex-anchored only
                        missed these because they have no unit)
  16. table_attrs     — <table border= cellpadding= cellspacing=> and
                        <td/th colspan= rowspan=> HTML integer attributes
                        (operates outside CSS scope, like img_dims)

The rejected HTML is therefore:
  - Syntactically valid and structurally identical to the chosen HTML
  - Visually wrong across 16 independent axes — typography, positioning,
    overflow/visibility, borders, filters, blend modes, backgrounds, layout
    counts, and table structure
  - The strongest DPO signal available in this file family; best paired
    with ablations against the 8-axis multi_axis baseline to measure the
    marginal value of each extra category.

All CSS-scoped perturbations operate inside <style>…</style> blocks and
style="..." / style='...' attributes. The img_dims and table_attrs
categories operate on the full HTML (outside CSS). Text content, class
names, and resource URLs are never touched.

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
    "rejected_response": "<!DOCTYPE html>... [semantic perturbation]",
    "images": ["images-00000.tar/chunk_0_row_0.png"]
  }

Usage:
  python3 transform_to_dpo_semantic_full_coverage.py --input data.jsonl --output data_dpo.jsonl \
      [--limit N] [--seed S] [--perturb-rate 0.7] \
      [--categories colors,dimensions,opacity,fonts,font_weight,transform,display,img_dims,text_style,position,overflow,border_style,filter,background,unitless_number,table_attrs]
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path
from typing import Tuple, Optional, Dict, Any, List, Set


# =============================================================================
# Color / dimension vocabularies and regexes
# =============================================================================

# CSS named colors we are willing to substitute. Limited to visually distinct
# colors so every swap is a meaningful perceptual change.
NAMED_COLORS = [
    'black', 'white', 'red', 'green', 'blue', 'yellow', 'orange', 'purple',
    'pink', 'brown', 'gray', 'grey', 'cyan', 'magenta', 'lime', 'navy',
    'teal', 'olive', 'maroon', 'silver', 'gold', 'indigo', 'violet', 'tan',
    'beige', 'coral', 'salmon', 'khaki', 'crimson', 'aqua', 'turquoise',
]
NAMED_COLOR_SET = {c.lower() for c in NAMED_COLORS}

# Named-color synonyms — swapping between these produces no visible change,
# so pick_different_named_color() filters them out.
NAMED_COLOR_SYNONYMS: Dict[str, str] = {
    'aqua': 'cyan',
    'cyan': 'aqua',
    'grey': 'gray',
    'gray': 'grey',
}

# Hex colors (#rgb, #rgba, #rrggbb, #rrggbbaa). Anchored alternation with
# longest-first so '#abc123de' isn't truncated to '#abc123d'. The trailing
# negative lookahead rules out matching inside a longer hex literal.
HEX_COLOR_RE = re.compile(
    r'#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])'
)

# rgb() / rgba() functional notation
RGB_COLOR_RE = re.compile(r'rgba?\(\s*[\d.,\s%/]+\)', re.IGNORECASE)

# hsl() / hsla() functional notation — the '%' is optional for the hue but
# required for saturation/lightness in practice, so we accept any mix.
HSL_COLOR_RE = re.compile(r'hsla?\(\s*[\d.,\s%/deg]+\)', re.IGNORECASE)

# Named colors — matched with word boundaries so they don't eat class names.
# The negative lookahead `(?!-[a-zA-Z])` rejects compound CSS identifiers that
# start with a color word (e.g. `white-space`, `red-eye`, `blue-violet` if
# we were to add it), which would otherwise cause false-positive swaps inside
# property names like `white-space: nowrap`.
NAMED_COLOR_RE = re.compile(
    r'\b(' + '|'.join(sorted(NAMED_COLORS, key=len, reverse=True)) + r')\b(?!-[a-zA-Z])',
    re.IGNORECASE,
)

# Dimension values — numbers with a CSS length or angle unit. The lookbehind
# prevents matching inside words/identifiers (e.g. class names with digits).
# 'deg' is now included so gradient angles and unit-bearing transform
# arguments are scaled the same way as sizes.
DIMENSION_RE = re.compile(
    r'(?<![\w.#-])(-?\d+(?:\.\d+)?)(px|em|rem|vh|vw|pt|cm|mm|in|deg|%)\b',
    re.IGNORECASE,
)

# Discrete scale multipliers for dimension perturbation — every multiplier is
# far enough from 1.0 to be visually obvious.
DIM_MULTIPLIERS = [0.25, 0.5, 2.0, 3.0, 4.0]

# -----------------------------------------------------------------------------
# Multi-axis perturbation vocabularies
# -----------------------------------------------------------------------------

# Opacity / fill-opacity / stroke-opacity: property-anchored unitless float.
OPACITY_RE = re.compile(
    r'(\b(?:opacity|fill-opacity|stroke-opacity)\s*:\s*)(\d*\.?\d+)',
    re.IGNORECASE,
)

# font-family: property-anchored value (stops at ; or } or newline).
FONT_FAMILY_RE = re.compile(
    r'(\bfont-family\s*:\s*)([^;}\n]+)',
    re.IGNORECASE,
)

# font-weight: property-anchored value. Accepts keywords or 100-900.
FONT_WEIGHT_RE = re.compile(
    r'(\bfont-weight\s*:\s*)(normal|bold|lighter|bolder|[1-9]00)\b',
    re.IGNORECASE,
)

# transform: property-anchored value block (entire function list).
TRANSFORM_RE = re.compile(
    r'(\btransform\s*:\s*)([^;}\n]+)',
    re.IGNORECASE,
)

# Single transform function calls inside a transform: block, e.g. rotate(45deg),
# scale(1.2), translate(10px, 20px). Matched with the function name captured so
# we can dispatch per-function perturbation logic.
TRANSFORM_FN_RE = re.compile(
    r'(rotate|rotateX|rotateY|rotateZ|scale|scaleX|scaleY|translate|translateX|translateY|skew|skewX|skewY)\(\s*([^)]*)\)',
    re.IGNORECASE,
)

# display keyword. Anchored so we only catch 'display:' values, not text.
DISPLAY_RE = re.compile(
    r'(\bdisplay\s*:\s*)(flex|grid|block|inline|inline-block|inline-flex|inline-grid|none|table|table-cell)\b',
    re.IGNORECASE,
)

# flex-direction / justify-content / align-items keyword values.
FLEX_DIRECTION_RE = re.compile(
    r'(\bflex-direction\s*:\s*)(row|row-reverse|column|column-reverse)\b',
    re.IGNORECASE,
)
JUSTIFY_CONTENT_RE = re.compile(
    r'(\bjustify-content\s*:\s*)(flex-start|flex-end|center|space-between|space-around|space-evenly|start|end)\b',
    re.IGNORECASE,
)
ALIGN_ITEMS_RE = re.compile(
    r'(\balign-items\s*:\s*)(flex-start|flex-end|center|stretch|baseline|start|end)\b',
    re.IGNORECASE,
)

# <img width=...> and <img height=...> HTML attribute — perturbed outside CSS.
IMG_DIM_ATTR_RE = re.compile(
    r'(<img\b[^>]*?\s(?:width|height)\s*=\s*["\']?)(\d+)(?:px)?(["\']?)',
    re.IGNORECASE,
)

# Font-family replacement pool. Each entry is a visually distinct CSS stack.
FONT_FAMILY_POOL: List[str] = [
    "'Times New Roman', Times, serif",
    "Arial, Helvetica, sans-serif",
    "'Courier New', Courier, monospace",
    "Georgia, 'Times New Roman', serif",
    "'Comic Sans MS', 'Comic Sans', cursive",
    "Impact, 'Arial Black', sans-serif",
    "Verdana, Geneva, sans-serif",
    "'Lucida Console', Monaco, monospace",
]

# font-weight swap table. Keys lowercased; swap produces a visibly different
# weight (lightest <-> heaviest, medium <-> extreme).
FONT_WEIGHT_SWAP: Dict[str, str] = {
    'normal': 'bold',
    'bold': 'normal',
    'lighter': 'bolder',
    'bolder': 'lighter',
    '100': '900',
    '200': '800',
    '300': '700',
    '400': '900',
    '500': '100',
    '600': '200',
    '700': '300',
    '800': '200',
    '900': '400',
}

# display keyword scramble — each value maps to a visually different display.
DISPLAY_SWAP: Dict[str, str] = {
    'flex': 'block',
    'grid': 'block',
    'block': 'inline-block',
    'inline': 'block',
    'inline-block': 'block',
    'inline-flex': 'block',
    'inline-grid': 'block',
    'none': 'block',
    'table': 'block',
    'table-cell': 'inline-block',
}

FLEX_DIRECTION_SWAP: Dict[str, str] = {
    'row': 'column',
    'row-reverse': 'column-reverse',
    'column': 'row',
    'column-reverse': 'row-reverse',
}

JUSTIFY_CONTENT_SWAP: Dict[str, str] = {
    'flex-start': 'flex-end',
    'flex-end': 'flex-start',
    'start': 'end',
    'end': 'start',
    'center': 'space-between',
    'space-between': 'center',
    'space-around': 'flex-start',
    'space-evenly': 'flex-end',
}

ALIGN_ITEMS_SWAP: Dict[str, str] = {
    'flex-start': 'flex-end',
    'flex-end': 'flex-start',
    'start': 'end',
    'end': 'start',
    'center': 'baseline',
    'stretch': 'center',
    'baseline': 'stretch',
}


# -----------------------------------------------------------------------------
# EXTENDED COVERAGE: typography / layout flow / overflow / borders / filters /
# backgrounds / unitless numbers / HTML table attrs
# -----------------------------------------------------------------------------

# --- Typography keyword swaps (category: text_style) -------------------------
TEXT_ALIGN_RE = re.compile(
    r'(\btext-align\s*:\s*)(left|right|center|justify|start|end)\b', re.IGNORECASE)
TEXT_DECORATION_RE = re.compile(
    r'(\btext-decoration(?:-line)?\s*:\s*)(none|underline|overline|line-through)\b', re.IGNORECASE)
TEXT_TRANSFORM_RE = re.compile(
    r'(\btext-transform\s*:\s*)(none|uppercase|lowercase|capitalize)\b', re.IGNORECASE)
FONT_STYLE_RE = re.compile(
    r'(\bfont-style\s*:\s*)(normal|italic|oblique)\b', re.IGNORECASE)

TEXT_ALIGN_SWAP: Dict[str, str] = {
    'left': 'right', 'right': 'left',
    'center': 'justify', 'justify': 'center',
    'start': 'end', 'end': 'start',
}
TEXT_DECORATION_SWAP: Dict[str, str] = {
    'none': 'underline', 'underline': 'line-through',
    'overline': 'underline', 'line-through': 'none',
}
TEXT_TRANSFORM_SWAP: Dict[str, str] = {
    'none': 'uppercase', 'uppercase': 'lowercase',
    'lowercase': 'capitalize', 'capitalize': 'none',
}
FONT_STYLE_SWAP: Dict[str, str] = {
    'normal': 'italic', 'italic': 'normal', 'oblique': 'normal',
}

# --- Layout flow keyword swaps (category: position) --------------------------
POSITION_RE = re.compile(
    r'(\bposition\s*:\s*)(static|relative|absolute|fixed|sticky)\b', re.IGNORECASE)
FLOAT_RE = re.compile(
    r'(\bfloat\s*:\s*)(none|left|right)\b', re.IGNORECASE)
CLEAR_RE = re.compile(
    r'(\bclear\s*:\s*)(none|left|right|both)\b', re.IGNORECASE)

POSITION_SWAP: Dict[str, str] = {
    'static': 'absolute', 'relative': 'fixed',
    'absolute': 'static', 'fixed': 'relative', 'sticky': 'fixed',
}
FLOAT_SWAP: Dict[str, str] = {
    'none': 'left', 'left': 'right', 'right': 'none',
}
CLEAR_SWAP: Dict[str, str] = {
    'none': 'both', 'left': 'right', 'right': 'left', 'both': 'none',
}

# --- Overflow / visibility (category: overflow) ------------------------------
OVERFLOW_RE = re.compile(
    r'(\boverflow(?:-x|-y)?\s*:\s*)(visible|hidden|scroll|auto|clip)\b', re.IGNORECASE)
VISIBILITY_RE = re.compile(
    r'(\bvisibility\s*:\s*)(visible|hidden|collapse)\b', re.IGNORECASE)
WHITESPACE_RE = re.compile(
    r'(\bwhite-space\s*:\s*)(normal|nowrap|pre|pre-wrap|pre-line)\b', re.IGNORECASE)
BOX_SIZING_RE = re.compile(
    r'(\bbox-sizing\s*:\s*)(content-box|border-box)\b', re.IGNORECASE)

OVERFLOW_SWAP: Dict[str, str] = {
    'visible': 'hidden', 'hidden': 'visible',
    'scroll': 'auto', 'auto': 'hidden', 'clip': 'visible',
}
VISIBILITY_SWAP: Dict[str, str] = {
    'visible': 'hidden', 'hidden': 'visible', 'collapse': 'visible',
}
WHITESPACE_SWAP: Dict[str, str] = {
    'normal': 'nowrap', 'nowrap': 'normal',
    'pre': 'pre-wrap', 'pre-wrap': 'pre', 'pre-line': 'pre',
}
BOX_SIZING_SWAP: Dict[str, str] = {
    'content-box': 'border-box', 'border-box': 'content-box',
}

# --- Border / outline / list / cursor styles (category: border_style) --------
BORDER_STYLE_RE = re.compile(
    r'(\b(?:border|outline)(?:-(?:top|right|bottom|left))?-style\s*:\s*)'
    r'(none|solid|dashed|dotted|double|groove|ridge|inset|outset)\b',
    re.IGNORECASE,
)
LIST_STYLE_TYPE_RE = re.compile(
    r'(\blist-style-type\s*:\s*)'
    r'(disc|circle|square|decimal|decimal-leading-zero|lower-roman|upper-roman|lower-alpha|upper-alpha|none)\b',
    re.IGNORECASE,
)
CURSOR_RE = re.compile(
    r'(\bcursor\s*:\s*)'
    r'(auto|default|pointer|text|wait|help|crosshair|move|not-allowed|grab|grabbing)\b',
    re.IGNORECASE,
)

BORDER_STYLE_SWAP: Dict[str, str] = {
    'none': 'solid', 'solid': 'dashed', 'dashed': 'dotted', 'dotted': 'double',
    'double': 'none', 'groove': 'ridge', 'ridge': 'groove',
    'inset': 'outset', 'outset': 'inset',
}
LIST_STYLE_TYPE_SWAP: Dict[str, str] = {
    'disc': 'square', 'circle': 'disc', 'square': 'circle',
    'decimal': 'lower-roman', 'decimal-leading-zero': 'decimal',
    'lower-roman': 'upper-roman', 'upper-roman': 'decimal',
    'lower-alpha': 'upper-alpha', 'upper-alpha': 'lower-alpha',
    'none': 'disc',
}
CURSOR_SWAP: Dict[str, str] = {
    'auto': 'pointer', 'default': 'not-allowed', 'pointer': 'default',
    'text': 'move', 'wait': 'crosshair', 'help': 'default',
    'crosshair': 'move', 'move': 'wait', 'not-allowed': 'pointer',
    'grab': 'grabbing', 'grabbing': 'grab',
}

# --- Filter functions + mix-blend-mode (category: filter) --------------------
FILTER_RE = re.compile(r'(\bfilter\s*:\s*)([^;}\n]+)', re.IGNORECASE)
FILTER_FN_RE = re.compile(
    r'(blur|brightness|contrast|grayscale|invert|hue-rotate|saturate|sepia|opacity)\(\s*([^)]*)\)',
    re.IGNORECASE,
)
MIX_BLEND_MODE_RE = re.compile(
    r'(\bmix-blend-mode\s*:\s*)'
    r'(normal|multiply|screen|overlay|darken|lighten|color-dodge|color-burn|hard-light|soft-light|difference|exclusion|hue|saturation|color|luminosity)\b',
    re.IGNORECASE,
)
MIX_BLEND_MODE_SWAP: Dict[str, str] = {
    'normal': 'multiply', 'multiply': 'screen', 'screen': 'overlay',
    'overlay': 'darken', 'darken': 'lighten', 'lighten': 'difference',
    'color-dodge': 'color-burn', 'color-burn': 'color-dodge',
    'hard-light': 'soft-light', 'soft-light': 'hard-light',
    'difference': 'exclusion', 'exclusion': 'difference',
    'hue': 'saturation', 'saturation': 'color',
    'color': 'luminosity', 'luminosity': 'hue',
}

# --- Background layout keyword swaps (category: background) ------------------
BG_POSITION_RE = re.compile(
    r'(\bbackground-position\s*:\s*)(top|right|bottom|left|center)\b', re.IGNORECASE)
BG_SIZE_RE = re.compile(
    r'(\bbackground-size\s*:\s*)(auto|cover|contain)\b', re.IGNORECASE)
BG_REPEAT_RE = re.compile(
    r'(\bbackground-repeat\s*:\s*)(repeat|no-repeat|repeat-x|repeat-y|space|round)\b', re.IGNORECASE)

BG_POSITION_SWAP: Dict[str, str] = {
    'top': 'bottom', 'right': 'left', 'bottom': 'top',
    'left': 'right', 'center': 'top',
}
BG_SIZE_SWAP: Dict[str, str] = {
    'auto': 'cover', 'cover': 'contain', 'contain': 'auto',
}
BG_REPEAT_SWAP: Dict[str, str] = {
    'repeat': 'no-repeat', 'no-repeat': 'repeat',
    'repeat-x': 'repeat-y', 'repeat-y': 'repeat-x',
    'space': 'round', 'round': 'space',
}

# --- Unitless numbers (category: unitless_number) ----------------------------
# Property-anchored so we don't touch random digits in text / classnames.
UNITLESS_NUM_RE = re.compile(
    r'(\b(?:z-index|order|flex-grow|flex-shrink|line-height|tab-size|column-count)\s*:\s*)'
    r'(-?\d+(?:\.\d+)?)(?=\s*(?:;|}|\n|\Z))',
    re.IGNORECASE,
)

# --- HTML table integer attributes (category: table_attrs) -------------------
TABLE_BORDER_RE = re.compile(
    r'(<table\b[^>]*?\sborder\s*=\s*["\']?)(\d+)(["\']?)', re.IGNORECASE)
TABLE_CELLPADDING_RE = re.compile(
    r'(<table\b[^>]*?\scellpadding\s*=\s*["\']?)(\d+)(["\']?)', re.IGNORECASE)
TABLE_CELLSPACING_RE = re.compile(
    r'(<table\b[^>]*?\scellspacing\s*=\s*["\']?)(\d+)(["\']?)', re.IGNORECASE)
COLSPAN_RE = re.compile(
    r'(<t[dh]\b[^>]*?\scolspan\s*=\s*["\']?)(\d+)(["\']?)', re.IGNORECASE)
ROWSPAN_RE = re.compile(
    r'(<t[dh]\b[^>]*?\srowspan\s*=\s*["\']?)(\d+)(["\']?)', re.IGNORECASE)


# -----------------------------------------------------------------------------
# Category flags
# -----------------------------------------------------------------------------

ALL_CATEGORIES = (
    # 8 inherited from multi_axis
    'colors', 'dimensions', 'opacity', 'fonts', 'font_weight',
    'transform', 'display', 'img_dims',
    # 8 new in full_coverage
    'text_style', 'position', 'overflow', 'border_style',
    'filter', 'background', 'unitless_number', 'table_attrs',
)


class CategoryFlags:
    """Per-category enable flags. Default = all on."""
    __slots__ = ALL_CATEGORIES

    def __init__(self, enabled: Optional[set] = None) -> None:
        on = enabled if enabled is not None else set(ALL_CATEGORIES)
        for name in ALL_CATEGORIES:
            setattr(self, name, name in on)

    @classmethod
    def from_csv(cls, csv: Optional[str]) -> 'CategoryFlags':
        if csv is None or csv.strip() == '' or csv.strip().lower() == 'all':
            return cls()
        raw = {p.strip().lower() for p in csv.split(',') if p.strip()}
        unknown = raw - set(ALL_CATEGORIES)
        if unknown:
            raise ValueError(
                f"Unknown category/categories: {sorted(unknown)}. "
                f"Valid: {sorted(ALL_CATEGORIES)}"
            )
        return cls(enabled=raw)


# =============================================================================
# Perturbation primitives
# =============================================================================

def random_hex_color() -> str:
    """Return a random 6-digit hex color like '#a3f92c'."""
    return '#{:06x}'.format(random.randint(0, 0xFFFFFF))


def random_rgb_color() -> str:
    """Return a random rgb() color string."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    return f'rgb({r}, {g}, {b})'


def random_rgba_color() -> str:
    """Return a random rgba() color string."""
    r = random.randint(0, 255)
    g = random.randint(0, 255)
    b = random.randint(0, 255)
    a = round(random.uniform(0.1, 1.0), 2)
    return f'rgba({r}, {g}, {b}, {a})'


def random_hsl_color() -> str:
    """Return a random hsl() color string."""
    h = random.randint(0, 359)
    s = random.randint(20, 100)
    l = random.randint(15, 85)
    return f'hsl({h}, {s}%, {l}%)'


def random_hsla_color() -> str:
    """Return a random hsla() color string."""
    h = random.randint(0, 359)
    s = random.randint(20, 100)
    l = random.randint(15, 85)
    a = round(random.uniform(0.2, 1.0), 2)
    return f'hsla({h}, {s}%, {l}%, {a})'


def pick_different_named_color(current: str) -> str:
    """Pick a random named color that is neither identical nor a visual
    synonym of `current`."""
    current_l = current.lower()
    synonym = NAMED_COLOR_SYNONYMS.get(current_l)
    choices = [
        c for c in NAMED_COLORS
        if c.lower() != current_l and c.lower() != synonym
    ]
    return random.choice(choices)


def scale_dimension(value: float, unit: str) -> str:
    """Scale a dimension by a random multiplier from DIM_MULTIPLIERS."""
    factor = random.choice(DIM_MULTIPLIERS)
    new_val = value * factor
    # Preserve integer formatting when possible
    if abs(new_val - round(new_val)) < 1e-9:
        return f'{int(round(new_val))}{unit}'
    return f'{new_val:g}{unit}'


def perturb_opacity_value(current: str) -> str:
    """Remap an opacity float to a value that differs from the original by
    at least 0.2 (clamped to [0.1, 1.0])."""
    try:
        cur = float(current)
    except ValueError:
        cur = 1.0
    for _ in range(10):
        candidate = round(random.uniform(0.1, 1.0), 2)
        if abs(candidate - cur) >= 0.2:
            return f'{candidate:g}'
    # Fallback: complement
    complement = round(max(0.1, min(1.0, 1.0 - cur)), 2)
    return f'{complement:g}'


def pick_different_font_family(current: str) -> str:
    """Pick a replacement font-family value that differs from the current
    one. Comparison is done on the first identifier of each stack so that
    'Arial, Helvetica' and 'Arial, sans-serif' are treated as the same."""
    def head(s: str) -> str:
        s = s.strip().lower()
        first = s.split(',')[0].strip().strip("'\"")
        return first

    cur_head = head(current)
    choices = [f for f in FONT_FAMILY_POOL if head(f) != cur_head]
    return random.choice(choices) if choices else random.choice(FONT_FAMILY_POOL)


def perturb_font_weight(current: str) -> str:
    """Swap a font-weight token via FONT_WEIGHT_SWAP; fall back to 'bold'."""
    return FONT_WEIGHT_SWAP.get(current.lower(), 'bold')


def perturb_display_keyword(current: str) -> str:
    return DISPLAY_SWAP.get(current.lower(), 'block')


def perturb_flex_direction(current: str) -> str:
    return FLEX_DIRECTION_SWAP.get(current.lower(), 'column')


def perturb_justify_content(current: str) -> str:
    return JUSTIFY_CONTENT_SWAP.get(current.lower(), 'flex-end')


def perturb_align_items(current: str) -> str:
    return ALIGN_ITEMS_SWAP.get(current.lower(), 'flex-end')


# =============================================================================
# CSS-text perturbation
# =============================================================================

class PerturbStats:
    """Mutable counters threaded through the regex callbacks so we can tell
    which entries actually received a meaningful perturbation."""
    __slots__ = (
        # 8 inherited from multi_axis
        'colors', 'dimensions', 'opacity', 'fonts', 'font_weights',
        'transforms', 'display', 'img_dims',
        # 8 new in full_coverage
        'text_style', 'position', 'overflow', 'border_style',
        'filter', 'background', 'unitless_number', 'table_attrs',
    )

    def __init__(self) -> None:
        for slot in self.__slots__:
            setattr(self, slot, 0)

    def any_nonzero(self) -> bool:
        return any(getattr(self, s) for s in self.__slots__)

    def as_label(self) -> str:
        return (
            f"semantic_c{self.colors}_d{self.dimensions}_o{self.opacity}"
            f"_f{self.fonts}_fw{self.font_weights}_t{self.transforms}"
            f"_dp{self.display}_img{self.img_dims}"
            f"_ts{self.text_style}_pos{self.position}_ov{self.overflow}"
            f"_bs{self.border_style}_fl{self.filter}_bg{self.background}"
            f"_un{self.unitless_number}_tab{self.table_attrs}"
        )


def _make_keyword_sub(
    swap: Dict[str, str],
    stats_attr: str,
    perturb_rate: float,
    stats: PerturbStats,
):
    """Return a re.sub callback that swaps a property's keyword value via
    `swap` and increments `stats.<stats_attr>` on hit. The regex MUST have
    group 1 as the `prop:` prefix and group 2 as the keyword value."""
    def cb(m: 're.Match[str]') -> str:
        if random.random() < perturb_rate:
            new = swap.get(m.group(2).lower())
            if new is not None and new.lower() != m.group(2).lower():
                setattr(stats, stats_attr, getattr(stats, stats_attr) + 1)
                return m.group(1) + new
        return m.group(0)
    return cb


def _perturb_filter_block(block: str, perturb_rate: float, stats: PerturbStats) -> str:
    """Perturb individual blur/brightness/contrast/… function calls inside a
    CSS `filter` value block. Analogous to _perturb_transform_block."""
    def sub_fn(m: 're.Match[str]') -> str:
        name = m.group(1)
        args = m.group(2).strip()
        name_l = name.lower()
        if random.random() >= perturb_rate:
            return m.group(0)
        stats.filter += 1

        if name_l == 'blur':
            # args: '5px' / '0.5rem'
            num_m = re.match(r'(-?\d+(?:\.\d+)?)\s*(px|em|rem)?', args, re.IGNORECASE)
            if num_m:
                val = float(num_m.group(1))
                unit = num_m.group(2) or 'px'
                return f'{name}({scale_dimension(val, unit)})'
            return m.group(0)

        if name_l == 'hue-rotate':
            num_m = re.match(r'(-?\d+(?:\.\d+)?)\s*(deg|rad|turn|grad)?', args, re.IGNORECASE)
            if num_m:
                val = float(num_m.group(1))
                unit = num_m.group(2) or 'deg'
                offset = random.choice([45, 90, 135, 180, -45, -90])
                new_val = val + offset
                if abs(new_val - round(new_val)) < 1e-9:
                    return f'{name}({int(round(new_val))}{unit})'
                return f'{name}({new_val:g}{unit})'
            return m.group(0)

        # brightness / contrast / grayscale / invert / saturate / sepia / opacity
        # Args are either 'X%' or a unitless fraction.
        num_m = re.match(r'(-?\d+(?:\.\d+)?)\s*(%)?', args, re.IGNORECASE)
        if not num_m:
            return m.group(0)
        is_percent = num_m.group(2) == '%'
        if is_percent:
            new_val = random.randint(0, 200)
            return f'{name}({new_val}%)'
        val = float(num_m.group(1))
        factor = random.choice([0.3, 0.5, 1.8, 2.5])
        new_val = val * factor
        if abs(new_val - round(new_val)) < 1e-9:
            return f'{name}({int(round(new_val))})'
        return f'{name}({new_val:g})'

    return FILTER_FN_RE.sub(sub_fn, block)


def _perturb_transform_block(block: str, perturb_rate: float, stats: PerturbStats) -> str:
    """Perturb individual rotate/scale/translate/skew function calls inside a
    transform: value block. Each call is rolled independently."""

    def sub_fn(m: 're.Match[str]') -> str:
        name = m.group(1)
        args = m.group(2).strip()
        name_l = name.lower()
        if random.random() >= perturb_rate:
            return m.group(0)
        stats.transforms += 1

        if name_l.startswith('rotate') or name_l.startswith('skew'):
            # Args look like '45deg' or '0.5turn'. Add a large offset.
            num_m = re.match(r'(-?\d+(?:\.\d+)?)\s*(deg|rad|turn|grad)?', args, re.IGNORECASE)
            if num_m:
                val = float(num_m.group(1))
                unit = num_m.group(2) or 'deg'
                offset = random.choice([45, 90, 135, 180, -45, -90])
                new_val = val + offset
                if abs(new_val - round(new_val)) < 1e-9:
                    new_val_s = f'{int(round(new_val))}'
                else:
                    new_val_s = f'{new_val:g}'
                return f'{name}({new_val_s}{unit})'
            return m.group(0)

        if name_l.startswith('scale'):
            parts = [p.strip() for p in args.split(',')]
            new_parts = []
            for p in parts:
                try:
                    v = float(p)
                except ValueError:
                    new_parts.append(p)
                    continue
                factor = random.choice([0.3, 0.5, 1.8, 2.5, 3.0])
                nv = v * factor
                if abs(nv - round(nv)) < 1e-9:
                    new_parts.append(f'{int(round(nv))}')
                else:
                    new_parts.append(f'{nv:g}')
            return f'{name}({", ".join(new_parts)})'

        if name_l.startswith('translate'):
            # Args are dimension values (px/em/%) — re-use scale_dimension.
            def sub_dim_local(dm: 're.Match[str]') -> str:
                value = float(dm.group(1))
                unit = dm.group(2)
                return scale_dimension(value, unit)
            new_args = DIMENSION_RE.sub(sub_dim_local, args)
            return f'{name}({new_args})'

        return m.group(0)

    return TRANSFORM_FN_RE.sub(sub_fn, block)


def perturb_css_text(
    css: str,
    perturb_rate: float,
    stats: PerturbStats,
    flags: CategoryFlags,
) -> str:
    """Apply all enabled perturbations to a CSS text blob."""

    # --- colors: hex, rgb/rgba, hsl/hsla, named ------------------------------
    if flags.colors:
        def sub_hex(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return random_hex_color()
            return m.group(0)

        def sub_rgb(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return random_rgba_color() if 'rgba' in m.group(0).lower() else random_rgb_color()
            return m.group(0)

        def sub_hsl(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return random_hsla_color() if 'hsla' in m.group(0).lower() else random_hsl_color()
            return m.group(0)

        def sub_named(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.colors += 1
                return pick_different_named_color(m.group(0))
            return m.group(0)

        # Order matters: hex first (digits), then rgb, then hsl, then named.
        css = HEX_COLOR_RE.sub(sub_hex, css)
        css = RGB_COLOR_RE.sub(sub_rgb, css)
        css = HSL_COLOR_RE.sub(sub_hsl, css)
        css = NAMED_COLOR_RE.sub(sub_named, css)

    # --- opacity / fill-opacity / stroke-opacity -----------------------------
    if flags.opacity:
        def sub_opacity(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.opacity += 1
                return m.group(1) + perturb_opacity_value(m.group(2))
            return m.group(0)

        css = OPACITY_RE.sub(sub_opacity, css)

    # --- font-family ---------------------------------------------------------
    if flags.fonts:
        def sub_font_family(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.fonts += 1
                return m.group(1) + pick_different_font_family(m.group(2))
            return m.group(0)

        css = FONT_FAMILY_RE.sub(sub_font_family, css)

    # --- font-weight ---------------------------------------------------------
    if flags.font_weight:
        def sub_font_weight(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.font_weights += 1
                return m.group(1) + perturb_font_weight(m.group(2))
            return m.group(0)

        css = FONT_WEIGHT_RE.sub(sub_font_weight, css)

    # --- transform functions (rotate/scale/translate/skew) ------------------
    if flags.transform:
        def sub_transform(m: 're.Match[str]') -> str:
            prefix = m.group(1)
            block = m.group(2)
            return prefix + _perturb_transform_block(block, perturb_rate, stats)

        css = TRANSFORM_RE.sub(sub_transform, css)

    # --- display / flex-direction / justify-content / align-items ------------
    if flags.display:
        def sub_display(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_display_keyword(m.group(2))
            return m.group(0)

        def sub_flex_dir(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_flex_direction(m.group(2))
            return m.group(0)

        def sub_justify(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_justify_content(m.group(2))
            return m.group(0)

        def sub_align(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.display += 1
                return m.group(1) + perturb_align_items(m.group(2))
            return m.group(0)

        css = DISPLAY_RE.sub(sub_display, css)
        css = FLEX_DIRECTION_RE.sub(sub_flex_dir, css)
        css = JUSTIFY_CONTENT_RE.sub(sub_justify, css)
        css = ALIGN_ITEMS_RE.sub(sub_align, css)

    # --- text_style: text-align / text-decoration / text-transform / font-style
    if flags.text_style:
        css = TEXT_ALIGN_RE.sub(
            _make_keyword_sub(TEXT_ALIGN_SWAP, 'text_style', perturb_rate, stats), css)
        css = TEXT_DECORATION_RE.sub(
            _make_keyword_sub(TEXT_DECORATION_SWAP, 'text_style', perturb_rate, stats), css)
        css = TEXT_TRANSFORM_RE.sub(
            _make_keyword_sub(TEXT_TRANSFORM_SWAP, 'text_style', perturb_rate, stats), css)
        css = FONT_STYLE_RE.sub(
            _make_keyword_sub(FONT_STYLE_SWAP, 'text_style', perturb_rate, stats), css)

    # --- position: position / float / clear ---------------------------------
    if flags.position:
        css = POSITION_RE.sub(
            _make_keyword_sub(POSITION_SWAP, 'position', perturb_rate, stats), css)
        css = FLOAT_RE.sub(
            _make_keyword_sub(FLOAT_SWAP, 'position', perturb_rate, stats), css)
        css = CLEAR_RE.sub(
            _make_keyword_sub(CLEAR_SWAP, 'position', perturb_rate, stats), css)

    # --- overflow: overflow / visibility / white-space / box-sizing ----------
    if flags.overflow:
        css = OVERFLOW_RE.sub(
            _make_keyword_sub(OVERFLOW_SWAP, 'overflow', perturb_rate, stats), css)
        css = VISIBILITY_RE.sub(
            _make_keyword_sub(VISIBILITY_SWAP, 'overflow', perturb_rate, stats), css)
        css = WHITESPACE_RE.sub(
            _make_keyword_sub(WHITESPACE_SWAP, 'overflow', perturb_rate, stats), css)
        css = BOX_SIZING_RE.sub(
            _make_keyword_sub(BOX_SIZING_SWAP, 'overflow', perturb_rate, stats), css)

    # --- border_style: border/outline-style / list-style-type / cursor ------
    if flags.border_style:
        css = BORDER_STYLE_RE.sub(
            _make_keyword_sub(BORDER_STYLE_SWAP, 'border_style', perturb_rate, stats), css)
        css = LIST_STYLE_TYPE_RE.sub(
            _make_keyword_sub(LIST_STYLE_TYPE_SWAP, 'border_style', perturb_rate, stats), css)
        css = CURSOR_RE.sub(
            _make_keyword_sub(CURSOR_SWAP, 'border_style', perturb_rate, stats), css)

    # --- filter function block + mix-blend-mode ------------------------------
    # Filter runs BEFORE dimensions so blur(Xpx) / hue-rotate(Xdeg) get their
    # function-scoped handling first, same invariant as transform.
    if flags.filter:
        def sub_filter(m: 're.Match[str]') -> str:
            return m.group(1) + _perturb_filter_block(m.group(2), perturb_rate, stats)
        css = FILTER_RE.sub(sub_filter, css)
        css = MIX_BLEND_MODE_RE.sub(
            _make_keyword_sub(MIX_BLEND_MODE_SWAP, 'filter', perturb_rate, stats), css)

    # --- background-position / background-size / background-repeat ----------
    if flags.background:
        css = BG_POSITION_RE.sub(
            _make_keyword_sub(BG_POSITION_SWAP, 'background', perturb_rate, stats), css)
        css = BG_SIZE_RE.sub(
            _make_keyword_sub(BG_SIZE_SWAP, 'background', perturb_rate, stats), css)
        css = BG_REPEAT_RE.sub(
            _make_keyword_sub(BG_REPEAT_SWAP, 'background', perturb_rate, stats), css)

    # --- unitless_number: z-index / order / flex-grow / line-height / … ----
    if flags.unitless_number:
        def sub_unitless(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                try:
                    value = float(m.group(2))
                except ValueError:
                    return m.group(0)
                stats.unitless_number += 1
                # Reuse the dimension multipliers but without a unit.
                new_val = scale_dimension(value, '')
                return m.group(1) + new_val
            return m.group(0)
        css = UNITLESS_NUM_RE.sub(sub_unitless, css)

    # --- dimensions (length + deg units) -------------------------------------
    # Dimensions are perturbed LAST so that transform blocks have their own
    # function-scoped handling applied first (otherwise the generic dimension
    # regex would rescale the inside of rotate()/translate() twice).
    if flags.dimensions:
        def sub_dim(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.dimensions += 1
                value = float(m.group(1))
                unit = m.group(2)
                return scale_dimension(value, unit)
            return m.group(0)

        css = DIMENSION_RE.sub(sub_dim, css)

    return css


# =============================================================================
# HTML scoping: <style> blocks, style="..." attrs, and (for img_dims) the
# whole HTML.
# =============================================================================

STYLE_BLOCK_RE = re.compile(r'(<style\b[^>]*>)(.*?)(</style>)', re.DOTALL | re.IGNORECASE)
STYLE_ATTR_RE = re.compile(r'style\s*=\s*"([^"]*)"', re.IGNORECASE)
STYLE_ATTR_SINGLE_RE = re.compile(r"style\s*=\s*'([^']*)'", re.IGNORECASE)


def perturb_html(
    html: str,
    perturb_rate: float,
    flags: CategoryFlags,
) -> Tuple[str, PerturbStats]:
    """Apply all enabled perturbations to an HTML document. CSS-scoped
    categories are routed through perturb_css_text; img_dims is applied to
    the entire document."""
    stats = PerturbStats()

    def sub_style_block(m: 're.Match[str]') -> str:
        return m.group(1) + perturb_css_text(m.group(2), perturb_rate, stats, flags) + m.group(3)

    def sub_style_attr_double(m: 're.Match[str]') -> str:
        return 'style="' + perturb_css_text(m.group(1), perturb_rate, stats, flags) + '"'

    def sub_style_attr_single(m: 're.Match[str]') -> str:
        return "style='" + perturb_css_text(m.group(1), perturb_rate, stats, flags) + "'"

    html = STYLE_BLOCK_RE.sub(sub_style_block, html)
    html = STYLE_ATTR_RE.sub(sub_style_attr_double, html)
    html = STYLE_ATTR_SINGLE_RE.sub(sub_style_attr_single, html)

    # <img width="..." height="..."> — operates on the whole document.
    if flags.img_dims:
        def sub_img_dim(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.img_dims += 1
                try:
                    value = float(m.group(2))
                except ValueError:
                    return m.group(0)
                new_val = scale_dimension(value, '')
                return m.group(1) + new_val + m.group(3)
            return m.group(0)

        html = IMG_DIM_ATTR_RE.sub(sub_img_dim, html)

    # <table border/cellpadding/cellspacing> + <td|th colspan/rowspan> — same
    # out-of-CSS pattern as img_dims. Each integer attribute is perturbed
    # independently via DIM_MULTIPLIERS, with a minimum of 1 so we never
    # produce zero-spans that would break table layout altogether.
    if flags.table_attrs:
        def sub_int_attr(m: 're.Match[str]') -> str:
            if random.random() < perturb_rate:
                stats.table_attrs += 1
                try:
                    value = float(m.group(2))
                except ValueError:
                    return m.group(0)
                new_val_s = scale_dimension(value, '')
                # colspan/rowspan must be ≥ 1; floor to 1 if scale produced 0.
                try:
                    if int(float(new_val_s)) < 1:
                        new_val_s = '1'
                except ValueError:
                    new_val_s = '1'
                return m.group(1) + new_val_s + m.group(3)
            return m.group(0)

        html = TABLE_BORDER_RE.sub(sub_int_attr, html)
        html = TABLE_CELLPADDING_RE.sub(sub_int_attr, html)
        html = TABLE_CELLSPACING_RE.sub(sub_int_attr, html)
        html = COLSPAN_RE.sub(sub_int_attr, html)
        html = ROWSPAN_RE.sub(sub_int_attr, html)

    return html, stats


# =============================================================================
# ms_swift entry processing
# =============================================================================

def process_entry(
    entry: Dict[str, Any],
    perturb_rate: float,
    flags: CategoryFlags,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional['PerturbStats']]:
    """Process a single ms_swift entry → DPO entry with multi-axis rejection."""
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

    rejected, stats = perturb_html(assistant_msg, perturb_rate, flags)

    # Skip entries where nothing could be perturbed along any enabled axis.
    if not stats.any_nonzero():
        return None, "no_perturbable_content", None

    # Skip pathological no-op (identical output). Very rare but worth catching.
    if rejected == assistant_msg:
        return None, "perturbation_no_op", None

    dpo_entry = {
        "query": user_msg,
        "response": assistant_msg,
        "rejected_response": rejected,
        "images": entry.get("images", []),
    }
    return dpo_entry, None, stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transform ms_swift JSONL dataset to DPO format with full-coverage (16-axis) semantic rejected samples"
    )
    parser.add_argument("--input", "-i", required=True, help="Input JSONL file path (ms_swift format)")
    parser.add_argument("--output", "-o", required=True, help="Output JSONL file path (DPO format)")
    parser.add_argument("--limit", "-l", type=int, default=None, help="Limit number of entries (default: all)")
    parser.add_argument("--seed", "-s", type=int, default=42, help="Random seed (default: 42)")
    parser.add_argument(
        "--perturb-rate",
        type=float,
        default=0.7,
        help="Per-value probability of perturbation [0.0, 1.0]. Higher = more aggressive (default: 0.7)",
    )
    parser.add_argument(
        "--categories",
        "-c",
        type=str,
        default="all",
        help=(
            "Comma-separated list of perturbation categories to enable. "
            "Valid: " + ",".join(ALL_CATEGORIES) + ". "
            "Default: 'all' (every category enabled)."
        ),
    )

    args = parser.parse_args()

    if not (0.0 < args.perturb_rate <= 1.0):
        print(f"Error: --perturb-rate must be in (0.0, 1.0], got {args.perturb_rate}", file=sys.stderr)
        sys.exit(1)

    try:
        flags = CategoryFlags.from_csv(args.categories)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.input).exists():
        print(f"Error: Input file '{args.input}' does not exist", file=sys.stderr)
        sys.exit(1)

    random.seed(args.seed)

    total = 0
    written = 0
    skipped = 0
    totals: Dict[str, int] = {name: 0 for name in ALL_CATEGORIES}
    # 'dimensions' in PerturbStats is the slot name; categories list also has it.
    stat_name_to_slot = {
        # 8 inherited from multi_axis
        'colors': 'colors',
        'dimensions': 'dimensions',
        'opacity': 'opacity',
        'fonts': 'fonts',
        'font_weight': 'font_weights',
        'transform': 'transforms',
        'display': 'display',
        'img_dims': 'img_dims',
        # 8 new in full_coverage
        'text_style': 'text_style',
        'position': 'position',
        'overflow': 'overflow',
        'border_style': 'border_style',
        'filter': 'filter',
        'background': 'background',
        'unitless_number': 'unitless_number',
        'table_attrs': 'table_attrs',
    }
    skip_reasons: Dict[str, int] = {}

    enabled_names = [c for c in ALL_CATEGORIES if getattr(flags, c)]

    print(f"Loading input file: {args.input}")
    print(f"Output file: {args.output}")
    if args.limit:
        print(f"Limit: {args.limit} entries")
    print(f"Seed: {args.seed}")
    print(f"Perturb rate: {args.perturb_rate}")
    print(f"Enabled categories ({len(enabled_names)}/{len(ALL_CATEGORIES)}): {','.join(enabled_names)}")
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
                skip_reasons["invalid_json"] = skip_reasons.get("invalid_json", 0) + 1
                continue

            dpo_entry, skip_reason, stats = process_entry(entry, args.perturb_rate, flags)

            if skip_reason:
                skipped += 1
                skip_reasons[skip_reason] = skip_reasons.get(skip_reason, 0) + 1
            else:
                fout.write(json.dumps(dpo_entry, ensure_ascii=False) + "\n")
                written += 1
                for cat_name, slot in stat_name_to_slot.items():
                    totals[cat_name] += getattr(stats, slot)

            if total % 1000 == 0:
                print(f"  Processed {total} lines, written {written} entries...")

    print("\n" + "=" * 60)
    print("TRANSFORMATION STATISTICS (SEMANTIC FULL-COVERAGE — 16 AXES)")
    print("=" * 60)
    print(f"Total lines processed: {total}")
    print(f"Successfully transformed: {written}")
    print(f"Skipped: {skipped}")

    if skip_reasons:
        print("\nSkip reasons:")
        for reason, count in sorted(skip_reasons.items()):
            print(f"  {reason}: {count}")

    if written > 0:
        print(f"\nPerturbation totals (per enabled category):")
        header = f"  {'category':<14} {'total':>10} {'avg/entry':>12}"
        print(header)
        print(f"  {'-' * 12:<14} {'-' * 8:>10} {'-' * 10:>12}")
        for cat in ALL_CATEGORIES:
            if not getattr(flags, cat):
                continue
            t = totals[cat]
            avg = t / written
            print(f"  {cat:<14} {t:>10} {avg:>12.2f}")

    print("\n" + "=" * 50)
    print(f"Output file: {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
