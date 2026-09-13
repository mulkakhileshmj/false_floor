"""False Floor: measuring how model performance shifts under evaluation cues.

The task is imported lazily so that `import false_floor` does not register the
task twice when Inspect loads `false_floor/task.py` by path.
"""

from typing import Any

__version__ = "0.2.0"
__all__ = ["false_floor", "parse_letter"]


def __getattr__(name: str) -> Any:
    if name == "false_floor":
        from false_floor.task import false_floor

        return false_floor
    if name == "parse_letter":
        from false_floor.scoring import parse_letter

        return parse_letter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
