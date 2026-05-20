"""BenchmarkRunner: loads manifest, runs turns, scores, writes results."""

import csv
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .adapters.registry import build_adapter
from .models import TurnResult, Score
from .scoring import score_turn, score_routing_turn, score_pipeline_turn
from .tools import load_tools, ALL_TOOLS, META_TOOLS, CATEGORY_TO_META_TOOL, SUBTOOLS_BY_BUCKET


RESULTS_DIR = Path(__file__).parent.parent.parent / "results"
PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts"


def _load_manifest(manifest_path: Path | None = None) -> list[dict]:
    path = manifest_path or (PROMPTS_DIR / "manifest.json")
    with open(path) as f:
        return json.load(f)


SYSTEM_PROMPT_ALIASES: dict[str, str] = {
    "claude-sonnet": "claude-opus",
    "gpt-4o": "gpt-text",
    "gpt-5": "gpt-text",
    "gemini-pro": "gemini-text",
    "gemini-flash": "gemini-text",
}


def _load_system_prompt(agent: str) -> str:
    alias = SYSTEM_PROMPT_ALIASES.get(agent, agent)
    p = PROMPTS_DIR / "system" / f"{alias}.md"
    if p.exists():
        return p.read_text()
    # Generic fallback for text adapters
    return (
        "You are an assistant for a lab camera app. "
        "When the user gives a command, call the appropriate tool. "
        "Do not explain — just call the tool."
    )


