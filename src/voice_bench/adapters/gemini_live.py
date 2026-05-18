"""
GeminiLiveAdapter — streams a WAV file to Gemini Live, collects tool calls,
sends synthetic tool responses, and returns a fully-timed TurnResult.

Wire format verified against SciSymbioLens-Android GeminiLiveWebSocket.kt.
Uses the google-genai Python SDK (>= 1.0) with client.aio.live.connect.
"""

import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf
from google import genai
from google.genai import types

from ..models import (
    RawProviderEvent,
    TerminalReason,
    ToolCallEvent,
    TurnResult,
    TurnTimeline,
)
from ..tools import DummyTool
from .base import DEFAULT_TIMEOUTS

# SciSymbioLens-Android GeminiLiveWebSocket.kt:45 — confirmed working model.
DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Kore"
AUDIO_CHUNK_BYTES = 2048  # ~64ms of 16kHz PCM16 mono


def _load_pcm16_16k(wav_path: Path) -> bytes:
    """Read a WAV file and return raw PCM16 bytes at 16 kHz mono.

    Resamples if the source rate differs. Provider-specific rates (e.g. OpenAI
    expects 24 kHz) are handled at the caller level for other adapters.
    """
    data, src_rate = sf.read(str(wav_path), dtype="int16", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1).astype(np.int16)

    if src_rate != 16000:
        from fractions import Fraction
        from scipy.signal import resample_poly

        ratio = Fraction(16000, src_rate).limit_denominator(100)
        data = resample_poly(data.astype(np.float32), ratio.numerator, ratio.denominator)
        data = np.clip(data, -32768, 32767).astype(np.int16)

    return data.tobytes()


