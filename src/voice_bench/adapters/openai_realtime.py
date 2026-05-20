"""
OpenAIRealtimeAdapter — streams a WAV file to OpenAI Realtime, collects tool
calls, sends synthetic tool responses, issues a second response.create for
post-tool audio (TTFS), and returns a fully-timed TurnResult.

Wire format verified against openai>=2.37:
  - SDK namespace: client.realtime.connect()
  - Audio format:  {"type": "audio/pcm"}  (24 kHz PCM16 only)
  - VAD disabled:  audio.input.turn_detection = None
  - Turn flow:     input_audio_buffer.append → commit → response.create
  - Tool response: conversation.item.create (type=function_call_output)
  - TTFS:          second response.create after tool response
"""

import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path

from openai import AsyncOpenAI

from ..audio import load_pcm16
from ..models import (
    RawProviderEvent,
    TerminalReason,
    ToolCallEvent,
    TurnResult,
    TurnTimeline,
)
from ..tools import DummyTool
from .base import DEFAULT_TIMEOUTS

DEFAULT_MODEL = "gpt-realtime-2"
DEFAULT_REASONING_EFFORT = "high"  # gpt-realtime-2 supports reasoning.effort
AUDIO_RATE = 24000
AUDIO_CHUNK_BYTES = AUDIO_RATE * 2 // 10  # ~100ms of 24kHz PCM16 = 4800 bytes


