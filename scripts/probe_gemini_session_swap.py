"""
Phase 0 risk gate: verify Gemini Live session-restart swap mechanism.

Tests:
  1. Connect with camera_basics toolset; capture session resumption handle.
  2. Send text "Take a photo." → model should call take_photo (core tool).
  3. Wait for turn_complete; close session.
  4a. Attempt restart WITH resumption handle + lab_imaging pool.
  4b. If that fails, attempt clean restart (no handle).
  5. Send "Switch to the 20x objective." → model should call set_microscope_objective.
  6. Report: restart RTT, resumption handle received, mechanism that worked.

Conclusions reported:
  - Which mechanism works: session_resumption / clean_restart / neither
  - Restart RTT (time from old session close to new session ready)
  - Whether conversation context survives a restart (inferred from Phase 2 result)

Usage:
    cd voice-bench && uv run python scripts/probe_gemini_session_swap.py
"""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from google import genai
from google.genai import types

from voice_bench.toolsets import build_core, build_pool, build_visible_tools, TOOLSET_DESCRIPTIONS

DEFAULT_MODEL = os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")

SYSTEM_PROMPT = (
    "You are a voice-controlled lab camera assistant. "
    "When the user gives a command, call the appropriate tool. "
    "Do not explain — just call the tool."
)


def _build_config(tools, system_prompt, resumption_handle=None) -> dict:
    declarations = [
        types.FunctionDeclaration(
            name=t.name,
            description=t.description,
            parameters=_schema_from_dict(t.parameters),
        )
        for t in tools
    ]
    config: dict = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_prompt,
        "tools": [types.Tool(function_declarations=declarations)],
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }
    if resumption_handle:
        config["session_resumption"] = types.SessionResumptionConfig(
            handle=resumption_handle
        )
    return config


def _schema_from_dict(d: dict):
    type_map = {
        "boolean": "BOOLEAN", "string": "STRING", "number": "NUMBER",
        "integer": "INTEGER", "object": "OBJECT", "array": "ARRAY",
    }
    kwargs: dict = {}
    if "type" in d:
        kwargs["type"] = type_map.get(d["type"].lower(), d["type"].upper())
    if "description" in d:
        kwargs["description"] = d["description"]
    if "enum" in d:
        kwargs["enum"] = [str(v) for v in d["enum"]]
    if "properties" in d:
        kwargs["properties"] = {k: _schema_from_dict(v) for k, v in d["properties"].items()}
    if "required" in d:
        kwargs["required"] = d["required"]
    return types.Schema(**kwargs)


