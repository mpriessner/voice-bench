"""
Standalone probe for the OpenAI Realtime API wire format.

Verifies: SDK namespace, session.update shape, audio buffer flow,
tool-call event sequence, conversation.item.create, response.done.

Usage:
    OPENAI_API_KEY=sk-... uv run python scripts/probe_openai_realtime.py
"""

import asyncio
import base64
import json
import os
import sys
import time
from pathlib import Path

# ── Locate project root and add to path ──────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import numpy as np
import soundfile as sf


def _load_pcm16_24k(wav_path: Path) -> bytes:
    """Load WAV, resample to 24kHz mono PCM16."""
    data, src_rate = sf.read(str(wav_path), dtype="int16", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)
    if src_rate != 24000:
        from fractions import Fraction
        from scipy.signal import resample_poly
        ratio = Fraction(24000, src_rate).limit_denominator(100)
        data = resample_poly(data.astype(np.float32), ratio.numerator, ratio.denominator)
        data = np.clip(data, -32768, 32767).astype(np.int16)
    return data.tobytes()


AUDIO_WAV = ROOT / "prompts" / "audio" / "say" / "p001.wav"
TOOL_DEF = {
    "type": "function",
    "name": "toggle_flash",
    "description": "Turn the camera flash on or off.",
    "parameters": {
        "type": "object",
        "properties": {
            "on": {"type": "boolean", "description": "True to turn flash on, false to turn off."}
        },
        "required": ["on"],
    },
}


async def probe() -> None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)

    # Verify SDK namespace
    has_realtime = hasattr(client, "realtime")
    has_beta_realtime = hasattr(getattr(client, "beta", None), "realtime")
    print(f"SDK: openai {__import__('openai').__version__}")
    print(f"client.realtime: {has_realtime}")
    print(f"client.beta.realtime: {has_beta_realtime}")
    ns = client.realtime if has_realtime else client.beta.realtime
    print(f"Using namespace: {'client.realtime' if has_realtime else 'client.beta.realtime'}")
    print()

    if not AUDIO_WAV.exists():
        print(f"ERROR: audio fixture missing: {AUDIO_WAV}")
        print("Run: voice-bench gen-audio")
        sys.exit(1)

    audio_bytes = _load_pcm16_24k(AUDIO_WAV)
    print(f"Audio: {len(audio_bytes)} bytes at 24kHz PCM16")

    CHUNK = 24000 * 2 // 10  # 100ms chunks
    ts_start = time.time()

    try:
        async with asyncio.timeout(30.0):
            async with ns.connect(model="gpt-realtime") as conn:
                print("Connected.")

                # ── Session update ─────────────────────────────────────────
                await conn.session.update(session={
                    "type": "realtime",
                    "instructions": (
                        "You are a voice assistant for SciSymbioLens. "
                        "When the user gives a command, call the appropriate tool immediately. "
                        "After calling the tool, confirm in one short sentence."
                    ),
                    "tools": [TOOL_DEF],
                    "output_modalities": ["audio"],
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                            "turn_detection": None,
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": 24000},
                        },
                    },
                })
                print("session.update sent")

                # Wait for session.updated ack before sending audio
                async for ev in conn:
                    print(f"  -> {ev.type}")
                    if ev.type == "session.updated":
                        break
                    if ev.type == "error":
                        print(f"ERROR from server: {ev}")
                        return

                # ── Audio buffer ───────────────────────────────────────────
                ts_audio_start = time.time()
                offset = 0
                while offset < len(audio_bytes):
                    chunk = audio_bytes[offset:offset + CHUNK]
                    await conn.input_audio_buffer.append(audio=base64.b64encode(chunk).decode())
                    offset += CHUNK
                await conn.input_audio_buffer.commit()
                print(f"Audio sent: {len(audio_bytes)} bytes in {offset // CHUNK} chunks")

                await conn.response.create()
                print("response.create sent")
                print()

                # ── Event loop ─────────────────────────────────────────────
                function_call_args = ""
                function_call_done = None
                response_done = None

                async for ev in conn:
                    try:
                        payload = ev.model_dump(mode="json")
                    except Exception:
                        payload = str(ev)
                    print(f"[{ev.type}] {json.dumps(payload, default=str)[:200]}")

                    if ev.type == "response.function_call_arguments.delta":
                        function_call_args += ev.delta or ""

                    elif ev.type == "response.function_call_arguments.done":
                        function_call_done = ev
                        print(f"  => name={ev.name}  call_id={ev.call_id}")
                        print(f"  => arguments={ev.arguments!r}")
                        try:
                            parsed = json.loads(ev.arguments)
                            print(f"  => parsed args: {parsed}")
                        except json.JSONDecodeError as e:
                            print(f"  => JSON PARSE ERROR: {e}")

                        # Send tool response
                        await conn.conversation.item.create(item={
                            "type": "function_call_output",
                            "call_id": ev.call_id,
                            "output": '{"result":"ok"}',
                        })
                        print("  => conversation.item.create (function_call_output) sent")

                        # Second response.create for verbal confirmation + TTFS
                        await conn.response.create()
                        print("  => second response.create sent")

                    elif ev.type == "response.done":
                        response_done = ev
                        status = getattr(getattr(ev, "response", None), "status", "?")
                        print(f"  => response.done status={status}")
                        if function_call_done is not None:
                            # Only break after we've seen a tool call + the second response.done
                            if status in ("completed", "failed", "cancelled"):
                                break
                        else:
                            if status in ("completed", "failed", "cancelled"):
                                break

                    elif ev.type == "error":
                        print(f"  => ERROR: {ev}")
                        break

                print()
                print(f"Total elapsed: {time.time() - ts_start:.2f}s")
                if function_call_done:
                    print(f"PASS: tool call received — name={function_call_done.name}")
                else:
                    print("FAIL: no tool call received")

    except asyncio.TimeoutError:
        print("TIMEOUT after 30s")
    except Exception as e:
        print(f"EXCEPTION: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(probe())
