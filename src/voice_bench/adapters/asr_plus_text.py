"""
AsrPlusTextAdapter — Whisper-1 ASR → text adapter pipeline.

Baseline for the two-layer benchmark: transcribes the WAV with Whisper-1,
then sends the transcript to a text adapter (e.g. claude-opus, gpt-4o).
Measures ASR latency separately from text-model latency.

This answers: "How much accuracy and latency does full voice→tool routing
cost vs. a simpler ASR + text-model approach at 30 tools?"
"""

import asyncio
import json
import os
import time
from pathlib import Path

from openai import AsyncOpenAI, OpenAI

from ..models import (
    RawProviderEvent,
    TerminalReason,
    ToolCallEvent,
    TurnResult,
    TurnTimeline,
)
from ..tools import DummyTool


class AsrPlusTextAdapter:
    """Whisper-1 ASR → wrapped text adapter. REQUIRES_AUDIO = True."""

    REQUIRES_AUDIO = True

    def __init__(
        self,
        text_adapter,
        api_key: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        self._text_adapter = text_adapter
        api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY required for Whisper-1 ASR")
        self._asr_client = OpenAI(api_key=api_key)
        text_agent = getattr(text_adapter, "_agent_name", "text")
        self._agent_name = agent_name or f"asr+{text_agent}"

    async def probe(self) -> dict:
        t0 = time.time()
        inner = await self._text_adapter.probe()
        return {
            "agent": self._agent_name,
            "asr": "whisper-1",
            "text_agent": inner.get("agent"),
            "status": inner.get("status", "error"),
            "connect_ms": round((time.time() - t0) * 1000),
        }

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
        if not audio_wav_path or not audio_wav_path.exists():
            raise ValueError(f"AsrPlusTextAdapter requires audio_wav_path, got {audio_wav_path}")

        timeline = TurnTimeline(
            turn_id=turn_id, agent=self._agent_name,
            prompt_id=prompt_id, model_kind="text",
        )
        raw_events: list[RawProviderEvent] = []

        t0 = time.time()
        timeline.ts_connect_start = t0

        # ── Whisper-1 ASR ─────────────────────────────────────────────────────
        ts_asr_start = time.time()
        try:
            async with asyncio.timeout(30.0):
                with open(audio_wav_path, "rb") as af:
                    transcript_resp = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._asr_client.audio.transcriptions.create(
                            model="whisper-1",
                            file=af,
                            response_format="text",
                        ),
                    )
            transcript = str(transcript_resp).strip()
        except asyncio.TimeoutError:
            timeline.ts_turn_complete = time.time()
            return TurnResult(
                timeline=timeline, tool_calls=[], raw_events=raw_events,
                transcripts={}, terminal_reason=TerminalReason.TIMEOUT_FIRST_TOOL,
            )
        except Exception as e:
            timeline.ts_turn_complete = time.time()
            raw_events.append(RawProviderEvent(
                turn_id=turn_id, ts=time.time(), kind="asr_error",
                payload_json=json.dumps({"error": str(e)}),
            ))
            return TurnResult(
                timeline=timeline, tool_calls=[], raw_events=raw_events,
                transcripts={}, terminal_reason=TerminalReason.PROVIDER_ERROR,
            )

        ts_asr_end = time.time()
        asr_ms = int((ts_asr_end - ts_asr_start) * 1000)
        raw_events.append(RawProviderEvent(
            turn_id=turn_id, ts=ts_asr_end, kind="asr_complete",
            payload_json=json.dumps({"transcript": transcript, "asr_ms": asr_ms}),
        ))

        timeline.ts_setup_complete = ts_asr_end
        timeline.ts_first_event_received = t0  # request-start for ttf_request_to_call_ms

        # ── Text adapter ──────────────────────────────────────────────────────
        inner_result: TurnResult = await self._text_adapter.run_turn(
            audio_wav_path=None,
            tools=tools,
            system_prompt=system_prompt,
            turn_id=turn_id,
            prompt_id=prompt_id,
            timeouts=timeouts,
            prompt_text=transcript,
        )

        # Merge timelines: use inner timeline timestamps but carry ASR time
        inner_tl = inner_result.timeline
        timeline.ts_input_audio_start = ts_asr_start
        timeline.ts_input_audio_end = ts_asr_end
        timeline.ts_first_tool_call_emitted = inner_tl.ts_first_tool_call_emitted
        timeline.ts_turn_complete = inner_tl.ts_turn_complete or time.time()

        return TurnResult(
            timeline=timeline,
            tool_calls=inner_result.tool_calls,
            raw_events=raw_events + inner_result.raw_events,
            transcripts={"asr": transcript},
            terminal_reason=inner_result.terminal_reason,
        )
