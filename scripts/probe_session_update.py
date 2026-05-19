"""
Phase 0 risk gate: verify that OpenAI Realtime session.update works mid-session.

Tests:
  1. Connect and configure session with initial tools (camera_basics pool).
  2. Send a text-mode conversation item: "Take a photo."
  3. Request a response → model calls take_photo from core tools.
  4. After response.done, send session.update with lab_imaging pool.
  5. Wait for session.updated ack — measure round-trip latency.
  6. Send another text item: "Switch to the 20x objective."
  7. Request a response → model should call set_microscope_objective from new pool.
  8. Report results.

Usage:
    cd voice-bench && uv run python scripts/probe_session_update.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from dotenv import load_dotenv
load_dotenv()

from openai import AsyncOpenAI
from voice_bench.toolsets import build_core, build_pool, build_visible_tools, TOOLSET_DESCRIPTIONS

DEFAULT_MODEL = os.environ.get("OPENAI_REALTIME_MODEL", "gpt-realtime-2")

SYSTEM_PROMPT = (
    "You are a voice-controlled lab camera assistant. "
    "When the user gives a command, call the appropriate tool. "
    "Do not explain — just call the tool. "
    "For hide/off/disable → boolean false. For show/on/enable → boolean true."
)


def _build_tools(tools) -> list[dict]:
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in tools
    ]


def _serialize(event) -> str:
    try:
        return event.model_dump_json()
    except Exception:
        return str(event)


async def probe() -> dict:
    client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])

    initial_visible = build_visible_tools("camera_basics")
    lab_visible = build_core() + build_pool("lab_imaging")

    print(f"Model: {DEFAULT_MODEL}")
    print(f"Initial tools ({len(initial_visible)}): {[t.name for t in initial_visible]}")
    print(f"Lab pool tools ({len(lab_visible)}): {[t.name for t in lab_visible]}")
    print()

    results: dict = {
        "model": DEFAULT_MODEL,
        "phase1_tool_call": None,
        "session_update_rtt_ms": None,
        "phase2_tool_call": None,
        "errors": [],
    }

    call_id_to_name: dict[str, str] = {}

    async with asyncio.timeout(60.0):
        async with client.realtime.connect(model=DEFAULT_MODEL) as conn:

            # ── Setup ──────────────────────────────────────────────────────
            print("Sending initial session.update...")
            await conn.session.update(session={
                "type": "realtime",
                "instructions": SYSTEM_PROMPT,
                "tools": _build_tools(initial_visible),
                "tool_choice": "auto",
                "output_modalities": ["text"],  # text-only for the probe (no audio needed)
            })

            event_iter = conn.__aiter__()

            # Wait for initial session.updated
            while True:
                ev = await asyncio.wait_for(event_iter.__anext__(), timeout=10.0)
                print(f"  [{ev.type}]")
                if ev.type == "session.updated":
                    print("  ✓ Initial session.updated received")
                    break
                if ev.type == "error":
                    results["errors"].append(f"setup error: {_serialize(ev)}")
                    return results

            # ── Phase 1: send text command ─────────────────────────────────
            print('\nPhase 1: sending "Take a photo."')
            await conn.conversation.item.create(item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Take a photo."}],
            })
            await conn.response.create()

            phase1_done = False
            while not phase1_done:
                ev = await asyncio.wait_for(event_iter.__anext__(), timeout=15.0)
                print(f"  [{ev.type}]", end="")

                if ev.type == "response.output_item.added":
                    item = getattr(ev, "item", None)
                    if item and getattr(item, "type", "") == "function_call":
                        cid = getattr(item, "call_id", None)
                        nm = getattr(item, "name", None)
                        if cid and nm:
                            call_id_to_name[cid] = nm
                            print(f" → function_call: {nm} (call_id={cid})", end="")

                elif ev.type == "response.function_call_arguments.done":
                    call_id = getattr(ev, "call_id", None)
                    name = call_id_to_name.get(call_id or "", "UNKNOWN")
                    args_str = getattr(ev, "arguments", "{}") or "{}"
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}
                    results["phase1_tool_call"] = {"name": name, "args": args}
                    print(f" → DONE: {name}({args})", end="")

                    # Send tool response only — do NOT send response.create yet.
                    # We must wait for the current response.done before starting another
                    # response, otherwise the swap session.update will race with it.
                    await conn.conversation.item.create(item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": '{"result":"ok"}',
                    })

                elif ev.type == "response.done":
                    status = None
                    try:
                        status = ev.response.status
                    except AttributeError:
                        pass
                    print(f" status={status}", end="")
                    if status == "completed":
                        phase1_done = True
                    elif status in ("failed", "cancelled"):
                        results["errors"].append(f"phase1 response {status}")
                        return results

                elif ev.type == "error":
                    results["errors"].append(f"phase1 error: {_serialize(ev)}")
                    return results

                print()

            p1 = results["phase1_tool_call"]
            if p1:
                expected = "take_photo"
                ok = p1["name"] == expected
                print(f"\n  Phase 1 result: {p1['name']}({p1['args']}) — {'PASS ✓' if ok else f'FAIL ✗ (expected {expected})'}")
            else:
                print("\n  Phase 1 result: no tool call — FAIL ✗")

            # ── Mid-session session.update ─────────────────────────────────
            print(f'\nSwapping to lab_imaging pool ({len(lab_visible)} tools)...')
            ts_swap_start = time.time()
            await conn.session.update(session={
                "type": "realtime",
                "tools": _build_tools(lab_visible),
                "tool_choice": "auto",
            })

            # Wait for session.updated ack
            while True:
                ev = await asyncio.wait_for(event_iter.__anext__(), timeout=10.0)
                print(f"  [{ev.type}]")
                if ev.type == "session.updated":
                    rtt = int((time.time() - ts_swap_start) * 1000)
                    results["session_update_rtt_ms"] = rtt
                    print(f"  ✓ session.updated ack received — RTT: {rtt}ms")
                    break
                if ev.type == "error":
                    results["errors"].append(f"swap error: {_serialize(ev)}")
                    return results

            # ── Phase 2: send lab command ──────────────────────────────────
            print('\nPhase 2: sending "Switch to the 20x objective."')
            await conn.conversation.item.create(item={
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Switch to the 20x objective."}],
            })
            await conn.response.create()

            phase2_done = False
            while not phase2_done:
                ev = await asyncio.wait_for(event_iter.__anext__(), timeout=15.0)
                print(f"  [{ev.type}]", end="")

                if ev.type == "response.output_item.added":
                    item = getattr(ev, "item", None)
                    if item and getattr(item, "type", "") == "function_call":
                        cid = getattr(item, "call_id", None)
                        nm = getattr(item, "name", None)
                        if cid and nm:
                            call_id_to_name[cid] = nm
                            print(f" → function_call: {nm}", end="")

                elif ev.type == "response.function_call_arguments.done":
                    call_id = getattr(ev, "call_id", None)
                    name = call_id_to_name.get(call_id or "", "UNKNOWN")
                    args_str = getattr(ev, "arguments", "{}") or "{}"
                    try:
                        args = json.loads(args_str)
                    except Exception:
                        args = {}
                    results["phase2_tool_call"] = {"name": name, "args": args}
                    print(f" → DONE: {name}({args})", end="")

                    await conn.conversation.item.create(item={
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": '{"result":"ok"}',
                    })
                    # No response.create — probe ends after the tool is confirmed

                elif ev.type == "response.done":
                    status = None
                    try:
                        status = ev.response.status
                    except AttributeError:
                        pass
                    print(f" status={status}", end="")
                    if status == "completed":
                        phase2_done = True
                    elif status in ("failed", "cancelled"):
                        results["errors"].append(f"phase2 response {status}")
                        break

                elif ev.type == "error":
                    results["errors"].append(f"phase2 error: {_serialize(ev)}")
                    break

                print()

            p2 = results["phase2_tool_call"]
            if p2:
                expected = "set_microscope_objective"
                ok = p2["name"] == expected
                print(f"\n  Phase 2 result: {p2['name']}({p2['args']}) — {'PASS ✓' if ok else f'FAIL ✗ (expected {expected})'}")
            else:
                print("\n  Phase 2 result: no tool call — FAIL ✗")

    return results


def main():
    try:
        results = asyncio.run(probe())
    except asyncio.TimeoutError:
        print("\nERROR: probe timed out after 60 seconds")
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

    # Determine pass/fail
    p1 = results.get("phase1_tool_call")
    p2 = results.get("phase2_tool_call")
    rtt = results.get("session_update_rtt_ms")
    errors = results.get("errors", [])

    p1_ok = p1 and p1.get("name") == "take_photo"
    p2_ok = p2 and p2.get("name") == "set_microscope_objective"
    rtt_ok = rtt is not None and rtt < 3000  # session.update RTT should be under 3s

    print("\nResults:")
    print(f"  Phase 1 (core tool call):   {'PASS' if p1_ok else 'FAIL'}")
    print(f"  session.update RTT:         {'PASS' if rtt_ok else 'FAIL'} ({rtt}ms)")
    print(f"  Phase 2 (new pool tool):    {'PASS' if p2_ok else 'FAIL'}")
    if errors:
        print(f"  Errors: {errors}")

    if p1_ok and p2_ok and rtt_ok and not errors:
        print("\n✓ All checks passed — session.update mid-session is working correctly.")
        sys.exit(0)
    else:
        print("\n✗ Some checks failed — review output above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
