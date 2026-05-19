"""
OpenAIRealtimeSwapAdapter — dynamic tool-pool swap via session.update.

Architecture:
  - Core tools (always loaded): take_photo, toggle_flash, switch_camera,
    start_documentation, switch_toolset, list_toolsets
  - Swappable pool: one of camera_basics / camera_advanced / lab_imaging / lab_data
  - Total visible at any time: ~16-23 tools (well under the 30t confusion cliff)

Key SDK invariant (OpenAI Realtime):
  - response.function_call_arguments.done does NOT carry the function name.
  - Track name from response.output_item.added (item.type == "function_call")
    keyed by item.call_id, then look up at arguments.done time.

Swap flow (within a single run_turn call):
  1. Model calls switch_toolset(name="...") in a response.
  2. We send function_call_output only — no response.create (avoids racing a new
     response with the pending session.update).
  3. response.done fires (status="completed") — no active response remains.
  4. We send session.update with the new tools list.
  5. We drain events until session.updated arrives and record the RTT.
  6. Turn ends; self._current_toolset is updated for the next run_turn call.
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
from ..toolsets import TOOLSETS, TOOLSET_DESCRIPTIONS, build_core, build_visible_tools
from .base import DEFAULT_TIMEOUTS

DEFAULT_MODEL = "gpt-realtime-2"
DEFAULT_REASONING_EFFORT = "high"
AUDIO_RATE = 24000
AUDIO_CHUNK_BYTES = AUDIO_RATE * 2 // 10  # ~100ms chunks

_SWAP_META_TOOLS = {"switch_toolset", "list_toolsets"}


class OpenAIRealtimeSwapAdapter:
    """Voice adapter with dynamic tool-pool swap via session.update."""

    REQUIRES_AUDIO = True

    def __init__(
        self,
        toolsets: dict[str, list[DummyTool]] | None = None,
        core_tools: list[DummyTool] | None = None,
        initial_toolset: str = "camera_basics",
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Set OPENAI_API_KEY environment variable")
        self.model = model or os.environ.get("OPENAI_REALTIME_MODEL", DEFAULT_MODEL)
        self.reasoning_effort = (
            os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_REASONING_EFFORT) or None
        )
        self.client = AsyncOpenAI(api_key=self.api_key)

        self._toolsets = toolsets if toolsets is not None else TOOLSETS
        self._core_tools = core_tools if core_tools is not None else build_core()
        self._current_toolset = initial_toolset
        if initial_toolset not in self._toolsets:
            raise ValueError(
                f"Unknown initial_toolset {initial_toolset!r}. Valid: {sorted(self._toolsets)}"
            )

    # ── Helpers (same pattern as OpenAIRealtimeAdapter) ───────────────────────

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
        built = self._build_tools(tools)
        config: dict = {
            "type": "realtime",
            "instructions": system_prompt,
            "tools": built,
            "tool_choice": "auto",  # required so model can choose swap vs task
            "output_modalities": ["audio"],
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "turn_detection": None,
                    "transcription": {"model": "whisper-1"},
                },
                "output": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                },
            },
        }
        if self.reasoning_effort:
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

    def _visible_tools(self) -> list[DummyTool]:
        pool = self._toolsets.get(self._current_toolset, [])
        return self._core_tools + pool

    # ── Public interface ──────────────────────────────────────────────────────

    async def probe(self) -> dict:
        ts_start = time.time()
        try:
            async with asyncio.timeout(15.0):
                async with self.client.realtime.connect(model=self.model) as conn:
                    async for event in conn:
                        if event.type == "session.created":
                            return {
                                "agent": "openai-realtime-swap",
                                "model": self.model,
                                "connect_ms": int((time.time() - ts_start) * 1000),
                                "current_toolset": self._current_toolset,
                                "status": "ok",
                            }
                        if event.type == "error":
                            return {
                                "agent": "openai-realtime-swap",
                                "model": self.model,
                                "status": "error",
                                "error": self._serialize(event),
                            }
        except asyncio.TimeoutError:
            return {"agent": "openai-realtime-swap", "model": self.model, "status": "timeout"}
        except Exception as e:
            return {"agent": "openai-realtime-swap", "model": self.model,
                    "status": "error", "error": str(e)}

    async def run_turn(
        self,
        audio_wav_path: Path | None,
        tools: list[DummyTool],  # ignored — adapter uses self._visible_tools()
        system_prompt: str,
        turn_id: str,
        prompt_id: str,
        timeouts: dict | None = None,
        prompt_text: str | None = None,
    ) -> TurnResult:
        t = timeouts or DEFAULT_TIMEOUTS
        timeline = TurnTimeline(
            turn_id=turn_id,
            agent="openai-realtime-swap",
            prompt_id=prompt_id,
            model_kind="voice_swap",
        )
        tool_calls: list[ToolCallEvent] = []
        raw_events: list[RawProviderEvent] = []
        transcripts: dict[str, str] = {"user": "", "ai": ""}
        terminal_reason = TerminalReason.PROVIDER_ERROR

        # Per-turn state
        call_id_to_name: dict[str, str] = {}
        seen_call_ids: set[str] = set()
        pending_swap_name: str | None = None
        waiting_for_swap_ack = False
        response_count = 0

        initial_tools = self._visible_tools()
        audio_bytes = load_pcm16(audio_wav_path, target_rate=AUDIO_RATE)
        total_budget = t["connect"] + t["first_tool"] + t["quiet"] * 3 + 5.0  # extra for swap RTT
        timeline.ts_connect_start = time.time()

        try:
            async with asyncio.timeout(total_budget):
                async with self.client.realtime.connect(model=self.model) as conn:

                    # ── Session setup ──────────────────────────────────────
                    await conn.session.update(
                        session=self._session_config(initial_tools, system_prompt)
                    )
                    timeline.ts_setup_complete = time.time()

                    event_iter = conn.__aiter__()

                    # Wait for initial session.updated ack
                    while True:
                        try:
                            ev = await asyncio.wait_for(
                                event_iter.__anext__(), timeout=t["connect"]
                            )
                        except asyncio.TimeoutError:
                            terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL
                            return TurnResult(
                                timeline=timeline, tool_calls=tool_calls,
                                raw_events=raw_events, transcripts=transcripts,
                                terminal_reason=terminal_reason,
                            )
                        except StopAsyncIteration:
                            return TurnResult(
                                timeline=timeline, tool_calls=tool_calls,
                                raw_events=raw_events, transcripts=transcripts,
                                terminal_reason=terminal_reason,
                            )
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

                    # ── Send audio ─────────────────────────────────────────
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

                    # ── Main event loop ────────────────────────────────────
                    terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL

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

                        # ── Track function name from output_item ───────
                        if ev.type == "response.output_item.added":
                            item = getattr(ev, "item", None)
                            if item and getattr(item, "type", "") == "function_call":
                                cid = getattr(item, "call_id", None)
                                nm = getattr(item, "name", None)
                                if cid and nm:
                                    call_id_to_name[cid] = nm

                        # ── Function call timing (first delta) ─────────
                        elif ev.type == "response.function_call_arguments.delta":
                            if timeline.ts_first_tool_call_emitted is None:
                                timeline.ts_first_tool_call_emitted = ts

                        # ── Function call complete ──────────────────────
                        elif ev.type == "response.function_call_arguments.done":
                            if timeline.ts_first_tool_call_emitted is None:
                                timeline.ts_first_tool_call_emitted = ts

                            call_id = getattr(ev, "call_id", None) or str(uuid.uuid4())
                            name = call_id_to_name.get(call_id, "")
                            raw_args = getattr(ev, "arguments", "") or ""

                            try:
                                args = json.loads(raw_args) if raw_args else {}
                            except json.JSONDecodeError:
                                args = {}

                            if call_id in seen_call_ids:
                                continue
                            seen_call_ids.add(call_id)

                            if name == "switch_toolset":
                                toolset_name = args.get("name", "")
                                if toolset_name in self._toolsets:
                                    pending_swap_name = toolset_name
                                    output = json.dumps({
                                        "result": "ok",
                                        "switched_to": toolset_name,
                                    })
                                else:
                                    output = json.dumps({
                                        "error": "unknown_toolset",
                                        "available": sorted(self._toolsets),
                                    })
                                # Send tool response only — do NOT call response.create here.
                                # Must wait for the current response.done before the next
                                # response, otherwise session.update races with an active response.
                                await conn.conversation.item.create(item={
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": output,
                                })

                            elif name == "list_toolsets":
                                toolset_info = [
                                    {"name": k, "description": v}
                                    for k, v in TOOLSET_DESCRIPTIONS.items()
                                ]
                                # Silent meta call — no response.create needed
                                await conn.conversation.item.create(item={
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps({"toolsets": toolset_info}),
                                })

                            else:
                                # Regular task tool call
                                tool_calls.append(ToolCallEvent(
                                    turn_id=turn_id,
                                    tool_name=name,
                                    args=args,
                                    call_id=call_id,
                                    ts_called=ts,
                                    toolset_at_call=self._current_toolset,
                                ))
                                matching = next(
                                    (t_ for t_ in self._visible_tools() if t_.name == name),
                                    None,
                                )
                                if matching:
                                    matching(turn_id=turn_id, **args)

                                await conn.conversation.item.create(item={
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": '{"result":"ok"}',
                                })
                                if timeline.ts_tool_response_sent is None:
                                    timeline.ts_tool_response_sent = time.time()
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

                        # ── Response complete ──────────────────────────
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
                                if pending_swap_name is not None:
                                    # Build new visible tools and send session.update
                                    new_pool = self._toolsets[pending_swap_name]
                                    new_visible = self._core_tools + new_pool
                                    self._current_toolset = pending_swap_name
                                    pending_swap_name = None

                                    timeline.ts_swap_request = time.time()
                                    await conn.session.update(session={
                                        "type": "realtime",
                                        "tools": self._build_tools(new_visible),
                                        "tool_choice": "auto",
                                    })
                                    waiting_for_swap_ack = True
                                    # Don't break — wait for session.updated
                                else:
                                    timeline.ts_turn_complete = ts
                                    terminal_reason = TerminalReason.TURN_COMPLETE
                                    if not tool_calls or response_count >= 2:
                                        break

                            elif status == "incomplete":
                                break

                        # ── Swap ack ───────────────────────────────────
                        elif ev.type == "session.updated":
                            if waiting_for_swap_ack:
                                timeline.ts_swap_ack = time.time()
                                waiting_for_swap_ack = False
                                # Swap complete — end turn
                                timeline.ts_turn_complete = time.time()
                                terminal_reason = TerminalReason.TURN_COMPLETE
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
