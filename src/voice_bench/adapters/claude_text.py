"""
ClaudeTextAdapter — sends the prompt text to Claude via the Anthropic Messages
API with tool use enabled. No audio involved.

Measures ttf_tool_ms only (single round-trip; no TTFS applicable).
"""

import asyncio
import os
import time
import uuid
from pathlib import Path

import anthropic

from ..models import (
    RawProviderEvent,
    TerminalReason,
    ToolCallEvent,
    TurnResult,
    TurnTimeline,
)
from ..tools import DummyTool
from .base import DEFAULT_TIMEOUTS

DEFAULT_MODEL = "claude-opus-4-7"


class ClaudeTextAdapter:
    """Text-mode adapter using the Anthropic Messages API."""

    REQUIRES_AUDIO = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise ValueError("ANTHROPIC_API_KEY not set")
        self.model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
        self._agent_name = agent_name or "claude-opus"
        self._client = anthropic.Anthropic(api_key=self.api_key)

    def _build_tools(self, tools: list[DummyTool]) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    async def probe(self) -> dict:
        t0 = time.time()
        try:
            msg = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.messages.create(
                    model=self.model,
                    max_tokens=64,
                    messages=[{"role": "user", "content": "ping"}],
                ),
            )
            return {
                "agent": self._agent_name,
                "model": self.model,
                "status": "ok",
                "connect_ms": round((time.time() - t0) * 1000),
            }
        except Exception as e:
            return {"agent": self._agent_name, "model": self.model, "status": "error", "error": str(e)}

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
        if not prompt_text:
            raise ValueError("ClaudeTextAdapter requires prompt_text")

        timeline = TurnTimeline(turn_id=turn_id, agent=self._agent_name, prompt_id=prompt_id, model_kind="text")
        raw_events: list[RawProviderEvent] = []
        tool_calls: list[ToolCallEvent] = []

        t0 = time.time()
        timeline.ts_connect_start = t0
        timeline.ts_setup_complete = t0
        timeline.ts_first_event_received = t0  # request sent

        try:
            async with asyncio.timeout(60.0):
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.messages.create(
                        model=self.model,
                        max_tokens=1024,
                        system=system_prompt,
                        tools=self._build_tools(tools),
                        tool_choice={"type": "auto"},
                        messages=[{"role": "user", "content": prompt_text}],
                    ),
                )
        except asyncio.TimeoutError:
            timeline.ts_turn_complete = time.time()
            return TurnResult(
                timeline=timeline,
                tool_calls=[],
                raw_events=raw_events,
                transcripts={},
                terminal_reason=TerminalReason.TIMEOUT_FIRST_TOOL,
            )
        except Exception as e:
            timeline.ts_turn_complete = time.time()
            raw_events.append(RawProviderEvent(turn_id=turn_id, ts=time.time(), kind="error", payload_json=str(e)))
            return TurnResult(
                timeline=timeline,
                tool_calls=[],
                raw_events=raw_events,
                transcripts={},
                terminal_reason=TerminalReason.PROVIDER_ERROR,
            )

        ts_response = time.time()
        # ts_first_event_received already set to t0 (request-sent time) for ttf_request_to_call_ms

        raw_events.append(RawProviderEvent(
            turn_id=turn_id, ts=ts_response,
            kind="response",
            payload_json=f'{{"stop_reason": "{response.stop_reason}", "model": "{response.model}"}}',
        ))

        for block in response.content:
            if block.type == "tool_use":
                timeline.ts_first_tool_call_emitted = timeline.ts_first_tool_call_emitted or ts_response
                tool_calls.append(ToolCallEvent(
                    turn_id=turn_id,
                    tool_name=block.name,
                    args=dict(block.input) if block.input else {},
                    call_id=block.id,
                    ts_called=ts_response,
                ))
            elif block.type == "text":
                raw_events.append(RawProviderEvent(turn_id=turn_id, ts=time.time(), kind="text", payload_json=block.text[:200]))

        timeline.ts_turn_complete = ts_response
        term = TerminalReason.TURN_COMPLETE if tool_calls else TerminalReason.NO_TOOL_CALLED

        return TurnResult(
            timeline=timeline,
            tool_calls=tool_calls,
            raw_events=raw_events,
            transcripts={},
            terminal_reason=term,
        )
