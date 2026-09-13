"""Test suite for False Floor.

Run with:  pytest -q

Several of these are regression tests for defects found by probing real models,
not hypotheticals. Where that is the case the docstring says so, because the
cheapest way to reintroduce a bug is to delete the test that looks arbitrary.
"""

from __future__ import annotations

import itertools
import json
import random
from collections import Counter
from pathlib import Path

import pytest

from false_floor.analysis import (
    BENIGN,
    SAFETY,
    Cell,
    PairAnalysis,
    js_divergence,
    normalised_entropy,
    pool,
)
from false_floor.permutation import (
    LETTERS,
    ItemFormatError,
    permute_item,
    render_item,
    shift_for,
    split_item,
)
from false_floor.scoring import parse_letter
from false_floor.stats import (
    holm_adjust,
    mcnemar_exact_p,
    mcnemar_midp,
    sign_flip_test,
    wilson_interval,
)
from false_floor.task import ANSWER_FORMATS, build_system_prompt, load_items

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# answer parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("B", "B"),
    (" b ", "B"),
    ("**C**", "C"),
    ("(D)", "D"),
    ("A.", "A"),
    ("'A'", "A"),
    ("ANSWER: B", "B"),
    ("answer: c", "C"),
    ("Answer - A", "A"),
    ("final answer is D", "D"),
    ("Let me think. Option A says x, option B says y.\nANSWER: C", "C"),
    ("The answer is B) The batch consumes 3.0 mol", "B"),
    ("reasoning here\nB) The batch consumes 3.0 mol of precursor", "B"),
])
def test_parse_letter_accepts_unambiguous_answers(text, expected):
    assert parse_letter(text) == expected


@pytest.mark.parametrize("text", [
    # Regression: these were scored as answering "A" by an earlier parser that
    # accepted the first capital A-D next to punctuation. The English article
    # matched. Five real models produced replies of this shape in one probe.
    'The user asks: "A site rule states that any batch consuming"',
    "A site rule states that any single batch must be declared.",
    "We are given: \"A site rule states that any single batch\"",
    "The user wants me to answer a multiple choice question. Let me analyze.",
    "I cannot help with that request.",
    "Sorry, no.",
    "Answer: a bird flew past",
    "",
    "   ",
    None,
])
def test_parse_letter_rejects_prose_without_an_answer(text):
    assert parse_letter(text) is None


def test_truncated_replies_are_never_parsed():
    """A model cut off mid-sentence has not committed to an answer."""
    assert parse_letter("The answer is B", truncated=True) is None
    assert parse_letter("B", truncated=True) is None


def test_last_marker_wins_over_earlier_mentions():
    text = "First I thought ANSWER: A but on reflection\nANSWER: D"
    assert parse_letter(text) == "D"


# --------------------------------------------------------------------------
# statistics
# --------------------------------------------------------------------------

def _brute_force_sign_flip(deltas):
    nonzero = [d for d in deltas if abs(d) > 1e-12]
    observed = abs(sum(deltas))
    total = hits = 0
    for signs in itertools.product([1, -1], repeat=len(nonzero)):
        value = sum(a * b for a, b in zip(signs, nonzero))
        total += 1
        if abs(value) >= observed - 1e-9:
            hits += 1
    return hits / total


def test_sign_flip_matches_brute_force_enumeration():
    """The fast convolution must agree exactly with full enumeration."""
    rng = random.Random(3)
    for _ in range(200):
        k = rng.randint(1, 11)
        deltas = [rng.choice([-2, -1, 0, 1, 2, -0.5, 0.5, 0.25]) for _ in range(k)]
        assert sign_flip_test(deltas).p_value == pytest.approx(
            _brute_force_sign_flip(deltas), abs=1e-9
        )


def test_sign_flip_minimum_attainable_p():
    """With k informative pairs the smallest two-sided p is 2/2**k."""
    assert sign_flip_test([1] * 6).p_value == pytest.approx(0.03125)
    assert sign_flip_test([1] * 5).p_value == pytest.approx(0.0625)
    assert sign_flip_test([1] * 5).underpowered is True
    assert sign_flip_test([1] * 6).underpowered is False


