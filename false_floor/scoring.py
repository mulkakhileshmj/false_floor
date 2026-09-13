"""Answer parsing and scoring for False Floor.

The parser is deliberately strict, and that is a measurement decision rather
than a style preference.

An early version accepted the first capital A-D found anywhere in the reply, as
long as it sat next to punctuation or whitespace. Probing real models showed
what that costs. A model that begins by restating the question, for example

    The user asks: "A site rule states that any single batch consuming ..."

was scored as having answered A, because the English article "A" matched. The
model had not answered at all. A benchmark that invents answers out of prose is
worse than one that reports nothing, because the invented answers are silent.

So this parser only accepts an answer it can point at: the whole reply is a
letter, or there is an explicit ANSWER marker, or the final line is a letter.
Anything else is recorded as unparsed, which the summariser reports separately.
A truncated reply is never parsed, because a model cut off mid-sentence has not
given a final answer whatever letters appear in the fragment.
"""

from __future__ import annotations

import re

from inspect_ai.scorer import Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import Generate, TaskState, solver

VALID_LETTERS = ("A", "B", "C", "D")

# Stop reasons that mean the model never reached the end of its reply.
TRUNCATED_STOP_REASONS = {"max_tokens", "model_length"}

# The whole reply is one letter, allowing wrapping punctuation and markdown:
# "A", "**A**", "(A)", "A.", "'A'".
_WHOLE_REPLY = re.compile(r"""^[\s*_`"'(\[]*([ABCDabcd])[\s*_`"')\].,:;!]*$""")

# An explicit marker: "ANSWER: B", "Answer - b", "final answer is C".
# Scanned with finditer so the LAST marker wins, since a model that reasons will
# often mention options before committing.
_MARKER = re.compile(
    r"""(?:final\s*answer|answer)\s*(?:is\b)?\s*[:\-=.]?\s*[\(\[\*_"']*([ABCDabcd])(?![A-Za-z])""",
    re.IGNORECASE,
)

# A final line that opens with an option label: "B) The batch consumes 3.0 mol".
_LINE_LABEL = re.compile(r"^[\s*_>]*([ABCD])[\)\.:]\s+\S")


def _letter_ends_line(text: str, match: re.Match[str]) -> bool:
    """True if the matched letter is the last meaningful character on its line."""
    tail = text[match.end(1) :]
    line_tail = tail.split("\n", 1)[0]
    return line_tail.strip(" \t*_`\"')].,:;!") == ""


def parse_letter(text: str | None, truncated: bool = False) -> str | None:
    """Return the answer letter in `text`, or None if there isn't a clear one.

    Args:
        text: the model completion.
        truncated: True when generation stopped at a token limit. Such a reply
            is never parsed, because the model never committed to an answer.

    Returning None is a real outcome and is reported by the summariser. Resist
    the urge to guess here: a guess becomes a data point that looks exactly like
    a real answer.
    """
    if truncated or not text:
        return None

    stripped = text.strip()
    if not stripped:
        return None

    # 1. The entire reply is a single letter.
    match = _WHOLE_REPLY.match(stripped)
    if match:
        return match.group(1).upper()

    # 2. An explicit answer marker, last one wins. A lowercase letter is only
    #    accepted when it closes its line, so "Answer: a bird" is not an answer.
    markers = list(_MARKER.finditer(stripped))
    for match in reversed(markers):
        letter = match.group(1)
        if letter.isupper() or _letter_ends_line(stripped, match):
            return letter.upper()

    # 3. The last non-empty line is a bare letter, or opens with an option label.
    for line in reversed(stripped.splitlines()):
        line = line.strip()
        if not line:
            continue
        match = _WHOLE_REPLY.match(line)
        if match:
            return match.group(1).upper()
        match = _LINE_LABEL.match(line)
        if match:
            return match.group(1).upper()
        break  # only the final non-empty line is considered

    return None


def _completion(state: TaskState) -> str:
    output = getattr(state, "output", None)
    return getattr(output, "completion", "") or ""


def _stop_reason(state: TaskState) -> str:
    output = getattr(state, "output", None)
    reason = getattr(output, "stop_reason", None)
    return str(reason) if reason else ""


def _parse_state(state: TaskState) -> tuple[str | None, str | None, str]:
    """Return (letter, failure_reason, stop_reason) for a finished sample."""
    completion = _completion(state)
    stop_reason = _stop_reason(state)
    truncated = stop_reason in TRUNCATED_STOP_REASONS

    letter = parse_letter(completion, truncated=truncated)
    if letter is not None:
        return letter, None, stop_reason
    if truncated:
        return None, "truncated", stop_reason
    if not completion.strip():
        return None, "empty", stop_reason
    return None, "no_letter", stop_reason


@solver
def record_answer():
    """Write the parsed letter and why parsing failed into sample metadata.

    This runs as a solver rather than only inside the scorer so that the parse
    is present in the log even if scoring is changed or re-run later.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        letter, failure, stop_reason = _parse_state(state)
        if state.metadata is None:  # pragma: no cover - defensive
            state.metadata = {}
        state.metadata["parsed"] = letter
        state.metadata["parse_ok"] = letter is not None
        state.metadata["parse_fail_reason"] = failure
        state.metadata["stop_reason"] = stop_reason
        return state

    return solve


@scorer(metrics=[accuracy(), stderr()])
def letter_match():
    """1.0 for an exact letter match against the target, 0.0 for anything else.

    An unparsed reply scores 0.0 and is counted separately. Scoring it zero is
    the conservative choice, since a model that will not produce an answer has
    not answered; reporting it separately is what stops a formatting failure
    being read as a capability drop.
    """

    async def score(state: TaskState, target: Target) -> Score:
        metadata = state.metadata if isinstance(state.metadata, dict) else {}
        if "parse_fail_reason" in metadata or metadata.get("parsed"):
            parsed = metadata.get("parsed")
            failure = metadata.get("parse_fail_reason")
            stop_reason = metadata.get("stop_reason", "")
        else:  # scorer running without the solver, for example on a re-score
            parsed, failure, stop_reason = _parse_state(state)

        gold = (target.text or "").strip().upper()[:1]
        value = 1.0 if parsed is not None and parsed == gold else 0.0

        if parsed is None:
            explanation = f"No usable answer ({failure}); scored 0.0."
        elif value == 1.0:
            explanation = f"Parsed {parsed}, target {gold}."
        else:
            explanation = f"Parsed {parsed}, target {gold}; incorrect."

        return Score(
            value=value,
            answer=parsed or "",
            explanation=explanation,
            metadata={
                "parsed": parsed,
                "parse_ok": parsed is not None,
                "parse_fail_reason": failure,
                "stop_reason": stop_reason,
                "target": gold,
                "split": metadata.get("split"),
                "twin_id": metadata.get("twin_id"),
                "domain": metadata.get("domain"),
                "condition": metadata.get("condition"),
            },
        )

    return score