def _pick_audio(prompt: dict, voice: str) -> Path | None:
    subdir = prompt.get("audio_subdir")
    audio_dir = PROMPTS_DIR / "audio" / voice / subdir if subdir else PROMPTS_DIR / "audio" / voice
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
    routing_mode: str | None = None,
    strict_routing: bool = True,
    benchmark_mode: str = "needle",
) -> dict:
    RESULTS_DIR.mkdir(exist_ok=True)

    manifest_name = (manifest_path or PROMPTS_DIR / "manifest.json").stem
    manifest = _load_manifest(manifest_path)
    system_prompt = _load_system_prompt(agent)

    # Routing mode: load meta-tools instead of progressive tools BEFORE filtering
    is_routing = (mode == "routing")
    if is_routing:
        tools = META_TOOLS[:]
        tool_count = len(tools)
        run_id = run_id or f"{agent}-routing-{routing_mode or 'auto'}-{int(time.time())}"
    else:
        tools = load_tools(tool_count)
        run_id = run_id or f"{agent}-{tool_count}t-{int(time.time())}"

    # Filter by mode
    if mode == "smoke":
        smoke_tagged = [p for p in manifest if p.get("smoke", False)]
        prompts = smoke_tagged if smoke_tagged else manifest[:5]
    elif mode == "v1":
        prompts = [p for p in manifest if p.get("difficulty") == "v1"]
    elif mode == "v2":
        prompts = [p for p in manifest if p.get("difficulty") == "v2"]
    elif mode == "v3":
        prompts = [p for p in manifest if p.get("difficulty") == "v3"]
    else:
        prompts = manifest

    # Build adapter via central registry
    adapter = build_adapter(agent, force_tool_call=strict_routing)

    # Override tool_choice for forced routing mode (text adapters only)
    if is_routing and routing_mode == "forced" and not getattr(adapter, "REQUIRES_AUDIO", True):
        adapter._routing_force = True  # picked up below in run_turn kwargs

    # Filter prompts to only those whose expected tool is loaded in this run.
    # In routing mode, skip this filter — all meta-tools are always loaded.
    if not is_routing:
        loaded_tool_names = {t.name for t in tools}
        prompts = [
            p for p in prompts
            if not p.get("expected_tool") or p["expected_tool"] in loaded_tool_names
        ]

    requires_audio = getattr(adapter, "REQUIRES_AUDIO", True)

    jsonl_path = RESULTS_DIR / f"{run_id}.jsonl"
    csv_path = RESULTS_DIR / f"{run_id}.csv"

    import asyncio

    rows: list[dict] = []

    async def _run_all() -> None:
        for prompt in prompts:
            audio_path = _pick_audio(prompt, voice)
            if audio_path is None and requires_audio:
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
                prompt_text=prompt.get("text"),
            )

            if is_routing:
                score: Score = score_routing_turn(
                    result=result,
                    expected_category=prompt.get("expected_category"),
                    category_to_meta_tool=CATEGORY_TO_META_TOOL,
                )
            else:
                score: Score = score_turn(
                    result=result,
                    expected_tool=prompt.get("expected_tool"),
                    expected_args=prompt.get("expected_args"),
                    is_negative_prompt=prompt.get("negative", False),
                )

            model_kind = getattr(getattr(result, "timeline", None), "model_kind", "voice")
            row = {
                "run_id": run_id,
                "agent": agent,
                "tool_count": tool_count,
                "voice": voice,
                "manifest": manifest_name,
                "model_kind": model_kind,
                "tool_mode": "routing" if is_routing else "standard",
                "benchmark_mode": benchmark_mode,
                "routing_mode": routing_mode if is_routing else None,
                "prompt_id": prompt["id"],
                "prompt_text": prompt["text"],
                "expected_tool": CATEGORY_TO_META_TOOL.get(prompt.get("expected_category", ""), "") if is_routing else prompt.get("expected_tool"),
                "terminal_reason": result.terminal_reason.value,
                **score.to_dict(),
            }
            rows.append(row)

            # Append JSONL line
            with open(jsonl_path, "a") as jf:
                line = {
                    "run_id": run_id,
                    "manifest": manifest_name,
                    "model_kind": model_kind,
                    "tool_mode": "routing" if is_routing else "standard",
                    "benchmark_mode": benchmark_mode,
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


def run_pipeline_benchmark(
    voice_agent: str,
    text_agent: str,
    mode: str,
    voice: str = "say",
    run_id: str | None = None,
    manifest_path: Path | None = None,
    timeouts: dict | None = None,
) -> dict:
    """Two-layer pipeline: voice adapter routes to bucket → text adapter picks sub-tool."""
    RESULTS_DIR.mkdir(exist_ok=True)

    manifest_name = (manifest_path or PROMPTS_DIR / "manifest.json").stem
    manifest = _load_manifest(manifest_path)

    run_id = run_id or f"pipeline-{voice_agent}+{text_agent}-{int(time.time())}"

    voice_system_prompt = _load_system_prompt(voice_agent)
    text_system_prompt = _load_system_prompt(text_agent)

    voice_adapter = build_adapter(voice_agent)
    text_adapter = build_adapter(text_agent)

    if not getattr(voice_adapter, "REQUIRES_AUDIO", True):
        raise ValueError(f"{voice_agent} is not a native voice adapter (REQUIRES_AUDIO must be True)")
    if getattr(text_adapter, "REQUIRES_AUDIO", True):
        raise ValueError(f"{text_agent} is not a text adapter (REQUIRES_AUDIO must be False)")

    all_tools_by_name = {t.name: t for t in ALL_TOOLS}

    if mode == "smoke":
        smoke_tagged = [p for p in manifest if p.get("smoke", False)]
        prompts = smoke_tagged if smoke_tagged else manifest[:5]
    elif mode == "v1":
        prompts = [p for p in manifest if p.get("difficulty") == "v1"]
    elif mode == "v2":
        prompts = [p for p in manifest if p.get("difficulty") == "v2"]
    elif mode == "v3":
        prompts = [p for p in manifest if p.get("difficulty") == "v3"]
    else:
        prompts = manifest

    # Pipeline only scores positive prompts with a known expected tool
    prompts = [p for p in prompts if p.get("expected_tool")]

    jsonl_path = RESULTS_DIR / f"{run_id}.jsonl"
    csv_path = RESULTS_DIR / f"{run_id}.csv"

    import asyncio

    rows: list[dict] = []

    async def _run_all() -> None:
        for prompt in prompts:
            audio_path = _pick_audio(prompt, voice)
            if audio_path is None:
                print(f"  [SKIP] No audio for {prompt['id']} voice={voice}")
                continue

            expected_tool_name = prompt.get("expected_tool")
            expected_tool_obj = all_tools_by_name.get(expected_tool_name or "")
            expected_category = expected_tool_obj.category if expected_tool_obj else None

            turn_id = str(uuid.uuid4())[:8]
            print(f"  [{turn_id}] {prompt['id']} — \"{prompt['text']}\"")

            # ── Layer 1: voice routes to meta-tool ────────────────────────────
            voice_result: TurnResult = await voice_adapter.run_turn(
                audio_wav_path=audio_path,
                tools=META_TOOLS,
                system_prompt=voice_system_prompt,
                turn_id=turn_id,
                prompt_id=prompt["id"],
                timeouts=timeouts,
                prompt_text=prompt.get("text"),
            )

            called_meta = voice_result.tool_calls[0].tool_name if voice_result.tool_calls else None

            # Prefer in-session ASR transcript; fall back to prompt text
            user_transcript = (
                voice_result.transcripts.get("user", "").strip()
                or prompt.get("text", "")
            )

            # ── Layer 2: text picks sub-tool within bucket ────────────────────
            text_result: TurnResult | None = None
            if called_meta and called_meta in SUBTOOLS_BY_BUCKET:
                sub_tools = SUBTOOLS_BY_BUCKET[called_meta]
                if sub_tools:
                    text_result = await text_adapter.run_turn(
                        audio_wav_path=None,
                        tools=sub_tools,
                        system_prompt=text_system_prompt,
                        turn_id=turn_id,
                        prompt_id=prompt["id"],
                        timeouts=timeouts,
                        prompt_text=user_transcript,
                    )

            pipeline_score = score_pipeline_turn(
                voice_result=voice_result,
                text_result=text_result,
                expected_category=expected_category,
                expected_tool=expected_tool_name,
                expected_args=prompt.get("expected_args"),
                category_to_meta_tool=CATEGORY_TO_META_TOOL,
            )

            row = {
                "run_id": run_id,
                "voice_agent": voice_agent,
                "text_agent": text_agent,
                "voice": voice,
                "manifest": manifest_name,
                "tool_mode": "pipeline",
                "prompt_id": prompt["id"],
                "prompt_text": prompt["text"],
                "expected_tool": expected_tool_name,
                "expected_category": expected_category,
                "user_transcript": user_transcript,
                "voice_terminal_reason": voice_result.terminal_reason.value,
                "text_terminal_reason": text_result.terminal_reason.value if text_result else None,
                **pipeline_score.to_dict(),
            }
            rows.append(row)

            with open(jsonl_path, "a") as jf:
                line = {
                    "run_id": run_id,
                    "manifest": manifest_name,
                    "tool_mode": "pipeline",
                    "voice_agent": voice_agent,
                    "text_agent": text_agent,
                    "prompt": prompt,
                    "voice_result": voice_result.to_dict(),
                    "text_result": text_result.to_dict() if text_result else None,
                    "pipeline_score": pipeline_score.to_dict(),
                    "user_transcript": user_transcript,
                }
                jf.write(json.dumps(line) + "\n")

            status = "PASS" if pipeline_score.end_to_end_pass else "FAIL"
            wall = f"{pipeline_score.pipeline_wall_ms}ms" if pipeline_score.pipeline_wall_ms is not None else "—"
            print(
                f"    {status}  bucket={pipeline_score.bucket_match}  "
                f"subtool={pipeline_score.subtool_match}  args={pipeline_score.arg_score:.2f}  "
                f"wall={wall}"
            )

    asyncio.run(_run_all())

    if rows:
        fieldnames = list(rows[0].keys())
        with open(csv_path, "w", newline="") as cf:
            writer = csv.DictWriter(cf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    passed = sum(1 for r in rows if r.get("end_to_end_pass"))
    total = len(rows)
    accuracy = passed / total if total else 0.0

    summary = {
        "run_id": run_id,
        "voice_agent": voice_agent,
        "text_agent": text_agent,
        "mode": mode,
        "total_turns": total,
        "passed": passed,
        "accuracy": round(accuracy, 4),
        "jsonl": str(jsonl_path),
        "csv": str(csv_path),
    }
    print(f"\n  End-to-end accuracy: {passed}/{total} = {accuracy:.1%}")
    print(f"  Results:  {jsonl_path}")
    return summary