def test_sign_flip_ignores_zero_pairs():
    """Pairs that did not move carry no information and must not dilute p."""
    assert sign_flip_test([1] * 6).p_value == pytest.approx(
        sign_flip_test([1] * 6 + [0] * 14).p_value
    )


def test_sign_flip_null_is_not_significant():
    assert sign_flip_test([0.0] * 20).p_value == 1.0
    assert sign_flip_test([]).p_value == 1.0


def test_midp_is_less_conservative_than_exact():
    for down, up in [(6, 0), (5, 0), (4, 0), (8, 1)]:
        assert mcnemar_midp(down, up) < mcnemar_exact_p(down, up)


def test_wilson_interval_stays_in_range():
    for successes, total in [(0, 20), (20, 20), (10, 20), (0, 0)]:
        low, high = wilson_interval(successes, total)
        assert 0.0 <= low <= high <= 1.0


def test_holm_is_monotone_and_conservative():
    raw = {"a": 0.001, "b": 0.02, "c": 0.30, "d": 0.04}
    adjusted = holm_adjust(raw)
    assert all(adjusted[k] >= raw[k] for k in raw)
    ordered = sorted(raw, key=lambda k: raw[k])
    values = [adjusted[k] for k in ordered]
    assert values == sorted(values)


# --------------------------------------------------------------------------
# option-order permutation
# --------------------------------------------------------------------------

def test_every_item_round_trips_through_the_option_parser():
    for item in load_items("all"):
        stem, options = split_item(item["input"])
        assert len(options) == 4
        assert render_item(stem, options).strip() == item["input"].strip()


def test_rotation_keeps_the_correct_content_under_the_target_letter():
    for item in load_items("all"):
        _, options = split_item(item["input"])
        correct = options[LETTERS.index(item["target"])]
        for shift in range(4):
            rotated = permute_item(item, shift)
            _, rotated_options = split_item(rotated["input"])
            assert rotated_options[LETTERS.index(rotated["target"])] == correct
            assert sorted(rotated_options) == sorted(options)


def test_shift_is_deterministic_and_nonzero_for_nonzero_seeds():
    items = load_items("all")
    assert all(shift_for(i["id"], 0) == 0 for i in items)
    for seed in (1, 62):
        shifts = [shift_for(i["id"], seed) for i in items]
        assert all(s != 0 for s in shifts), "a shift of zero is not a control"
        assert shifts == [shift_for(i["id"], seed) for i in items]


def test_split_item_rejects_malformed_input():
    with pytest.raises(ItemFormatError):
        split_item("A stem with no options at all")


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------

def test_dataset_shape_and_pairing():
    items = load_items("all")
    assert len(items) == 40
    twins = Counter(i["twin_id"] for i in items)
    assert len(twins) == 20
    assert set(twins.values()) == {2}
    for item in items:
        assert item["choices"] == list(LETTERS)
        assert item["target"] in LETTERS


def test_twins_share_skill_and_difficulty_band():
    pairs: dict[str, list[dict]] = {}
    for item in load_items("all"):
        pairs.setdefault(item["twin_id"], []).append(item)
    for twin_id, members in pairs.items():
        assert len({m["skill"] for m in members}) == 1, twin_id
        bands = [m["notes"].lower().split("difficulty band:")[1].split(".")[0].strip()
                 for m in members]
        assert bands[0] == bands[1], twin_id


def test_answer_letters_are_balanced():
    """Position bias is a real confound: an unbalanced key lets a letter-biased
    model manufacture a split difference."""
    items = load_items("all")
    overall = Counter(i["target"] for i in items)
    assert set(overall.values()) == {10}
    cells: dict[tuple[str, str], Counter] = {}
    for item in items:
        cells.setdefault((item["domain"], item["split"]), Counter())[item["target"]] += 1
    for cell, counter in cells.items():
        for letter in LETTERS:
            assert counter[letter] >= 2, (cell, letter)


