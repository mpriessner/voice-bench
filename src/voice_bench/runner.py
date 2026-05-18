"""BenchmarkRunner: loads manifest, runs turns, scores, writes results."""

import csv
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .adapters.gemini_live import GeminiLiveAdapter
from .models import TurnResult, Score
from .scoring import score_turn
from .tools import load_tools


RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def _load_manifest(manifest_path: Path | None = None) -> list[dict]:
    path = manifest_path or (PROMPTS_DIR / "manifest.json")
    with open(path) as f:
        return json.load(f)


def _load_system_prompt(agent: str) -> str:
    p = PROMPTS_DIR / "system" / f"{agent}.md"
    if p.exists():
        return p.read_text()
    return (
        "You are a voice assistant for a lab camera app. "
        "When the user gives a command, call the appropriate tool immediately. "
        "Give a very brief spoken confirmation after calling the tool."
    )


def _pick_audio(prompt: dict, voice: str) -> Path | None:
    audio_dir = PROMPTS_DIR / "audio" / voice
    wav = audio_dir / f"{prompt['id']}.wav"
    return wav if wav.exists() else None


def run_benchmark(
    agent: str,
    tool_count: int,
    mode: str,
    voice: str = "say",
    run_id: str | None = None,
    manifest_path: Path | None = None,
    timeouts: dict | None = None,
) -> dict:
    run_id = run_id or f"{agent}-{tool_count}t-{int(time.time())}"
    RESULTS_DIR.mkdir(exist_ok=True)

    manifest = _load_manifest(manifest_path)
    system_prompt = _load_system_prompt(agent)
    tools = load_tools(tool_count)

    # Filter by mode
    if mode == "smoke":
        prompts = manifest[:5]
    else:
        prompts = manifest

    # Build adapter
    if agent == "gemini-live":
        adapter = GeminiLiveAdapter()
    else:
        raise NotImplementedError(f"Adapter not yet implemented: {agent}")

    jsonl_path = RESULTS_DIR / f"{run_id}.jsonl"
    csv_path = RESULTS_DIR / f"{run_id}.csv"

    import asyncio

    rows: list[dict] = []

    async def _run_all() -> None:
        for prompt in prompts:
            audio_path = _pick_audio(prompt, voice)
            if audio_path is None:
                print(f"  [SKIP] No audio for prompt {prompt['id']} voice={voice}")
                continue

            turn_id = str(uuid.uuid4())[:8]
            print(f"  [{turn_id}] {prompt['id']} — \"{prompt['text']}\"")

            result: TurnResult = await adapter.run_turn(
                audio_wav_path=audio_path,
                tools=tools,
                system_prompt=system_prompt,
                turn_id=turn_id,
                prompt_id=prompt["id"],
                timeouts=timeouts,
            )

            score: Score = score_turn(
                result=result,
                expected_tool=prompt.get("expected_tool"),
                expected_args=prompt.get("expected_args"),
                is_negative_prompt=prompt.get("negative", False),
            )

            row = {
                "run_id": run_id,
                "agent": agent,
                "tool_count": tool_count,
                "voice": voice,
                "prompt_id": prompt["id"],
                "prompt_text": prompt["text"],
                "expected_tool": prompt.get("expected_tool"),
                "terminal_reason": result.terminal_reason.value,
                **score.to_dict(),
            }
            rows.append(row)

            # Append JSONL line
            with open(jsonl_path, "a") as jf:
                line = {
                    "run_id": run_id,
                    "prompt": prompt,
                    "result": result.to_dict(),
                    "score": score.to_dict(),
                }
                jf.write(json.dumps(line) + "\n")

            status = "PASS" if score.passed else "FAIL"
            ttft = f"{score.ttf_tool_ms}ms" if score.ttf_tool_ms is not None else "—"
            print(f"    {status}  tool={score.tool_name_match}  args={score.arg_score:.2f}  ttf_tool={ttft}")

    asyncio.run(_run_all())

    # Write summary CSV
    if rows:
        fieldnames = list(rows[0].keys())
        with open(csv_path, "w", newline="") as cf:
            writer = csv.DictWriter(cf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    passed = sum(1 for r in rows if r.get("passed"))
    total = len(rows)
    accuracy = passed / total if total else 0.0

    summary = {
        "run_id": run_id,
        "agent": agent,
        "tool_count": tool_count,
        "mode": mode,
        "total_turns": total,
        "passed": passed,
        "accuracy": round(accuracy, 4),
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
    }
    print(f"\n  Accuracy: {passed}/{total} = {accuracy:.1%}")
    print(f"  Results:  {jsonl_path}")
    return summary