class GeminiLiveAdapter:
    """Adapter for Gemini Live native voice model."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self.api_key:
            raise ValueError("Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable")
        self.model = model or os.environ.get("GEMINI_LIVE_MODEL", DEFAULT_MODEL)
        self.voice = voice or os.environ.get("GEMINI_VOICE", DEFAULT_VOICE)
        self.client = genai.Client(api_key=self.api_key)

    @staticmethod
    def _schema_from_dict(d: dict) -> types.Schema:
        """Recursively convert a JSON Schema dict to a types.Schema object."""
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
            kwargs["enum"] = d["enum"]
        if "properties" in d:
            kwargs["properties"] = {
                k: GeminiLiveAdapter._schema_from_dict(v)
                for k, v in d["properties"].items()
            }
        if "required" in d:
            kwargs["required"] = d["required"]
        if "items" in d:
            kwargs["items"] = GeminiLiveAdapter._schema_from_dict(d["items"])
        return types.Schema(**kwargs)

    def _build_config(
        self, tools: list[DummyTool], system_prompt: str
    ) -> dict:
        gemini_tools: list = []
        if tools:
            declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=self._schema_from_dict(t.parameters),
                )
                for t in tools
            ]
            gemini_tools = [types.Tool(function_declarations=declarations)]

        # AUDIO modality required — gemini-3.1-flash-live-preview rejects TEXT
        # with a 1011 WebSocket error. We score tool calls, not audio output;
        # any audio chunks received are logged but ignored for scoring.
        return {
            "response_modalities": ["AUDIO"],
            "system_instruction": system_prompt,
            "tools": gemini_tools,
        }

    async def probe(self) -> dict:
        """Connect, confirm setup-complete, disconnect. Returns probe metadata."""
        ts_start = time.time()
        config = self._build_config(tools=[], system_prompt="Probe only.")
        try:
            async with asyncio.timeout(15.0):
                async with self.client.aio.live.connect(
                    model=f"models/{self.model}", config=config
                ) as session:
                    ts_ready = time.time()
                    return {
                        "agent": "gemini-live",
                        "model": self.model,
                        "voice": self.voice,
                        "connect_ms": int((ts_ready - ts_start) * 1000),
                        "status": "ok",
                    }
        except asyncio.TimeoutError:
            return {"agent": "gemini-live", "model": self.model, "status": "timeout"}
        except Exception as e:
            return {"agent": "gemini-live", "model": self.model, "status": "error", "error": str(e)}

    async def run_turn(
        self,
        audio_wav_path: Path,
        tools: list[DummyTool],
        system_prompt: str,
        turn_id: str,
        prompt_id: str,
        timeouts: dict | None = None,
    ) -> TurnResult:
        t = timeouts or DEFAULT_TIMEOUTS
        timeline = TurnTimeline(turn_id=turn_id, agent="gemini-live", prompt_id=prompt_id)
        tool_calls: list[ToolCallEvent] = []
        raw_events: list[RawProviderEvent] = []
        transcripts: dict[str, str] = {"user": "", "ai": ""}
        terminal_reason = TerminalReason.PROVIDER_ERROR
        seen_call_ids: set[str] = set()

        config = self._build_config(tools, system_prompt)
        audio_bytes = _load_pcm16_16k(audio_wav_path)

        timeline.ts_connect_start = time.time()

        try:
            total_budget = t["connect"] + t["first_tool"] + t["quiet"]
            async with asyncio.timeout(total_budget):
                async with self.client.aio.live.connect(
                    model=f"models/{self.model}", config=config
                ) as session:
                    timeline.ts_setup_complete = time.time()

                    # ── Send audio as a complete client turn ──────────────
                    # send_client_content with turn_complete=True gives explicit
                    # turn control — no VAD needed. send_realtime_input relies on
                    # server-side VAD which silently discards pre-recorded audio.
                    timeline.ts_input_audio_start = time.time()
                    await session.send_client_content(
                        turns={
                            "role": "user",
                            "parts": [{
                                "inline_data": {
                                    "mime_type": "audio/pcm;rate=16000",
                                    "data": base64.b64encode(audio_bytes).decode(),
                                }
                            }],
                        },
                        turn_complete=True,
                    )
                    timeline.ts_input_audio_end = time.time()

                    # ── Receive loop ──────────────────────────────────────
                    last_event_ts = time.time()
                    terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL

                    async for message in session.receive():
                        ts = time.time()
                        last_event_ts = ts

                        if timeline.ts_first_event_received is None:
                            timeline.ts_first_event_received = ts

                        # Log every message for debugging
                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=ts, kind="raw_message",
                            payload_json=json.dumps({
                                "has_tool_call": message.tool_call is not None,
                                "has_data": message.data is not None,
                                "has_text": message.text is not None,
                                "has_server_content": message.server_content is not None,
                                "turn_complete": (
                                    message.server_content.turn_complete
                                    if message.server_content else False
                                ),
                            }),
                        ))

                        # ── Tool call ─────────────────────────────────
                        if message.tool_call:
                            if timeline.ts_first_tool_call_emitted is None:
                                timeline.ts_first_tool_call_emitted = ts

                            for fc in message.tool_call.function_calls:
                                raw_events.append(
                                    RawProviderEvent(
                                        turn_id=turn_id,
                                        ts=ts,
                                        kind="tool_call",
                                        payload_json=json.dumps(
                                            {
                                                "name": fc.name,
                                                "id": fc.id,
                                                "args": dict(fc.args or {}),
                                            }
                                        ),
                                    )
                                )

                                call_id = fc.id or str(uuid.uuid4())
                                if call_id not in seen_call_ids:
                                    seen_call_ids.add(call_id)
                                    args = dict(fc.args or {})
                                    tool_calls.append(
                                        ToolCallEvent(
                                            turn_id=turn_id,
                                            tool_name=fc.name,
                                            args=args,
                                            call_id=call_id,
                                            ts_called=ts,
                                        )
                                    )
                                    matching = next(
                                        (t_ for t_ in tools if t_.name == fc.name), None
                                    )
                                    if matching:
                                        matching(turn_id=turn_id, **args)

                                # Always send tool response (even for dupes — Gemini expects it)
                                await session.send_tool_response(
                                    function_responses=types.FunctionResponse(
                                        name=fc.name,
                                        response={"result": "ok"},
                                        id=fc.id,
                                    )
                                )
                                if timeline.ts_tool_response_sent is None:
                                    timeline.ts_tool_response_sent = time.time()

                        # ── Audio response ─────────────────────────────
                        if message.data and timeline.ts_first_output_audio is None:
                            timeline.ts_first_output_audio = ts
                            raw_events.append(
                                RawProviderEvent(
                                    turn_id=turn_id,
                                    ts=ts,
                                    kind="first_audio",
                                    payload_json=json.dumps({"bytes": len(message.data)}),
                                )
                            )

                        # ── Transcripts ────────────────────────────────
                        if message.server_content:
                            sc = message.server_content
                            if getattr(sc, "input_transcription", None):
                                transcripts["user"] += sc.input_transcription.text or ""
                            if message.text:
                                transcripts["ai"] += message.text

                            if sc.turn_complete:
                                timeline.ts_turn_complete = ts
                                terminal_reason = TerminalReason.TURN_COMPLETE
                                break

                        # ── Quiet timeout ──────────────────────────────
                        if time.time() - last_event_ts > t["quiet"]:
                            terminal_reason = TerminalReason.TURN_COMPLETE
                            break

        except asyncio.TimeoutError:
            pass  # terminal_reason reflects whichever phase we were in
        except Exception as exc:
            terminal_reason = TerminalReason.PROVIDER_ERROR
            raw_events.append(
                RawProviderEvent(
                    turn_id=turn_id,
                    ts=time.time(),
                    kind="error",
                    payload_json=json.dumps({"error": str(exc), "type": type(exc).__name__}),
                )
            )

        if not tool_calls and terminal_reason == TerminalReason.TURN_COMPLETE:
            terminal_reason = TerminalReason.NO_TOOL_CALLED

        return TurnResult(
            timeline=timeline,
            tool_calls=tool_calls,
            raw_events=raw_events,
            transcripts=transcripts,
            terminal_reason=terminal_reason,
        )