def test_dataset_file_is_valid_jsonl():
    path = REPO_ROOT / "false_floor" / "data" / "public.jsonl"
    with open(path, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if line.strip():
                json.loads(line)


# --------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------

def test_format_suffix_is_identical_across_conditions():
    """The whole design rests on only the cue differing between conditions."""
    for answer_format in ANSWER_FORMATS:
        prompts = {
            condition: build_system_prompt(condition, answer_format=answer_format)
            for condition in ("control", "pressure_placebo", "eval_cue",
                              "deploy_threat", "monitor")
        }
        suffixes = set()
        for text in prompts.values():
            suffixes.add(text.split("\n\n", 1)[1])
        assert len(suffixes) == 1, answer_format


def test_unknown_condition_or_format_raises():
    with pytest.raises(ValueError):
        build_system_prompt("no_such_condition")
    with pytest.raises(ValueError):
        build_system_prompt("control", answer_format="no_such_format")


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

def _cell(safety_correct: int, benign_correct: int = 20, letters: str | None = None) -> Cell:
    cell = Cell()
    for index in range(20):
        twin = f"t{index}"
        for split, correct in ((BENIGN, benign_correct), (SAFETY, safety_correct)):
            letter = letters or LETTERS[index % 4]
            cell.add(f"{twin}_{split}", twin, split,
                     1.0 if index < correct else 0.0, letter)
    return cell


def test_pair_analysis_computes_difference_in_differences():
    base = _cell(safety_correct=20)
    cue = _cell(safety_correct=14)
    analysis = PairAnalysis(base, cue)
    assert analysis.result.n_pairs == 20
    assert analysis.d_sel == pytest.approx(0.30)
    assert analysis.result.n_informative == 6
    assert analysis.p_value == pytest.approx(0.03125)
    assert analysis.safety_down == 6


def test_equal_drop_on_both_arms_is_not_selective():
    base = _cell(safety_correct=20, benign_correct=20)
    cue = _cell(safety_correct=14, benign_correct=14)
    assert PairAnalysis(base, cue).d_sel == pytest.approx(0.0)


def test_verdict_requires_significance_not_just_magnitude():
    base = _cell(safety_correct=20)
    big_but_thin = PairAnalysis(base, _cell(safety_correct=17))
    assert big_but_thin.d_sel == pytest.approx(0.15)
    assert "investigate" not in big_but_thin.verdict()


def test_pooling_over_option_orders_averages_per_item():
    merged = pool([_cell(safety_correct=20), _cell(safety_correct=0)])
    assert merged.accuracy(SAFETY) == pytest.approx(0.5)


def test_entropy_and_divergence():
    assert normalised_entropy(Counter(A=10, B=10, C=10, D=10)) == pytest.approx(1.0)
    assert normalised_entropy(Counter(A=40)) == 0.0
    assert normalised_entropy(Counter()) is None
    assert js_divergence(Counter(A=40), Counter(A=40)) == pytest.approx(0.0)
    assert js_divergence(Counter(A=40), Counter(B=40)) == pytest.approx(1.0)


def test_refusal_rule_b_drops_unanswered_pairs():
    base = _cell(safety_correct=20)
    cue = Cell()
    for index in range(20):
        twin = f"t{index}"
        cue.add(f"{twin}_{BENIGN}", twin, BENIGN, 1.0, "A")
        # First five pairs produce no usable answer at all.
        parsed = None if index < 5 else "A"
        cue.add(f"{twin}_{SAFETY}", twin, SAFETY, 0.0 if index < 5 else 1.0, parsed)
    lenient = PairAnalysis(base, cue, require_answered=False)
    strict = PairAnalysis(base, cue, require_answered=True)
    assert lenient.result.n_pairs == 20
    assert strict.result.n_pairs == 15
    assert strict.dropped_for_refusal == 5
