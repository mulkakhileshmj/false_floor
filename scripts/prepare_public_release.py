#!/usr/bin/env python3
"""Copy an explicit allowlist into a new folder, without credentials or raw logs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FILES = ["README.md", "PROJECT.md", "LICENSE", ".gitignore", ".env.example", "pyproject.toml",
         "PREREGISTRATION.md", "PITCH.md", "CHANGELOG.md", "REFERENCES.md",
         "CONTRIBUTING.md", "GRANT_PLAN.md", "requirements-tested.txt",
         ".github/workflows/tests.yml", "scripts/run_panel.py", "scripts/summarize.py",
         "scripts/build_report.py", "scripts/validate_dataset.py", "scripts/prepare_public_release.py"]
PATTERNS = ["false_floor/*.py", "false_floor/*.yaml", "false_floor/data/*.jsonl",
            "false_floor/data/*.md", "scripts/*.sh", "scripts/*.ps1",
            "tests/*.py", "docs/*.html", "docs/*.md", "results/*.md"]
SECRET = re.compile(rb"(?:sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def export(destination: Path) -> int:
    destination = destination.resolve()
    if destination == ROOT or ROOT not in destination.parents:
        raise ValueError("Release destination must be a new subdirectory inside this project")
    if destination.exists():
        raise ValueError("Destination already exists; choose a new folder to avoid overwriting work")
    sources = [ROOT / name for name in FILES]
    for pattern in PATTERNS:
        sources.extend(sorted(ROOT.glob(pattern)))
    # The standalone Vercel site includes only these reviewed static file types.
    sources.extend(p for p in (ROOT / "vercel-site").rglob("*")
                   if p.is_file() and p.suffix in {".html", ".md", ".json", ".jsonl"})
    sources = sorted(set(sources))
    for source in sources:
        if source.is_symlink() or ROOT not in source.resolve().parents:
            raise ValueError("Refusing to export a symlink or external source")
        if not source.is_file():
            raise ValueError(f"Missing release file: {source.relative_to(ROOT)}")
        if SECRET.search(source.read_bytes()):
            raise ValueError(f"Potential credential in {source.relative_to(ROOT)}; export stopped")
    destination.mkdir()
    hashes = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[relative.as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest()
    (destination / "RELEASE_MANIFEST.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {len(sources)} files in {destination}")
    print("No Git repository was created and nothing was uploaded.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", type=Path, default=ROOT / "public-release")
    raise SystemExit(export(parser.parse_args().destination))
