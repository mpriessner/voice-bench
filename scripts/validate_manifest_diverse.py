"""
Validate manifest_diverse.json against the diverse-mode invariants.

Checks:
  1. IDs are unique.
  2. No positive prompt reuses the same expected_tool twice.
  3. Every expected_tool exists in ALL_TOOLS.
  4. Negative prompts have expected_tool=None and negative=True.
  5. Positive prompts have expected_tool set and negative=False.
  6. At least 3 negative prompts present.
  7. audio_subdir is set on every entry.
  8. No ID collision with the standard manifest (if it exists).

Run:
    uv run python scripts/validate_manifest_diverse.py [path/to/manifest_diverse.json]
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent


def load(path: Path) -> list[dict]:
    with open(path) as f:
        return json.load(f)


def validate(prompts: list[dict], standard_ids: set[str]) -> list[str]:
    errors: list[str] = []

    ids = [p["id"] for p in prompts]
    if len(ids) != len(set(ids)):
        from collections import Counter
        dupes = [k for k, v in Counter(ids).items() if v > 1]
        errors.append(f"Duplicate IDs: {dupes}")

    overlap = set(ids) & standard_ids
    if overlap:
        errors.append(f"ID collision with standard manifest: {sorted(overlap)}")

    try:
        from voice_bench.tools import ALL_TOOLS  # type: ignore
        valid_names = {t.name for t in ALL_TOOLS}
    except ImportError:
        valid_names = None

    seen_tools: set[str] = set()
    neg_count = 0

    for p in prompts:
        pid = p["id"]

        if not p.get("audio_subdir"):
            errors.append(f"{pid}: missing audio_subdir")

        if p.get("negative"):
            neg_count += 1
            if p.get("expected_tool") is not None:
                errors.append(f"{pid}: negative prompt must have expected_tool=null")
        else:
            tool = p.get("expected_tool")
            if not tool:
                errors.append(f"{pid}: positive prompt missing expected_tool")
                continue
            if tool in seen_tools:
                errors.append(f"{pid}: expected_tool={tool!r} appears more than once")
            seen_tools.add(tool)
            if valid_names and tool not in valid_names:
                errors.append(f"{pid}: expected_tool={tool!r} not in ALL_TOOLS")

    if neg_count < 3:
        errors.append(f"Too few negative prompts: {neg_count} (need >= 3)")

    return errors


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "prompts" / "manifest_diverse.json"
    if not path.exists():
        print(f"ERROR: {path} does not exist", file=sys.stderr)
        sys.exit(1)

    prompts = load(path)

    standard_path = REPO / "prompts" / "manifest.json"
    standard_ids: set[str] = set()
    if standard_path.exists():
        std = load(standard_path)
        standard_ids = {p["id"] for p in std}

    errors = validate(prompts, standard_ids)

    if errors:
        print(f"FAIL — {len(errors)} error(s) in {path}:")
        for e in errors:
            print(f"  • {e}")
        sys.exit(1)
    else:
        pos = sum(1 for p in prompts if not p.get("negative"))
        neg = sum(1 for p in prompts if p.get("negative"))
        tools = {p["expected_tool"] for p in prompts if p.get("expected_tool")}
        print(f"OK — {len(prompts)} prompts: {pos} positive ({len(tools)} tools), {neg} negative")


if __name__ == "__main__":
    main()