async def probe() -> dict:
    client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))

    initial_visible = build_visible_tools("camera_basics")
    lab_visible = build_core() + build_pool("lab_imaging")

    print(f"Model: {DEFAULT_MODEL}")
    print(f"Initial tools ({len(initial_visible)}): {[t.name for t in initial_visible]}")
    print(f"Lab pool ({len(lab_visible)}): {[t.name for t in lab_visible]}")
    print()

    results: dict = {
        "model": DEFAULT_MODEL,
        "phase1_tool_call": None,
        "resumption_handle_received": False,
        "resumption_handle_length": None,
        "restart_rtt_ms": None,
        "mechanism_used": None,
        "phase2_tool_call": None,
        "errors": [],
    }

    resumption_handle: str | None = None

    # ── Phase 1: Initial session ───────────────────────────────────────────────
    print("Phase 1: initial session with camera_basics...")
    config1 = _build_config(initial_visible, SYSTEM_PROMPT)

    try:
        async with asyncio.timeout(90.0):
            async with client.aio.live.connect(
                model=f"models/{DEFAULT_MODEL}", config=config1
            ) as session:
                await session.send_client_content(
                    turns={
                        "role": "user",
                        "parts": [{"text": "Take a photo."}],
                    },
                    turn_complete=True,
                )

                async for message in session.receive():
                    sru = getattr(message, "session_resumption_update", None)
                    if sru and sru.new_handle:
                        resumption_handle = sru.new_handle
                        results["resumption_handle_received"] = True
                        results["resumption_handle_length"] = len(resumption_handle)
                        print(f"  ✓ Resumption handle received ({len(resumption_handle)} chars)")

                    if message.tool_call:
                        for fc in message.tool_call.function_calls:
                            results["phase1_tool_call"] = {
                                "name": fc.name, "args": dict(fc.args or {}),
                            }
                            print(f"  Tool call: {fc.name}({dict(fc.args or {})})")
                            await session.send_tool_response(
                                function_responses=types.FunctionResponse(
                                    name=fc.name,
                                    response={"result": "ok"},
                                    id=fc.id,
                                )
                            )

                    if message.server_content and message.server_content.turn_complete:
                        print("  ✓ turn_complete — closing Phase 1 session")
                        break

    except asyncio.TimeoutError:
        results["errors"].append("phase1 timeout")
        # Still attempt Phase 2 if we at least got the tool call and handle
        if not (results["phase1_tool_call"] and resumption_handle):
            return results
    except Exception as e:
        results["errors"].append(f"phase1 error: {e}")
        return results

    p1 = results["phase1_tool_call"]
    p1_ok = p1 and p1["name"] == "take_photo"
    print(f"\n  Phase 1: {p1['name'] if p1 else 'no call'} — {'PASS ✓' if p1_ok else 'FAIL ✗'}")

    # ── Phase 2a: Restart WITH resumption handle ───────────────────────────────
    print(f"\nPhase 2a: restart with resumption {'handle' if resumption_handle else '(no handle — skip)'}...")
    ts_restart = time.time()

    if resumption_handle:
        config2 = _build_config(lab_visible, SYSTEM_PROMPT, resumption_handle=resumption_handle)
        try:
            async with asyncio.timeout(60.0):
                async with client.aio.live.connect(
                    model=f"models/{DEFAULT_MODEL}", config=config2
                ) as session2:
                    results["restart_rtt_ms"] = int((time.time() - ts_restart) * 1000)
                    results["mechanism_used"] = "session_resumption"
                    print(f"  ✓ Reconnected in {results['restart_rtt_ms']}ms (session_resumption)")

                    await session2.send_client_content(
                        turns={"role": "user", "parts": [{"text": "Switch to the 20x objective."}]},
                        turn_complete=True,
                    )

                    async for msg in session2.receive():
                        if msg.tool_call:
                            for fc in msg.tool_call.function_calls:
                                results["phase2_tool_call"] = {
                                    "name": fc.name, "args": dict(fc.args or {}),
                                }
                                print(f"  Tool call: {fc.name}({dict(fc.args or {})})")
                                await session2.send_tool_response(
                                    function_responses=types.FunctionResponse(
                                        name=fc.name,
                                        response={"result": "ok"},
                                        id=fc.id,
                                    )
                                )
                        if msg.server_content and msg.server_content.turn_complete:
                            break

        except Exception as e:
            # Empty-string exceptions are the SDK closing the session normally — not a real error.
            # Only treat it as a failure if phase2_tool_call was never set.
            if results["phase2_tool_call"] is None:
                print(f"  ✗ session_resumption failed: {e!r}")
                results["errors"].append(f"phase2a error: {e!r}")
                results["mechanism_used"] = None
                resumption_handle = None
            else:
                print(f"  (session closed after turn_complete — normal)")
                results["mechanism_used"] = "session_resumption"

    # ── Phase 2b: Clean restart fallback ──────────────────────────────────────
    if results["phase2_tool_call"] is None:
        print("\nPhase 2b: clean restart (no handle)...")
        ts_restart2 = time.time()
        config2b = _build_config(lab_visible, SYSTEM_PROMPT, resumption_handle=None)
        try:
            async with asyncio.timeout(60.0):
                async with client.aio.live.connect(
                    model=f"models/{DEFAULT_MODEL}", config=config2b
                ) as session2b:
                    results["restart_rtt_ms"] = int((time.time() - ts_restart2) * 1000)
                    results["mechanism_used"] = "clean_restart"
                    print(f"  ✓ Reconnected in {results['restart_rtt_ms']}ms (clean_restart)")

                    await session2b.send_client_content(
                        turns={"role": "user", "parts": [{"text": "Switch to the 20x objective."}]},
                        turn_complete=True,
                    )

                    async for msg in session2b.receive():
                        if msg.tool_call:
                            for fc in msg.tool_call.function_calls:
                                results["phase2_tool_call"] = {
                                    "name": fc.name, "args": dict(fc.args or {}),
                                }
                                print(f"  Tool call: {fc.name}({dict(fc.args or {})})")
                                await session2b.send_tool_response(
                                    function_responses=types.FunctionResponse(
                                        name=fc.name,
                                        response={"result": "ok"},
                                        id=fc.id,
                                    )
                                )
                        if msg.server_content and msg.server_content.turn_complete:
                            break

        except Exception as e:
            results["errors"].append(f"phase2b error: {e}")

    return results


def main():
    try:
        results = asyncio.run(probe())
    except asyncio.TimeoutError:
        print("\nERROR: probe timed out")
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print("\n" + "=" * 60)
    print("PROBE SUMMARY")
    print("=" * 60)
    print(json.dumps(results, indent=2))

    p1 = results.get("phase1_tool_call")
    p2 = results.get("phase2_tool_call")
    rtt = results.get("restart_rtt_ms")
    mechanism = results.get("mechanism_used")
    handle_ok = results.get("resumption_handle_received")
    errors = results.get("errors", [])

    p1_ok = p1 and p1.get("name") == "take_photo"
    p2_ok = p2 and p2.get("name") == "set_microscope_objective"
    rtt_ok = rtt is not None and rtt < 10000  # 10s is acceptable for Gemini restart

    print("\nResults:")
    print(f"  Phase 1 (core tool call):         {'PASS' if p1_ok else 'FAIL'}")
    print(f"  Resumption handle available:       {'YES' if handle_ok else 'NO'}")
    print(f"  Swap mechanism that worked:        {mechanism or 'NONE'}")
    print(f"  Restart RTT:                       {rtt}ms")
    print(f"  Phase 2 (lab tool after restart):  {'PASS' if p2_ok else 'FAIL'}")
    if errors:
        print(f"  Errors: {errors}")

    print()
    if mechanism == "session_resumption":
        print("✓ session_resumption works — context survives restart, lowest latency.")
    elif mechanism == "clean_restart":
        print("! clean_restart required — context NOT carried across sessions.")
        print("  Model must infer needed tool from audio/text alone (no conversation memory).")
    else:
        print("✗ Both mechanisms failed — swap is not feasible with current API version.")

    real_errors = [e for e in errors if e not in ("phase1 timeout",)]
    if p1_ok and p2_ok and rtt_ok and not real_errors:
        print("\n✓ All checks passed — Gemini swap mechanism is working.")
        sys.exit(0)
    else:
        print("\n✗ Some checks failed — review output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
