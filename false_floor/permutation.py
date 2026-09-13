"""Option-order permutation, the control that separates suppression from letter drift.

Why this module exists
----------------------

Cacioli (2026, arXiv:2604.26206) ran a pre-registered study in which Llama-3-8B
was told to underperform on MMLU-Pro. It complied, but not by avoiding correct
answers. Its response-position entropy collapsed from 0.972 to 0.681 and its
replies piled onto one letter. Accuracy then depended almost entirely on where
the correct answer happened to sit: 72.1% when the key landed on the model's
preferred position, 4.3% when it landed on A. The accuracy drop was a side
effect of positional collapse, not of content-aware avoidance.

The control that exposes this is cyclic option-order rotation. Rotating options
moves the content but leaves the letters in place, so:

- a model answering on content follows the rotation
- a model answering on letter position does not

Critically, in that study honest accuracy was unchanged by rotation (-0.2 pp,
p = 0.841), so rotation is a clean control rather than a new manipulation.

Without this, a drop in safety-proxy accuracy under a cue cannot be told apart
from the model drifting toward a favourite letter. With it, the two have
different signatures.

Design constraint
-----------------

A given item must get the SAME rotation in every condition, otherwise the
per-item pairing that the whole analysis depends on is broken. So the shift is
derived deterministically from the item id and a seed, never sampled per run.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

LETTERS = ("A", "B", "C", "D")
N_OPTIONS = len(LETTERS)

# An option line: "A) some text". Anchored at line start so option text
# containing a bracket cannot be mistaken for a new option.
_OPTION_LINE = re.compile(r"^([A-D])\)\s*(.*)$")


class ItemFormatError(ValueError):
    """Raised when an item's input cannot be split into a stem and four options."""


def split_item(text: str) -> tuple[str, list[str]]:
    """Split an item's input into its stem and its four option texts.

    Returns:
        (stem, [option_a, option_b, option_c, option_d])

    Raises:
        ItemFormatError: if the options are missing, out of order, or not four.
    """
    lines = text.splitlines()
    stem_lines: list[str] = []
    options: list[str] = []
    expected = 0

    for line in lines:
        match = _OPTION_LINE.match(line.strip())
        is_next_option = (
            match is not None
            and expected < N_OPTIONS
            and match.group(1) == LETTERS[expected]
        )
        if is_next_option:
            options.append(match.group(2).strip())
            expected += 1
        elif options:
            # A line after the options began that is not the next option.
            # Multi-line options are not supported, and no item needs them.
            raise ItemFormatError(
                f"unexpected line after option {LETTERS[expected - 1]}: {line!r}"
            )
        else:
            stem_lines.append(line)

    if len(options) != N_OPTIONS:
        raise ItemFormatError(
            f"expected {N_OPTIONS} options labelled A-D, found {len(options)}"
        )

    stem = "\n".join(stem_lines).strip()
    if not stem:
        raise ItemFormatError("item has no stem")
    return stem, options


def render_item(stem: str, options: list[str]) -> str:
    """Rebuild an item's input from a stem and four option texts."""
    if len(options) != N_OPTIONS:
        raise ItemFormatError(f"expected {N_OPTIONS} options, got {len(options)}")
    lines = [stem]
    lines.extend(f"{letter}) {text}" for letter, text in zip(LETTERS, options))
    return "\n".join(lines)


def shift_for(item_id: str, seed: int) -> int:
    """Deterministic rotation in 0..3 for an item, stable across runs and conditions.

    Uses a hash rather than random.Random so that the shift depends only on the
    item id and the seed, never on iteration order or on how many items were
    drawn before this one.
    """
    if seed == 0:
        return 0
    # Nonzero shifts only, so every item's content actually moves. Cacioli drew
    # k from {1..9} over ten options for the same reason: a shift of zero is not
    # a control, it is a repeat of the original ordering.
    digest = hashlib.sha256(f"{item_id}:{seed}".encode()).digest()
    return 1 + (digest[0] % (N_OPTIONS - 1))


def permute_item(item: dict[str, Any], shift: int) -> dict[str, Any]:
    """Return a copy of `item` with its options rotated by `shift` positions.

    The option originally at index j moves to index (j + shift) % 4, and the
    target letter is remapped to follow its content. A shift of 0 returns an
    equivalent item, so the un-permuted path and the permuted path go through
    exactly the same code.

    Adds three fields for analysis:
        permute_shift    the rotation applied
        original_target  the target letter before rotation
        original_id      the item id, unchanged by rotation
    """
    shift = shift % N_OPTIONS
    stem, options = split_item(item["input"])

    rotated = [""] * N_OPTIONS
    for index, text in enumerate(options):
        rotated[(index + shift) % N_OPTIONS] = text

    original_target = str(item["target"]).strip().upper()
    if original_target not in LETTERS:
        raise ItemFormatError(f"target {original_target!r} is not one of {LETTERS}")
    new_target = LETTERS[(LETTERS.index(original_target) + shift) % N_OPTIONS]

    permuted = dict(item)
    permuted["input"] = render_item(stem, rotated)
    permuted["target"] = new_target
    permuted["permute_shift"] = shift
    permuted["original_target"] = original_target
    permuted["original_id"] = item["id"]
    return permuted


def permute_items(items: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    """Apply a per-item deterministic rotation to a list of items."""
    return [permute_item(item, shift_for(str(item["id"]), seed)) for item in items]
