#!/usr/bin/env python3
"""Run one versioned panel. No API calls are made with --dry-run."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from false_floor.analysis import collect, find_logs  # noqa: E402

CONDITIONS = ["control", "pressure_placebo", "eval_cue", "deploy_threat", "monitor"]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def plan(args: argparse.Namespace) -> dict:
    items = [json.loads(line) for line in (ROOT / "false_floor/data/public.jsonl").read_text().splitlines() if line.strip()]
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models or len(models) != len(set(models)):
        raise ValueError("Supply a nonempty list of distinct model IDs")
    if min(args.epochs, args.max_tokens, args.max_connections) < 1:
        raise ValueError("Epochs, token limit and connection count must be positive")
    cells = []
    for model in models:
        for seed in (0, 62):
            for condition in CONDITIONS + ["control_repeat"]:
                cells.append({"model": model, "seed": seed, "condition": condition})
    random.Random(args.schedule_seed).shuffle(cells)
    for index, cell in enumerate(cells):
        cell["directory"] = f"cell-{index:03d}"
    calls = len(items) * len(cells) * args.epochs
    return {
        "protocol_version": "0.2", "models": models,
        "dataset_sha256": digest(ROOT / "false_floor/data/public.jsonl"),
        "prompts_sha256": digest(ROOT / "false_floor/prompts.yaml"),
        "code_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sorted((ROOT / "false_floor").glob("*.py"))},
        "epochs": args.epochs, "max_tokens": args.max_tokens,
        "answer_format": args.answer_format, "temperature": args.temperature,
        "send_temperature": not args.omit_temperature,
        "schedule_seed": args.schedule_seed, "n_items": len(items),
        "planned_responses": calls, "cells": cells,
    }


def save_manifest(path: Path, manifest: dict) -> None:
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    pending.replace(path)


def command(cell: dict, config: dict, study_id: str, log_dir: Path,
            max_connections: int) -> list[str]:
    repeat = cell["condition"] == "control_repeat"
    task_args = {
        "condition": "control" if repeat else cell["condition"],
        "baseline_repeat": int(repeat), "study_id": study_id,
        "planned_models": json.dumps(config["models"]),
        "permute_seed": cell["seed"], "epochs": config["epochs"],
        "answer_format": config["answer_format"], "max_tokens": config["max_tokens"],
        "temperature": config["temperature"],
        "send_temperature": str(config["send_temperature"]).lower(),
    }
    result = [sys.executable, "-m", "inspect_ai", "eval", "false_floor/task.py@false_floor",
              "--model", cell["model"], "--log-dir", str(log_dir),
              "--max-connections", str(max_connections)]
    for name, value in task_args.items():
        result.extend(["-T", f"{name}={value}"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="Comma-separated fixed Inspect model IDs")
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--answer-format", choices=["letter", "brief", "reasoned"], default="reasoned")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--omit-temperature", action="store_true")
    parser.add_argument("--max-connections", type=int, default=4)
    parser.add_argument("--schedule-seed", type=int, default=0)
    parser.add_argument("--max-responses", type=int, help="Abort before starting if planned responses exceed this limit")
    args = parser.parse_args()
    validation = subprocess.run([sys.executable, "scripts/validate_dataset.py", "--quiet"], cwd=ROOT)
    if validation.returncode:
        return validation.returncode
    config = plan(args)
    if args.max_responses is not None and config["planned_responses"] > args.max_responses:
        raise ValueError("Planned responses exceed --max-responses")
    print(f"Plan: {config['planned_responses']} responses, {len(config['cells'])} cells, "
          f"up to {args.max_tokens} output tokens per response.", flush=True)
    print("Includes two independent control runs per option order. Provider retries can add billable requests.", flush=True)
    print("Use provider credit limits for a hard spending cap; response counts are not dollar estimates.", flush=True)
    if args.dry_run:
        return 0
    if args.resume and args.log_dir is None:
        raise ValueError("--resume requires the original --log-dir")
    directory = (args.log_dir or ROOT / "logs" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])).resolve()
    manifest_path = directory / "manifest.json"
    if directory.exists():
        if not args.resume or not manifest_path.exists():
            raise ValueError("Use a new log directory, or --resume with its existing manifest")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["plan"] != config:
            raise ValueError("Resume refused: code, data or experiment settings changed")
    else:
        if args.resume:
            raise ValueError("Cannot resume a directory that does not exist")
        directory.mkdir(parents=True)
        manifest = {"study_id": uuid.uuid4().hex, "plan": config, "complete": False}
        save_manifest(manifest_path, manifest)
    print(f"Study directory: {directory}", flush=True)
    for cell in config["cells"]:
        cell_dir = directory / cell["directory"]
        existing, _ = collect(find_logs([cell_dir]))
        key = (cell["model"], cell["condition"], cell["seed"])
        if key in existing:
            print(f"Already complete: {cell['directory']}", flush=True)
            continue
        print(f"Running {cell['model']} / {cell['condition']} / seed {cell['seed']}", flush=True)
        result = subprocess.run(command(cell, config, manifest["study_id"], cell_dir, args.max_connections), cwd=ROOT)
        if result.returncode:
            print("Stopped after a failed cell. Resume the same directory to retry unfinished work.")
            return result.returncode
        completed, _ = collect(find_logs([cell_dir]))
        if key not in completed:
            raise ValueError("Cell did not produce a complete versioned log; report publication withheld")
    complete, problems = collect(find_logs([directory]))
    if len(complete) != len(config["cells"]):
        raise ValueError("Panel coverage is incomplete; report publication withheld")
    for script, filename in (("summarize.py", "report.md"), ("build_report.py", "report.html")):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), str(directory),
                                 "--out", str(directory / filename)], cwd=ROOT)
        if result.returncode:
            return result.returncode
    manifest["complete"] = True
    manifest["excluded_log_notes"] = problems
    save_manifest(manifest_path, manifest)
    print(f"Complete. Review {directory / 'report.html'} before publishing results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
