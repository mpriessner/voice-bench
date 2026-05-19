"""Runner for the dynamic pool-swap scenario benchmark."""

import csv
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .models import TurnResult
from .scoring_swap import score_swap_turn
from .toolsets import build_core, TOOLSETS

RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"

_SWAP_SYSTEM_PROMPT_PATHS: dict[str, Path] = {
    "openai-realtime-swap": PROMPTS_DIR / "system" / "openai-realtime-swap.md",
    "gemini-live-swap": PROMPTS_DIR / "system" / "gemini-live-swap.md",
}

_FALLBACK_SYSTEM_PROMPT = (
    "You are a voice-controlled lab camera assistant. "
    "Call the appropriate tool for each user command. "
    "If the tool you need is not available, call switch_toolset first."
)


def _load_swap_manifest(manifest_path: Path | None = None) -> list[dict]:
    path = manifest_path or (PROMPTS_DIR / "manifest_swap.json")
    with open(path) as f:
        return json.load(f)


def _load_system_prompt(agent: str = "openai-realtime-swap") -> str:
    path = _SWAP_SYSTEM_PROMPT_PATHS.get(agent)
    if path and path.exists():
        return path.read_text()
    return _FALLBACK_SYSTEM_PROMPT


def _make_adapter(agent: str, initial_toolset: str):
    """Create a fresh adapter instance for the given agent and initial toolset."""
    if agent == "openai-realtime-swap":
        from .adapters.openai_realtime_swap import OpenAIRealtimeSwapAdapter
        return OpenAIRealtimeSwapAdapter(
            toolsets=TOOLSETS, core_tools=build_core(), initial_toolset=initial_toolset
        )
    if agent == "gemini-live-swap":
        from .adapters.gemini_live_swap import GeminiLiveSwapAdapter
        return GeminiLiveSwapAdapter(
            toolsets=TOOLSETS, core_tools=build_core(), initial_toolset=initial_toolset
        )
    raise ValueError(f"Unknown swap agent: {agent!r}")


def _pick_audio(turn_id: str, voice: str) -> Path | None:
    audio_dir = PROMPTS_DIR / "audio" / voice
    wav = audio_dir / f"{turn_id}.wav"
    return wav if wav.exists() else None


def _make_jsonl_row(
    run_id: str,
    scenario: dict,
    turn: dict,
    result: TurnResult,
    score,
    toolset_at_turn_start: str,
    manifest_name: str,
) -> dict:
    return {
        "run_id": run_id,
        "scenario_id": scenario["id"],
        "turn_id": turn["id"],
        "manifest": manifest_name,
        "model_kind": "voice_swap",
        "initial_toolset": scenario.get("initial_toolset", "camera_basics"),
        "toolset_at_turn_start": toolset_at_turn_start,
        "prompt": {
            "id": turn["id"],
            "text": turn.get("text", ""),
            "expected_tool": turn.get("expected_tool"),
            "expected_args": turn.get("expected_args", {}),
        },
        "result": result.to_dict(),
        "score": score.to_dict(),
    }


async def _run_scenario(
    scenario: dict,
    adapter,  # duck-typed: any swap adapter with run_turn + _current_toolset
    run_id: str,
    voice: str,
    system_prompt: str,
    manifest_name: str,
) -> list[dict]:
    """Run all turns in a scenario with one adapter instance (preserves toolset state)."""
    rows: list[dict] = []
    turns = scenario.get("turns", [])

    print(f"\n  Scenario {scenario['id']}: {scenario.get('description', '')}")
    print(f"    initial_toolset={scenario.get('initial_toolset', 'camera_basics')}, {len(turns)} turns")

    for turn in turns:
        turn_id = f"{run_id}-{turn['id']}"
        toolset_at_start = adapter._current_toolset
        audio_path = _pick_audio(turn["id"], voice)

        if audio_path is None:
            print(f"    [SKIP] {turn['id']} — no audio at {audio_path}")
            continue

        expected_tool = turn.get("expected_tool", "")
        expected_args = turn.get("expected_args", {})

        result = await adapter.run_turn(
            audio_wav_path=audio_path,
            tools=[],  # swap adapter ignores this
            system_prompt=system_prompt,
            turn_id=turn_id,
            prompt_id=turn["id"],
            prompt_text=turn.get("text"),  # AC11: pass text for logging/context
        )

        score = score_swap_turn(result, expected_tool, expected_args)
        row = _make_jsonl_row(run_id, scenario, turn, result, score, toolset_at_start, manifest_name)
        rows.append(row)

        status = "PASS" if score.passed else "FAIL"
        rtt = f"  swap_rtt={score.swap_rtt_ms}ms" if score.swap_rtt_ms else ""
        print(
            f"    {status}  {turn['id']}: \"{turn.get('text', '')}\" "
            f"→ expected={expected_tool}{rtt}"
        )

    return rows


