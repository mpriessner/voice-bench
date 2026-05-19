"""
GeminiLiveSwapAdapter — dynamic tool-pool swap via session restart with optional resumption.

Architecture:
  - Core tools (always loaded): take_photo, toggle_flash, switch_camera,
    start_documentation, switch_toolset, list_toolsets
  - Swappable pool: one of camera_basics / camera_advanced / lab_imaging / lab_data
  - Total visible at any time: ~16-23 tools (well under the 30t confusion cliff)

Gemini Live constraint:
  Gemini has no mid-session tool-list replacement (no session.update equivalent).
  Swap = full session restart.  Optional SessionResumptionConfig carries the
  conversation handle to the new session so context survives.

Swap flow (within a single run_turn call):
  1. Main session: model hears audio, calls switch_toolset(name="...").
  2. We return {"result":"ok"} tool response; model says "switching" + turn_complete.
  3. Main session closes (async with exits normally).
  4. New session opens with new toolset; if resumption_handle available AND not
     fallback_locked, we try SessionResumptionConfig(handle=...).
  5. We send a synthetic text prompt asking the model to confirm readiness.
  6. We drain receive() max 8 s; capture ts_swap_ack at confirmation turn_complete.
  7. ts_swap_session_opened = time new session connected (cross-provider KPI).
  8. Turn ends; self._current_toolset is updated for the next run_turn call.

Circuit breaker:
  If session_resumption fails, self._fallback_locked = True. All subsequent
  swaps in the same benchmark run use clean_restart directly.

Three timing metrics (all on TurnTimeline):
  swap_mechanism_ms  = ts_swap_session_opened - ts_swap_request  (PRIMARY KPI, cross-provider)
  swap_rtt_ms        = ts_swap_ack - ts_swap_request              (user-visible, incl. confirmation)
  swap_ux_delay_ms   = ts_swap_ack - ts_swap_session_opened       (verbal confirmation cost)
"""

import asyncio
import base64
import json
import os
import time
import uuid
from pathlib import Path

from google import genai
from google.genai import types

from ..audio import load_pcm16
from ..models import (
    RawProviderEvent,
    TerminalReason,
    ToolCallEvent,
    TurnResult,
    TurnTimeline,
)
from ..tools import DummyTool
from ..toolsets import TOOLSETS, TOOLSET_DESCRIPTIONS, build_core
from .base import DEFAULT_TIMEOUTS
from ._gemini_common import schema_from_dict

DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
DEFAULT_VOICE = "Kore"
AUDIO_CHUNK_BYTES = 2048

_SWAP_META_TOOLS = {"switch_toolset", "list_toolsets"}

# Extra budget beyond DEFAULT_TIMEOUTS for swap restart + verbal confirmation
_SWAP_EXTRA_BUDGET = 30.0

# After sending the switch_toolset response, wait at most this long for
# turn_complete from the main session. Gemini occasionally goes silent after
# a tool response without ever emitting turn_complete; without this grace
# deadline the main loop would block until the outer budget expires, leaving
# Phase 2 (the swap session) unreached.
_POST_SWAP_TURN_COMPLETE_GRACE = 6.0


