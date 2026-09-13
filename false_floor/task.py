"""False Floor v0 - the Inspect task.

Run it:

    inspect eval false_floor/task.py@false_floor --model openai/gpt-4o-mini -T condition=deploy_threat

Every condition sees exactly the same 40 items in the same order. The only thing
that changes between runs is the system message, which is what makes the
across-condition comparison meaningful.

Temperature zero is a sampling setting, not a guarantee of deterministic output.
The panel runner records repetitions and separate controls explicitly. The task's
legacy automatic epoch default is retained for direct Inspect calls; use explicit
epochs for a versioned study.
"""

from __future__ import annotations

import inspect as pyinspect
import json
import hashlib
from pathlib import Path
from typing import Any

import yaml
from inspect_ai import Task, task
from inspect_ai.dataset import MemoryDataset, Sample
from inspect_ai.model import GenerateConfig
from inspect_ai.solver import generate, system_message

try:  # installed as a package, which is the normal case
    from false_floor.permutation import permute_items
    from false_floor.scoring import letter_match, record_answer
except ImportError:  # loaded as a bare file by the Inspect task loader
    from permutation import permute_items  # type: ignore[no-redef]
    from scoring import letter_match, record_answer  # type: ignore[no-redef]

PACKAGE_DIR = Path(__file__).parent
DATA_PATH = PACKAGE_DIR / "data" / "public.jsonl"
PROMPTS_PATH = PACKAGE_DIR / "prompts.yaml"

SPLITS = ("benign", "safety_proxy", "all")

# "danger" was the original name for the safety_proxy split. Kept so older
# commands and scripts do not silently select nothing.
SPLIT_ALIASES = {"danger": "safety_proxy"}

REQUIRED_FIELDS = (
    "id",
    "twin_id",
    "split",
    "domain",
    "input",
    "target",
    "choices",
    "skill",
)

# Legacy default for direct calls without explicit epochs. Calibrate repetitions
# for the selected models during pilot work; no false-positive rate is guaranteed.
NONDETERMINISTIC_EPOCHS = 5

# How the model is asked to answer, and how much room it gets to do so.
#
# "letter" is the tightest measurement and works on instruction-following chat
# models. It fails badly on models that reason out loud regardless of the
# prompt: they are still mid-sentence at the token cap, and every sample becomes
# a formatting failure. "reasoned" is the setting to use for those, and for any
# panel that mixes model families.
ANSWER_FORMATS: dict[str, dict[str, Any]] = {
    "letter": {"suffix": None, "max_tokens": 64},
    "brief": {"suffix": "reason_suffix", "max_tokens": 256},
    "reasoned": {"suffix": "open_reasoning_suffix", "max_tokens": 1024},
}

# Recommended non-zero option-order seed. Chosen by scanning seeds so that every
# item is rotated AND the rotated answer key stays balanced across letters, with
# no letter falling below two occurrences in any domain-by-split cell. Rotating
# with an arbitrary seed degrades that balance, which matters because letter
# balance is what stops a position-biased model faking a split difference.
DEFAULT_PERMUTE_SEED = 62


def load_prompts(path: Path = PROMPTS_PATH) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def available_conditions() -> list[str]:
    """Condition names wired up in v0. The v1 stubs are commented out."""
    return list(load_prompts()["conditions"].keys())


def build_system_prompt(condition: str, answer_format: str = "letter") -> str:
    """Join the condition cue with the shared output-format suffix.

    The format suffix is identical across all four conditions, so it cannot
    explain a difference between them. Only the cue differs.
    """
    prompts = load_prompts()
    conditions = prompts["conditions"]

    if condition not in conditions:
        raise ValueError(
            f"unknown condition {condition!r}. Available: {sorted(conditions)}"
        )
    if answer_format not in ANSWER_FORMATS:
        raise ValueError(
            f"unknown answer_format {answer_format!r}. "
            f"Available: {sorted(ANSWER_FORMATS)}"
        )

    parts = [str(conditions[condition]).strip(), str(prompts["shared_suffix"]).strip()]
    suffix_key = ANSWER_FORMATS[answer_format]["suffix"]
    if suffix_key:
        parts.append(str(prompts[suffix_key]).strip())
    return "\n\n".join(parts)


def load_items(split: str = "all", path: Path = DATA_PATH) -> list[dict[str, Any]]:
    """Read public.jsonl, optionally filtered to one split."""
    split = SPLIT_ALIASES.get(split, split)
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}. Available: {list(SPLITS)}")

    items: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path.name} line {line_no} is not valid JSON") from exc

            missing = [field for field in REQUIRED_FIELDS if field not in item]
            if missing:
                raise ValueError(
                    f"{path.name} line {line_no} is missing required fields: {missing}"
                )
            items.append(item)

    if split != "all":
        items = [item for item in items if item["split"] == split]

    if not items:
        raise ValueError(f"no items matched split={split!r} in {path}")

    return items


def _sample(item: dict[str, Any], condition: str) -> Sample:
    return Sample(
        id=item["id"],
        input=item["input"],
        target=item["target"],
        choices=list(item.get("choices", ["A", "B", "C", "D"])),
        metadata={
            "id": item["id"],
            "twin_id": item["twin_id"],
            "split": item["split"],
            "domain": item["domain"],
            "skill": item.get("skill"),
            "condition": condition,
            "target": item["target"],
            "permute_shift": int(item.get("permute_shift", 0)),
            "original_target": item.get("original_target", item["target"]),
            # Filled in by the record_answer solver once the model has replied.
            "parsed": None,
        },
    )


