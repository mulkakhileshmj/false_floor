#!/usr/bin/env python3
"""Structural checks on false_floor/data/public.jsonl.

Pure standard library, so it runs before anything is installed. The pairing and
the answer-position balance are the two things most likely to silently break the
experiment, so both are hard failures rather than warnings.

    python scripts/validate_dataset.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from false_floor.permutation import (  # noqa: E402
    ItemFormatError,
    permute_item,
    render_item,
    split_item,
)

DEFAULT_DATA = REPO_ROOT / "false_floor" / "data" / "public.jsonl"

REQUIRED_FIELDS = (
    "id",
    "twin_id",
    "split",
    "domain",
    "input",
    "target",
    "choices",
    "skill",
    "notes",
)

LETTERS = ("A", "B", "C", "D")
SPLITS = ("benign", "safety_proxy")
DOMAINS = ("science", "cyber_concept")

EXPECTED_ITEMS = 40
EXPECTED_PAIRS = 20
EXPECTED_PAIRS_PER_DOMAIN = 10

# Each letter is correct 10 times in a balanced 40-item set. Allow a little
# slack for future edits, but not enough for a position-biased model to fake a
# split difference.
LETTER_TOTAL_MIN, LETTER_TOTAL_MAX = 7, 13
# No letter may be near-absent from any domain x split cell of 10 items.
LETTER_CELL_MIN = 2


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def load(path: Path, report: Report) -> list[dict]:
    items: list[dict] = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                report.error(f"line {line_no}: invalid JSON ({exc.msg})")
    return items


def check_fields(items: list[dict], report: Report) -> None:
    seen_ids: set[str] = set()
    for index, item in enumerate(items, start=1):
        label = item.get("id", f"line {index}")

        missing = [field for field in REQUIRED_FIELDS if field not in item]
        if missing:
            report.error(f"{label}: missing fields {missing}")
            continue

        empty = [
            field
            for field in REQUIRED_FIELDS
            if isinstance(item[field], str) and not item[field].strip()
        ]
        if empty:
            report.error(f"{label}: empty fields {empty}")

        if item["id"] in seen_ids:
            report.error(f"{label}: duplicate id")
        seen_ids.add(item["id"])

        if item["split"] not in SPLITS:
            report.error(f"{label}: split {item['split']!r} not in {list(SPLITS)}")
        if item["domain"] not in DOMAINS:
            report.error(f"{label}: domain {item['domain']!r} not in {list(DOMAINS)}")
        if item["target"] not in LETTERS:
            report.error(f"{label}: target {item['target']!r} not in {list(LETTERS)}")
        if item["choices"] != list(LETTERS):
            report.error(f"{label}: choices must be exactly {list(LETTERS)}")

        expected_id = f"{item.get('twin_id')}_{item.get('split')}"
        if item["id"] != expected_id:
            report.warn(f"{label}: id does not follow <twin_id>_<split> ({expected_id})")

        # Every option label must actually appear in the stem, otherwise the
        # target letter points at nothing.
        for letter in LETTERS:
            if f"{letter})" not in item["input"]:
                report.error(f"{label}: input has no option labelled {letter})")

        # The item must survive option rotation, which the position control
        # depends on. An item whose options cannot be parsed out silently drops
        # out of every permuted run.
        try:
            stem, options = split_item(item["input"])
            if render_item(stem, options).strip() != item["input"].strip():
                report.error(f"{label}: does not round-trip through the option parser")
            for shift in range(1, 4):
                rotated = permute_item(item, shift)
                _, rotated_options = split_item(rotated["input"])
                original_correct = options[LETTERS.index(item["target"])]
                if rotated_options[LETTERS.index(rotated["target"])] != original_correct:
                    report.error(f"{label}: target does not follow content at shift {shift}")
        except ItemFormatError as exc:
            report.error(f"{label}: cannot be rotated ({exc})")


def check_pairs(items: list[dict], report: Report) -> None:
    pairs: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        pairs[item.get("twin_id", "?")].append(item)

    if len(pairs) != EXPECTED_PAIRS:
        report.error(f"expected {EXPECTED_PAIRS} twin_ids, found {len(pairs)}")

    for twin_id, members in sorted(pairs.items()):
        if len(members) != 2:
            report.error(f"{twin_id}: {len(members)} members, expected exactly 2")
            continue

        splits = sorted(member["split"] for member in members)
        if splits != sorted(SPLITS):
            report.error(f"{twin_id}: splits are {splits}, expected one of each")

        skills = {member.get("skill") for member in members}
        if len(skills) != 1:
            report.error(f"{twin_id}: members disagree on skill {sorted(skills)}")

        domains = {member.get("domain") for member in members}
        if len(domains) != 1:
            report.error(f"{twin_id}: members disagree on domain {sorted(domains)}")

        bands = [_difficulty_band(member) for member in members]
        if bands[0] is None or bands[1] is None:
            report.warn(f"{twin_id}: notes do not record a difficulty band")
        elif bands[0] != bands[1]:
            report.error(f"{twin_id}: difficulty bands differ, {bands[0]} vs {bands[1]}")

        # A large length gap is a hint that the pair is not matched on
        # linguistic complexity. Advisory: some safety-proxy stems legitimately
        # carry a sentence of scenario.
        lengths = sorted(len(member["input"]) for member in members)
        if lengths[1] > 1.8 * lengths[0]:
            report.warn(
                f"{twin_id}: input lengths differ a lot ({lengths[0]} vs {lengths[1]} chars)"
            )

    domain_pairs = Counter(
        members[0]["domain"] for members in pairs.values() if members and "domain" in members[0]
    )
    for domain in DOMAINS:
        count = domain_pairs.get(domain, 0)
        if count != EXPECTED_PAIRS_PER_DOMAIN:
            report.error(
                f"domain {domain}: {count} pairs, expected {EXPECTED_PAIRS_PER_DOMAIN}"
            )


def _difficulty_band(item: dict) -> str | None:
    notes = str(item.get("notes", "")).lower()
    marker = "difficulty band:"
    if marker not in notes:
        return None
    return notes.split(marker, 1)[1].split(".")[0].strip()


def check_balance(items: list[dict], report: Report) -> None:
    overall = Counter(item.get("target") for item in items)
    for letter in LETTERS:
        count = overall.get(letter, 0)
        if not LETTER_TOTAL_MIN <= count <= LETTER_TOTAL_MAX:
            report.error(
                f"answer balance: {letter} is correct {count} times, "
                f"expected {LETTER_TOTAL_MIN}-{LETTER_TOTAL_MAX} of {len(items)}"
            )

    cells: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for item in items:
        cells[(item.get("domain"), item.get("split"))][item.get("target")] += 1

    for (domain, split), counter in sorted(cells.items(), key=lambda kv: str(kv[0])):
        for letter in LETTERS:
            if counter.get(letter, 0) < LETTER_CELL_MIN:
                report.error(
                    f"answer balance: {domain}/{split} has {counter.get(letter, 0)} "
                    f"{letter} answers, expected at least {LETTER_CELL_MIN}"
                )


def summarise(items: list[dict]) -> str:
    lines = ["", f"{len(items)} items"]
    split_counts = Counter(item.get("split") for item in items)
    domain_counts = Counter(item.get("domain") for item in items)
    lines.append("  splits:  " + ", ".join(f"{k}={v}" for k, v in sorted(split_counts.items())))
    lines.append("  domains: " + ", ".join(f"{k}={v}" for k, v in sorted(domain_counts.items())))

    lines.append("  answer key by cell:")
    cells: dict[tuple[str, str], Counter] = defaultdict(Counter)
    for item in items:
        cells[(item.get("domain"), item.get("split"))][item.get("target")] += 1
    for cell, counter in sorted(cells.items(), key=lambda kv: str(kv[0])):
        detail = " ".join(f"{letter}={counter.get(letter, 0)}" for letter in LETTERS)
        lines.append(f"    {cell[0]:<14} {cell[1]:<13} {detail}")
    overall = Counter(item.get("target") for item in items)
    detail = " ".join(f"{letter}={overall.get(letter, 0)}" for letter in LETTERS)
    lines.append(f"    {'ALL':<14} {'':<13} {detail}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_DATA)
    parser.add_argument(
        "--quiet", action="store_true", help="only print problems and the verdict"
    )
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if not args.path.exists():
        print(f"FAIL: no dataset at {args.path}")
        return 1

    report = Report()
    items = load(args.path, report)

    if len(items) != EXPECTED_ITEMS:
        report.error(f"expected {EXPECTED_ITEMS} items, found {len(items)}")

    check_fields(items, report)
    check_pairs(items, report)
    check_balance(items, report)

    if not args.quiet:
        print(f"validating {args.path}")
        print(summarise(items))
        print()

    for warning in report.warnings:
        print(f"warn:  {warning}")
    for error in report.errors:
        print(f"ERROR: {error}")

    if report.errors:
        print(f"\nFAIL: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")
        return 1

    print(f"OK: dataset valid ({len(report.warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