def run_swap_benchmark(
    agent: str = "openai-realtime-swap",
    voice: str = "say",
    run_id: str | None = None,
    manifest_path: Path | None = None,
) -> dict:
    """Run the full swap benchmark, one scenario at a time."""
    import asyncio

    RESULTS_DIR.mkdir(exist_ok=True)

    manifest_path = manifest_path or (PROMPTS_DIR / "manifest_swap.json")
    manifest_name = manifest_path.stem
    scenarios = _load_swap_manifest(manifest_path)
    system_prompt = _load_system_prompt(agent)

    run_id = run_id or f"{agent}-{int(time.time())}"
    jsonl_path = RESULTS_DIR / f"{run_id}.jsonl"
    csv_path = RESULTS_DIR / f"{run_id}.csv"

    all_rows: list[dict] = []
    total_turns = 0
    passed_turns = 0

    print(f"\nSwap benchmark: {run_id}")
    print(f"Agent: {agent}")
    print(f"Manifest: {manifest_path} ({len(scenarios)} scenarios)")

    for scenario in scenarios:
        initial_ts = scenario.get("initial_toolset", "camera_basics")
        adapter = _make_adapter(agent, initial_ts)
        rows = asyncio.run(_run_scenario(scenario, adapter, run_id, voice, system_prompt, manifest_name))
        all_rows.extend(rows)

        for row in rows:
            total_turns += 1
            if row["score"].get("passed"):
                passed_turns += 1

    # Write JSONL
    with open(jsonl_path, "w") as f:
        for row in all_rows:
            f.write(json.dumps(row) + "\n")

    # Write CSV
    csv_fields = [
        "run_id", "scenario_id", "turn_id", "initial_toolset", "toolset_at_turn_start",
        "expected_tool", "passed", "tool_correct", "arg_score",
        "is_swap_turn", "swap_happened", "swap_rtt_ms", "toolset_at_call",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields, extrasaction="ignore")
        w.writeheader()
        for row in all_rows:
            sc = row["score"]
            w.writerow({
                "run_id": row["run_id"],
                "scenario_id": row["scenario_id"],
                "turn_id": row["turn_id"],
                "initial_toolset": row["initial_toolset"],
                "toolset_at_turn_start": row["toolset_at_turn_start"],
                "expected_tool": row["prompt"]["expected_tool"],
                "passed": sc.get("passed"),
                "tool_correct": sc.get("tool_correct"),
                "arg_score": sc.get("arg_score"),
                "is_swap_turn": sc.get("is_swap_turn"),
                "swap_happened": sc.get("swap_happened"),
                "swap_rtt_ms": sc.get("swap_rtt_ms"),
                "toolset_at_call": sc.get("toolset_at_call"),
            })

    accuracy = passed_turns / total_turns if total_turns else 0.0
    print(f"\n  Accuracy: {passed_turns}/{total_turns} = {accuracy:.1%}")
    print(f"  Results: {jsonl_path}")

    return {
        "run_id": run_id,
        "agent": agent,
        "total_turns": total_turns,
        "passed": passed_turns,
        "accuracy": accuracy,
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
    }