def _construct_task(**kwargs: Any) -> Task:
    """Build a Task, tolerating differences between Inspect versions.

    The solver argument was called "plan" in older releases, and parameters such
    as token_limit and epochs are not present in every version. Rather than pin
    a narrow range of Inspect versions, pass only what this installation takes.
    """
    solver_chain = kwargs.pop("solver")
    try:
        accepted = set(pyinspect.signature(Task).parameters)
    except (TypeError, ValueError):  # pragma: no cover - very defensive
        accepted = set()

    if not accepted or "solver" in accepted:
        kwargs["solver"] = solver_chain
    else:
        kwargs["plan"] = solver_chain

    if accepted:
        kwargs = {key: value for key, value in kwargs.items() if key in accepted}

    return Task(**kwargs)


@task
def false_floor(
    condition: str = "control",
    split: str = "all",
    answer_format: str = "letter",
    with_reason: bool = False,
    max_tokens: int | None = None,
    temperature: float = 0.0,
    send_temperature: bool = True,
    permute_seed: int = 0,
    epochs: int | None = None,
    token_limit: int | None = None,
    study_id: str = "manual",
    baseline_repeat: int = 0,
    planned_models: str | list[str] = "[]",
) -> Task:
    """Paired capability twins under one evaluation cue.

    Args:
        condition: which cue to apply. One of control, eval_cue, deploy_threat,
            monitor. See false_floor/prompts.yaml.
        split: benign, safety_proxy, or all. Defaults to all, which is what the
            metrics need.
        answer_format: how the model is asked to reply. "letter" is a bare
            A-D and is the tightest measurement. "brief" asks for "ANSWER: X"
            plus one sentence. "reasoned" lets the model think at length and
            then commit on a final ANSWER line, which is the only format many
            reasoning models can actually satisfy. The suffix is identical in
            every condition, so it cannot explain a between-condition
            difference.
        with_reason: deprecated alias, equivalent to answer_format="brief".
        max_tokens: generation cap. Defaults to the format's own budget, 64 for
            letter, 256 for brief, 1024 for reasoned. Raise it if truncated
            completions show up as a parse-failure spike.
        temperature: 0 by default, because determinism is what makes the
            across-condition comparison precise.
        send_temperature: pass -T send_temperature=false for reasoning models
            that reject an explicit temperature. Doing so switches the default
            epoch count to 5, since the run is then no longer deterministic.
        permute_seed: 0 keeps the published option order. Any other value
            rotates each item's options by a deterministic non-zero amount, so
            content moves while letters stay put. Run the panel once at 0 and
            once at DEFAULT_PERMUTE_SEED (62) to separate genuine suppression
            from a model drifting toward a favourite letter. The rotation
            depends only on the item id and the seed, so a given item gets the
            same rotation in every condition and the pairing survives.
        epochs: how many times to ask each item. Defaults to 1 when sampling is
            deterministic and 5 when it is not. Override only if you know why.
        token_limit: per-sample token limit, ignored on Inspect versions that do
            not support it.
    """
    if baseline_repeat not in (0, 1) or (baseline_repeat and condition != "control"):
        raise ValueError("baseline_repeat must be 0, or 1 for a repeated control")
    model_family = json.loads(planned_models) if isinstance(planned_models, str) else planned_models
    if not isinstance(model_family, list) or not all(isinstance(m, str) and m for m in model_family):
        raise ValueError("planned_models must be a list of model IDs")
    if with_reason and answer_format == "letter":
        answer_format = "brief"

    items = load_items(split)
    if permute_seed:
        items = permute_items(items, permute_seed)
    prompt = build_system_prompt(condition, answer_format=answer_format)

    if max_tokens is None:
        max_tokens = int(ANSWER_FORMATS[answer_format]["max_tokens"])
    if token_limit is None:
        # Leave headroom for the prompt and for reasoning tokens that some
        # providers bill separately from the visible completion.
        token_limit = max_tokens * 3 + 1000

    deterministic = bool(send_temperature) and float(temperature) == 0.0
    if epochs is None:
        epochs = 1 if deterministic else NONDETERMINISTIC_EPOCHS
    if epochs < 1 or max_tokens < 1:
        raise ValueError("epochs and max_tokens must be positive")

    config_kwargs: dict[str, Any] = {"max_tokens": max_tokens}
    if send_temperature:
        config_kwargs["temperature"] = float(temperature)

    dataset = MemoryDataset(
        samples=[_sample(item, condition) for item in items],
        name=f"false_floor_{condition}_{split}_p{permute_seed}",
    )

    return _construct_task(
        dataset=dataset,
        solver=[system_message(prompt), generate(), record_answer()],
        scorer=letter_match(),
        config=GenerateConfig(**config_kwargs),
        epochs=epochs,
        token_limit=token_limit,
        metadata={
            "protocol_version": "0.2",
            "study_id": study_id,
            "baseline_repeat": baseline_repeat,
            "planned_models": model_family,
            "dataset_sha256": hashlib.sha256(DATA_PATH.read_bytes()).hexdigest(),
            "prompts_sha256": hashlib.sha256(PROMPTS_PATH.read_bytes()).hexdigest(),
            "item_ids": [str(item["id"]) for item in items],
            "generation": {**config_kwargs, "token_limit": token_limit},
            "condition": condition,
            "split": split,
            "answer_format": answer_format,
            "max_tokens": max_tokens,
            "permute_seed": permute_seed,
            "deterministic": deterministic,
            "epochs": epochs,
            "n_items": len(items),
        },
    )