class GeminiLiveSwapAdapter:
    """Gemini Live adapter with dynamic tool-pool swap via session restart."""

    REQUIRES_AUDIO = True

    def __init__(
        self,
        toolsets: dict[str, list[DummyTool]] | None = None,
        core_tools: list[DummyTool] | None = None,
        initial_toolset: str = "camera_basics",
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        agent_name: str = "gemini-live-swap",
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
        self.agent_name = agent_name
        self.client = genai.Client(api_key=self.api_key)

        self._toolsets = toolsets if toolsets is not None else TOOLSETS
        self._core_tools = core_tools if core_tools is not None else build_core()
        self._current_toolset = initial_toolset
        if initial_toolset not in self._toolsets:
            raise ValueError(
                f"Unknown initial_toolset {initial_toolset!r}. Valid: {sorted(self._toolsets)}"
            )

        self._resumption_handle: str | None = None
        self._fallback_locked: bool = False

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _visible_tools(self) -> list[DummyTool]:
        pool = self._toolsets.get(self._current_toolset, [])
        return self._core_tools + pool

    def _build_config(
        self,
        tools: list[DummyTool],
        system_prompt: str,
        resumption_handle: str | None = None,
    ) -> dict:
        gemini_tools: list = []
        if tools:
            declarations = [
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description,
                    parameters=schema_from_dict(t.parameters),
                )
                for t in tools
            ]
            gemini_tools = [types.Tool(function_declarations=declarations)]

        config: dict = {
            "response_modalities": ["AUDIO"],
            "system_instruction": system_prompt,
            "tools": gemini_tools,
            "input_audio_transcription": {},
            "output_audio_transcription": {},  # required for AI transcript in AUDIO mode
        }
        if resumption_handle:
            config["session_resumption"] = types.SessionResumptionConfig(
                handle=resumption_handle
            )
        return config

    async def _drain_confirmation(
        self,
        session,
        pending_swap_to: str,
        transcripts: dict[str, str],
        timeline: TurnTimeline,
    ) -> None:
        """Send verbal readiness request, drain up to 8 s, set ts_swap_ack.

        With session_resumption, the model may replay prior tool calls before
        responding to the confirmation prompt.  We respond with {"result":"ok"}
        to any replayed calls so the model doesn't block waiting for a response
        that would otherwise never arrive.
        """
        confirm_prompt = (
            f"You just switched to the {pending_swap_to} toolset. "
            "In one short sentence, tell the user the new tools are ready."
        )
        await session.send_client_content(
            turns={"role": "user", "parts": [{"text": confirm_prompt}]},
            turn_complete=True,
        )
        try:
            async with asyncio.timeout(8.0):
                async for msg in session.receive():
                    sru = getattr(msg, "session_resumption_update", None)
                    if sru and sru.new_handle:
                        self._resumption_handle = sru.new_handle

                    if msg.tool_call:
                        # Unblock replayed tool calls from session_resumption context.
                        for fc in msg.tool_call.function_calls:
                            await session.send_tool_response(
                                function_responses=types.FunctionResponse(
                                    name=fc.name,
                                    response={"result": "ok"},
                                    id=fc.id,
                                )
                            )

                    if msg.server_content:
                        sc = msg.server_content
                        if getattr(sc, "output_transcription", None):
                            transcripts["ai"] += sc.output_transcription.text or ""
                        if msg.text:
                            transcripts["ai"] += msg.text
                        if sc.turn_complete:
                            timeline.ts_swap_ack = time.time()
                            break
        except asyncio.TimeoutError:
            pass

        if timeline.ts_swap_ack is None:
            timeline.ts_swap_ack = time.time()

    # ── Public interface ────────────────────────────────────────────────────────

    async def probe(self) -> dict:
        ts_start = time.time()
        config = self._build_config(tools=[], system_prompt="Probe only.")
        try:
            async with asyncio.timeout(15.0):
                async with self.client.aio.live.connect(
                    model=f"models/{self.model}", config=config
                ) as session:
                    ts_ready = time.time()
                    return {
                        "agent": self.agent_name,
                        "model": self.model,
                        "voice": self.voice,
                        "connect_ms": int((ts_ready - ts_start) * 1000),
                        "current_toolset": self._current_toolset,
                        "status": "ok",
                    }
        except asyncio.TimeoutError:
            return {"agent": self.agent_name, "model": self.model, "status": "timeout"}
        except Exception as e:
            return {
                "agent": self.agent_name, "model": self.model,
                "status": "error", "error": str(e),
            }

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
            agent=self.agent_name,
            prompt_id=prompt_id,
            model_kind="voice_swap",
        )
        tool_calls: list[ToolCallEvent] = []
        raw_events: list[RawProviderEvent] = []
        transcripts: dict[str, str] = {"user": "", "ai": ""}
        terminal_reason = TerminalReason.PROVIDER_ERROR
        seen_call_ids: set[str] = set()
        swap_events: list[dict] = []

        visible = self._visible_tools()
        audio_bytes = load_pcm16(audio_wav_path, target_rate=16000)
        total_budget = (
            t["connect"] + t["first_tool"] + t["quiet"] * 2 + _SWAP_EXTRA_BUDGET
        )

        timeline.ts_connect_start = time.time()
        pending_swap_to: str | None = None

        try:
            async with asyncio.timeout(total_budget):

                # ── Phase 1: Main turn session ─────────────────────────────
                # Main turns always use a clean session — no resumption handle.
                # Resumption context causes the model to replay prior tool calls
                # across consecutive turns, which breaks benchmark accuracy.
                # The resumption handle is used only by the swap session (Phase 2).
                main_config = self._build_config(visible, system_prompt, resumption_handle=None)

                async with self.client.aio.live.connect(
                    model=f"models/{self.model}", config=main_config
                ) as session:
                    timeline.ts_setup_complete = time.time()

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
                    terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL

                    # Manual iteration so we can apply a grace deadline after
                    # the switch_toolset response: Gemini sometimes goes silent
                    # post-tool-response without ever emitting turn_complete,
                    # which would otherwise hang the loop until the outer budget.
                    swap_response_sent_at: float | None = None
                    receive_iter = session.receive().__aiter__()
                    while True:
                        try:
                            if swap_response_sent_at is not None:
                                remaining = (
                                    swap_response_sent_at
                                    + _POST_SWAP_TURN_COMPLETE_GRACE
                                    - time.time()
                                )
                                if remaining <= 0:
                                    break
                                message = await asyncio.wait_for(
                                    receive_iter.__anext__(), timeout=remaining
                                )
                            else:
                                message = await receive_iter.__anext__()
                        except (asyncio.TimeoutError, StopAsyncIteration):
                            break
                        ts = time.time()
                        if timeline.ts_first_event_received is None:
                            timeline.ts_first_event_received = ts

                        sru = getattr(message, "session_resumption_update", None)
                        if sru and sru.new_handle:
                            self._resumption_handle = sru.new_handle

                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=ts, kind="raw_message",
                            payload_json=json.dumps({
                                "has_tool_call": message.tool_call is not None,
                                "has_data": message.data is not None,
                                "has_server_content": message.server_content is not None,
                                "turn_complete": (
                                    message.server_content.turn_complete
                                    if message.server_content else False
                                ),
                            }),
                        ))

                        if message.tool_call:
                            if timeline.ts_first_tool_call_emitted is None:
                                timeline.ts_first_tool_call_emitted = ts

                            for fc in message.tool_call.function_calls:
                                call_id = fc.id or str(uuid.uuid4())
                                args = dict(fc.args or {})

                                if fc.name == "switch_toolset":
                                    toolset_name = args.get("name", "")
                                    if toolset_name in self._toolsets:
                                        pending_swap_to = toolset_name
                                        timeline.ts_swap_request = time.time()
                                        output = {"result": "ok", "switched_to": toolset_name}
                                    else:
                                        output = {
                                            "error": "unknown_toolset",
                                            "available": sorted(self._toolsets),
                                        }
                                    await session.send_tool_response(
                                        function_responses=types.FunctionResponse(
                                            name=fc.name, response=output, id=fc.id,
                                        )
                                    )
                                    if timeline.ts_tool_response_sent is None:
                                        timeline.ts_tool_response_sent = time.time()
                                    # Arm the grace deadline only when the swap
                                    # was actually accepted; an unknown_toolset
                                    # error should let the model keep talking.
                                    if pending_swap_to is not None:
                                        swap_response_sent_at = time.time()

                                elif fc.name == "list_toolsets":
                                    toolset_info = [
                                        {"name": k, "description": v}
                                        for k, v in TOOLSET_DESCRIPTIONS.items()
                                    ]
                                    await session.send_tool_response(
                                        function_responses=types.FunctionResponse(
                                            name=fc.name,
                                            response={"toolsets": toolset_info},
                                            id=fc.id,
                                        )
                                    )

                                else:
                                    if call_id not in seen_call_ids:
                                        seen_call_ids.add(call_id)
                                        tool_calls.append(ToolCallEvent(
                                            turn_id=turn_id,
                                            tool_name=fc.name,
                                            args=args,
                                            call_id=call_id,
                                            ts_called=ts,
                                            toolset_at_call=self._current_toolset,
                                        ))
                                        matching = next(
                                            (t_ for t_ in self._visible_tools() if t_.name == fc.name),
                                            None,
                                        )
                                        if matching:
                                            matching(turn_id=turn_id, **args)

                                    await session.send_tool_response(
                                        function_responses=types.FunctionResponse(
                                            name=fc.name,
                                            response={"result": "ok"},
                                            id=fc.id,
                                        )
                                    )
                                    if timeline.ts_tool_response_sent is None:
                                        timeline.ts_tool_response_sent = time.time()

                        if message.data and timeline.ts_first_output_audio is None:
                            timeline.ts_first_output_audio = ts

                        if message.server_content:
                            sc = message.server_content
                            if getattr(sc, "input_transcription", None):
                                transcripts["user"] += sc.input_transcription.text or ""
                            if getattr(sc, "output_transcription", None):
                                transcripts["ai"] += sc.output_transcription.text or ""
                            if message.text:
                                transcripts["ai"] += message.text

                            if sc.turn_complete:
                                timeline.ts_turn_complete = ts
                                terminal_reason = TerminalReason.TURN_COMPLETE
                                break

                # ── Phase 2: Swap session (if switch_toolset was called) ────
                if pending_swap_to:
                    old_pool = self._current_toolset
                    new_pool_tools = self._toolsets[pending_swap_to]
                    new_visible = self._core_tools + new_pool_tools
                    mechanism_used = "clean_restart"

                    try:
                        # Always use clean_restart for swap sessions.
                        # Session_resumption causes the model to replay prior tool calls
                        # before answering the confirmation prompt, adding 5-11s overhead.
                        # Clean restart is faster (~1-2s total) and more predictable.
                        swap_config = self._build_config(
                            new_visible, system_prompt, resumption_handle=None
                        )

                        async with self.client.aio.live.connect(
                            model=f"models/{self.model}", config=swap_config
                        ) as new_session:
                            self._current_toolset = pending_swap_to
                            timeline.ts_swap_session_opened = time.time()
                            swap_events.append({
                                "from_pool": old_pool,
                                "to_pool": pending_swap_to,
                                "mechanism": mechanism_used,
                            })
                            await self._drain_confirmation(
                                new_session, pending_swap_to, transcripts, timeline
                            )

                    except Exception as exc:
                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=time.time(), kind="swap_error",
                            payload_json=json.dumps({"error": str(exc), "type": type(exc).__name__}),
                        ))

        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            terminal_reason = TerminalReason.PROVIDER_ERROR
            raw_events.append(RawProviderEvent(
                turn_id=turn_id, ts=time.time(), kind="error",
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
            swap_events=swap_events,
        )