class OpenAIRealtimeAdapter:
    """Adapter for OpenAI native voice Realtime API."""

    REQUIRES_AUDIO = True

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        agent_name: str = "openai-realtime",
        force_tool_call: bool = True,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Set OPENAI_API_KEY environment variable")
        self.model = model or os.environ.get("OPENAI_REALTIME_MODEL", DEFAULT_MODEL)
        self.agent_name = agent_name
        self.force_tool_call = force_tool_call
        self.reasoning_effort = os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT) or None
        self.client = AsyncOpenAI(api_key=self.api_key)

    def _build_tools(self, tools: list[DummyTool]) -> list[dict]:
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    def _session_config(self, tools: list[DummyTool], system_prompt: str) -> dict:
        built_tools = self._build_tools(tools)
        config: dict = {
            "type": "realtime",
            "instructions": system_prompt,
            "tools": built_tools,
            "tool_choice": ("required" if self.force_tool_call else "auto") if built_tools else "none",
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": None,
                    "transcription": {"model": "whisper-1"},  # enables user-turn transcript (Story 4)
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                },
            },
        }
        # reasoning.effort is only supported on gpt-realtime-2 and newer. Skip it
        # for legacy models, which reject the option with "Unsupported option for this model."
        if self.reasoning_effort and "realtime-2" in self.model:
            config["reasoning"] = {"effort": self.reasoning_effort}
        return config

    @staticmethod
    def _serialize(event) -> str:
        try:
            return event.model_dump_json()
        except Exception:
            try:
                return json.dumps(event.model_dump(mode="json"), default=str)
            except Exception:
                return str(event)

    async def probe(self) -> dict:
        """Connect, confirm session.created, disconnect. Returns probe metadata."""
        ts_start = time.time()
        try:
            async with asyncio.timeout(15.0):
                async with self.client.realtime.connect(model=self.model) as conn:
                    async for event in conn:
                        if event.type == "session.created":
                            return {
                                "agent": self.agent_name,
                                "model": self.model,
                                "connect_ms": int((time.time() - ts_start) * 1000),
                                "status": "ok",
                            }
                        if event.type == "error":
                            return {
                                "agent": self.agent_name,
                                "model": self.model,
                                "status": "error",
                                "error": self._serialize(event),
                            }
        except asyncio.TimeoutError:
            return {"agent": self.agent_name, "model": self.model, "status": "timeout"}
        except Exception as e:
            return {"agent": self.agent_name, "model": self.model,
                    "status": "error", "error": str(e)}

    async def run_turn(
        self,
        audio_wav_path: Path | None,
        tools: list[DummyTool],
        system_prompt: str,
        turn_id: str,
        prompt_id: str,
        timeouts: dict | None = None,
        prompt_text: str | None = None,
    ) -> TurnResult:
        t = timeouts or DEFAULT_TIMEOUTS
        timeline = TurnTimeline(turn_id=turn_id, agent=self.agent_name, prompt_id=prompt_id)
        tool_calls: list[ToolCallEvent] = []
        raw_events: list[RawProviderEvent] = []
        transcripts: dict[str, str] = {"user": "", "ai": ""}
        terminal_reason = TerminalReason.PROVIDER_ERROR
        seen_call_ids: set[str] = set()
        malformed_count = 0

        audio_bytes = load_pcm16(audio_wav_path, target_rate=AUDIO_RATE)
        total_budget = t["connect"] + t["first_tool"] + t["quiet"] * 2
        timeline.ts_connect_start = time.time()

        try:
            async with asyncio.timeout(total_budget):
                async with self.client.realtime.connect(model=self.model) as conn:

                    # ── Session setup ─────────────────────────────────────
                    await conn.session.update(session=self._session_config(tools, system_prompt))
                    timeline.ts_setup_complete = time.time()

                    # Wait for session.updated ack
                    event_iter = conn.__aiter__()
                    while True:
                        try:
                            ev = await asyncio.wait_for(event_iter.__anext__(), timeout=t["connect"])
                        except asyncio.TimeoutError:
                            terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL
                            break
                        except StopAsyncIteration:
                            break
                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=time.time(),
                            kind=ev.type, payload_json=self._serialize(ev),
                        ))
                        if ev.type == "session.updated":
                            break
                        if ev.type == "error":
                            terminal_reason = TerminalReason.PROVIDER_ERROR
                            return TurnResult(
                                timeline=timeline, tool_calls=tool_calls,
                                raw_events=raw_events, transcripts=transcripts,
                                terminal_reason=terminal_reason,
                            )
                    else:
                        # Broke out of while True — timeout hit
                        return TurnResult(
                            timeline=timeline, tool_calls=tool_calls,
                            raw_events=raw_events, transcripts=transcripts,
                            terminal_reason=terminal_reason,
                        )

                    # ── Send audio ────────────────────────────────────────
                    timeline.ts_input_audio_start = time.time()
                    offset = 0
                    while offset < len(audio_bytes):
                        chunk = audio_bytes[offset:offset + AUDIO_CHUNK_BYTES]
                        await conn.input_audio_buffer.append(
                            audio=base64.b64encode(chunk).decode()
                        )
                        offset += AUDIO_CHUNK_BYTES
                    await conn.input_audio_buffer.commit()
                    timeline.ts_input_audio_end = time.time()

                    await conn.response.create()

                    # ── Receive loop ──────────────────────────────────────
                    terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL
                    response_count = 0  # counts response.done events received

                    while True:
                        try:
                            ev = await asyncio.wait_for(
                                event_iter.__anext__(), timeout=t["quiet"]
                            )
                        except asyncio.TimeoutError:
                            break
                        except StopAsyncIteration:
                            break

                        ts = time.time()
                        if timeline.ts_first_event_received is None:
                            timeline.ts_first_event_received = ts

                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=ts,
                            kind=ev.type, payload_json=self._serialize(ev),
                        ))

                        # ── Function call timing (delta) ──────────────
                        if ev.type == "response.function_call_arguments.delta":
                            if timeline.ts_first_tool_call_emitted is None:
                                timeline.ts_first_tool_call_emitted = ts

                        # ── Function call complete ─────────────────────
                        elif ev.type == "response.function_call_arguments.done":
                            if timeline.ts_first_tool_call_emitted is None:
                                timeline.ts_first_tool_call_emitted = ts

                            call_id = getattr(ev, "call_id", None) or str(uuid.uuid4())
                            name = getattr(ev, "name", "")
                            raw_args = getattr(ev, "arguments", "") or ""

                            try:
                                args = json.loads(raw_args)
                            except json.JSONDecodeError as exc:
                                raw_events.append(RawProviderEvent(
                                    turn_id=turn_id, ts=ts,
                                    kind="malformed_args",
                                    payload_json=json.dumps({
                                        "call_id": call_id, "name": name,
                                        "raw": raw_args, "error": str(exc),
                                    }),
                                ))
                                malformed_count += 1
                                continue

                            if call_id not in seen_call_ids:
                                seen_call_ids.add(call_id)
                                tool_calls.append(ToolCallEvent(
                                    turn_id=turn_id,
                                    tool_name=name,
                                    args=args,
                                    call_id=call_id,
                                    ts_called=ts,
                                ))
                                matching = next((t_ for t_ in tools if t_.name == name), None)
                                if matching:
                                    matching(turn_id=turn_id, **args)

                            # Send tool response
                            await conn.conversation.item.create(item={
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": '{"result":"ok"}',
                            })
                            if timeline.ts_tool_response_sent is None:
                                timeline.ts_tool_response_sent = time.time()

                            # Second response.create → verbal confirmation + TTFS audio
                            await conn.response.create()

                        # ── Audio output ───────────────────────────────
                        elif ev.type in ("response.audio.delta", "response.output_audio.delta"):
                            if timeline.ts_first_output_audio is None:
                                timeline.ts_first_output_audio = ts

                        # ── Transcripts ────────────────────────────────
                        elif ev.type in (
                            "response.audio_transcript.delta",
                            "response.output_audio_transcript.delta",
                        ):
                            transcripts["ai"] += getattr(ev, "delta", "") or ""

                        elif ev.type == "conversation.item.input_audio_transcription.completed":
                            transcripts["user"] += getattr(ev, "transcript", "") or ""

                        # ── Turn terminal ──────────────────────────────
                        elif ev.type == "response.done":
                            response_count += 1
                            status = None
                            try:
                                status = ev.response.status
                            except AttributeError:
                                pass

                            if status in ("failed", "cancelled"):
                                terminal_reason = TerminalReason.PROVIDER_ERROR
                                timeline.ts_turn_complete = ts
                                break
                            elif status == "completed":
                                timeline.ts_turn_complete = ts
                                terminal_reason = TerminalReason.TURN_COMPLETE
                                # Break after the second response.done (post-tool confirmation)
                                # or immediately if no tool was called
                                if not tool_calls or response_count >= 2:
                                    break
                            # "incomplete" — keep whatever terminal_reason is set, break
                            elif status == "incomplete":
                                break

                        # ── Server error ───────────────────────────────
                        elif ev.type == "error":
                            terminal_reason = TerminalReason.PROVIDER_ERROR
                            break

        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            terminal_reason = TerminalReason.PROVIDER_ERROR
            raw_events.append(RawProviderEvent(
                turn_id=turn_id,
                ts=time.time(),
                kind="error",
                payload_json=json.dumps({"error": str(exc), "type": type(exc).__name__}),
            ))

        if not tool_calls and terminal_reason == TerminalReason.TURN_COMPLETE:
            terminal_reason = TerminalReason.NO_TOOL_CALLED

        return TurnResult(
            timeline=timeline,
            tool_calls=tool_calls,
            raw_events=raw_events,
            transcripts=transcripts,
            terminal_reason=terminal_reason,
        )
