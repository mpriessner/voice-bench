"""
GeminiTextAdapter — sends the prompt text to a Gemini generate_content model
with tool use enabled. No audio involved.

Measures ttf_request_to_call_ms (single round-trip; no TTFS applicable).
"""

import asyncio
import json
import os
import time
from pathlib import Path

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
from ._gemini_common import schema_from_dict

DEFAULT_MODEL = "gemini-2.5-pro"


class GeminiTextAdapter:
    """Text-mode adapter using the Gemini generate_content API."""

    REQUIRES_AUDIO = False

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        self.api_key = (
            api_key
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("GOOGLE_API_KEY")
        )
        if not self.api_key:
            raise ValueError("Set GEMINI_API_KEY or GOOGLE_API_KEY environment variable")
        self.model = model or os.environ.get("GEMINI_TEXT_MODEL", DEFAULT_MODEL)
        self._agent_name = agent_name or "gemini-pro"
        self._client = genai.Client(api_key=self.api_key)

    def _build_tools(self, tools: list[DummyTool]) -> list[types.Tool]:
        if not tools:
            return []
        declarations = [
            types.FunctionDeclaration(
                name=t.name,
                description=t.description,
                parameters=schema_from_dict(t.parameters),
            )
            for t in tools
        ]
        return [types.Tool(function_declarations=declarations)]

    async def probe(self) -> dict:
        t0 = time.time()
        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.models.generate_content(
                    model=self.model,
                    contents="ping",
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
            raise ValueError("GeminiTextAdapter requires prompt_text")

        timeline = TurnTimeline(turn_id=turn_id, agent=self._agent_name, prompt_id=prompt_id, model_kind="text")
        raw_events: list[RawProviderEvent] = []
        tool_calls: list[ToolCallEvent] = []

        t0 = time.time()
        timeline.ts_connect_start = t0
        timeline.ts_setup_complete = t0
        timeline.ts_first_event_received = t0  # request sent

        gemini_tools = self._build_tools(tools)
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=gemini_tools,
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )

        try:
            async with asyncio.timeout(60.0):
                response = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._client.models.generate_content(
                        model=self.model,
                        contents=prompt_text,
                        config=config,
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
            raw_events.append(RawProviderEvent(
                turn_id=turn_id, ts=time.time(), kind="error",
                payload_json=json.dumps({"error": str(e), "type": type(e).__name__}),
            ))
            return TurnResult(
                timeline=timeline,
                tool_calls=[],
                raw_events=raw_events,
                transcripts={},
                terminal_reason=TerminalReason.PROVIDER_ERROR,
            )

        ts_response = time.time()

        # Check for prompt block / safety filter
        block_reason = None
        if response.prompt_feedback and getattr(response.prompt_feedback, "block_reason", None):
            block_reason = str(response.prompt_feedback.block_reason)

        finish_reason = None
        if response.candidates:
            finish_reason = str(getattr(response.candidates[0], "finish_reason", None))

        raw_events.append(RawProviderEvent(
            turn_id=turn_id, ts=ts_response,
            kind="response",
            payload_json=json.dumps({
                "model": self.model,
                "finish_reason": finish_reason,
                "block_reason": block_reason,
            }),
        ))

        # Extract function calls from all candidates/parts
        if response.candidates:
            for candidate in response.candidates:
                if not candidate.content or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    if part.function_call:
                        fc = part.function_call
                        if timeline.ts_first_tool_call_emitted is None:
                            timeline.ts_first_tool_call_emitted = ts_response
                        args = dict(fc.args) if fc.args else {}
                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=ts_response,
                            kind="tool_call",
                            payload_json=json.dumps({"name": fc.name, "args": args}),
                        ))
                        tool_calls.append(ToolCallEvent(
                            turn_id=turn_id,
                            tool_name=fc.name,
                            args=args,
                            call_id=f"{turn_id}-{len(tool_calls)}",
                            ts_called=ts_response,
                        ))
                    elif part.text:
                        raw_events.append(RawProviderEvent(
                            turn_id=turn_id, ts=ts_response, kind="text",
                            payload_json=part.text[:200],
                        ))

        timeline.ts_turn_complete = ts_response
        term = TerminalReason.TURN_COMPLETE if tool_calls else TerminalReason.NO_TOOL_CALLED

        return TurnResult(
            timeline=timeline,
            tool_calls=tool_calls,
            raw_events=raw_events,
            transcripts={},
            terminal_reason=term,
        )
