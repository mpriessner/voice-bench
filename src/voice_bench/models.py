from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, Optional


class TerminalReason(str, Enum):
    TURN_COMPLETE = "TURN_COMPLETE"
    TIMEOUT_CONNECT = "TIMEOUT_CONNECT"
    TIMEOUT_FIRST_TOOL = "TIMEOUT_FIRST_TOOL"
    TIMEOUT_FIRST_AUDIO = "TIMEOUT_FIRST_AUDIO"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NO_TOOL_CALLED = "NO_TOOL_CALLED"
    DISCONNECTED = "DISCONNECTED"
    OUT_OF_TOOL_SCOPE = "OUT_OF_TOOL_SCOPE"


@dataclass
class TurnTimeline:
    turn_id: str
    agent: str
    prompt_id: str
    model_kind: Literal["voice", "text", "voice_swap"] = "voice"
    ts_connect_start: Optional[float] = None
    ts_setup_complete: Optional[float] = None
    ts_input_audio_start: Optional[float] = None
    ts_input_audio_end: Optional[float] = None
    ts_first_event_received: Optional[float] = None
    ts_first_tool_call_emitted: Optional[float] = None
    ts_tool_response_sent: Optional[float] = None
    ts_first_output_audio: Optional[float] = None
    ts_turn_complete: Optional[float] = None
    ts_swap_request: Optional[float] = None         # when swap initiated (switch_toolset tool response sent)
    ts_swap_session_opened: Optional[float] = None  # when new session connected (Gemini: restart cost)
    ts_swap_ack: Optional[float] = None             # when swap confirmed (OpenAI: session.updated; Gemini: verbal turn_complete)

    @property
    def swap_rtt_ms(self) -> Optional[int]:
        """User-visible swap latency: from initiation to full confirmation."""
        if self.ts_swap_ack and self.ts_swap_request:
            return round((self.ts_swap_ack - self.ts_swap_request) * 1000)
        return None

    @property
    def swap_mechanism_ms(self) -> Optional[int]:
        """Cross-provider KPI: time from swap request to new session ready (excludes verbal confirmation)."""
        if self.ts_swap_session_opened and self.ts_swap_request:
            return round((self.ts_swap_session_opened - self.ts_swap_request) * 1000)
        return None

    @property
    def swap_ux_delay_ms(self) -> Optional[int]:
        """Verbal confirmation cost: time from session ready to spoken confirmation received."""
        if self.ts_swap_ack and self.ts_swap_session_opened:
            return round((self.ts_swap_ack - self.ts_swap_session_opened) * 1000)
        return None

    @property
    def ttf_tool_ms(self) -> Optional[int]:
        if self.ts_first_tool_call_emitted and self.ts_input_audio_end:
            return int((self.ts_first_tool_call_emitted - self.ts_input_audio_end) * 1000)
        return None

    @property
    def ttfs_ms(self) -> Optional[int]:
        if self.ts_first_output_audio and self.ts_input_audio_end:
            return int((self.ts_first_output_audio - self.ts_input_audio_end) * 1000)
        return None

    @property
    def ttf_request_to_call_ms(self) -> Optional[int]:
        """Time from first event received (request sent) to first tool call, for text adapters."""
        if self.ts_first_tool_call_emitted and self.ts_first_event_received:
            return int((self.ts_first_tool_call_emitted - self.ts_first_event_received) * 1000)
        return None

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "agent": self.agent,
            "prompt_id": self.prompt_id,
            "model_kind": self.model_kind,
            "ts_connect_start": self.ts_connect_start,
            "ts_setup_complete": self.ts_setup_complete,
            "ts_input_audio_start": self.ts_input_audio_start,
            "ts_input_audio_end": self.ts_input_audio_end,
            "ts_first_event_received": self.ts_first_event_received,
            "ts_first_tool_call_emitted": self.ts_first_tool_call_emitted,
            "ts_tool_response_sent": self.ts_tool_response_sent,
            "ts_first_output_audio": self.ts_first_output_audio,
            "ts_turn_complete": self.ts_turn_complete,
            "ts_swap_request": self.ts_swap_request,
            "ts_swap_session_opened": self.ts_swap_session_opened,
            "ts_swap_ack": self.ts_swap_ack,
            "ttf_tool_ms": self.ttf_tool_ms,
            "ttfs_ms": self.ttfs_ms,
            "ttf_request_to_call_ms": self.ttf_request_to_call_ms,
            "swap_rtt_ms": self.swap_rtt_ms,
            "swap_mechanism_ms": self.swap_mechanism_ms,
            "swap_ux_delay_ms": self.swap_ux_delay_ms,
        }


@dataclass
class ToolCallEvent:
    turn_id: str
    tool_name: str
    args: dict
    call_id: str
    ts_called: float
    toolset_at_call: str | None = None

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "call_id": self.call_id,
            "ts_called": self.ts_called,
            "toolset_at_call": self.toolset_at_call,
        }


@dataclass
class RawProviderEvent:
    turn_id: str
    ts: float
    kind: str
    payload_json: str

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "ts": self.ts,
            "kind": self.kind,
            "payload_json": self.payload_json,
        }


@dataclass
class TurnResult:
    timeline: TurnTimeline
    tool_calls: list[ToolCallEvent]
    raw_events: list[RawProviderEvent]
    transcripts: dict[str, str]
    terminal_reason: TerminalReason
    swap_events: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timeline": self.timeline.to_dict(),
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "raw_events": [re.to_dict() for re in self.raw_events],
            "transcripts": self.transcripts,
            "terminal_reason": self.terminal_reason.value,
            "swap_events": self.swap_events,
        }


@dataclass
class Score:
    tool_name_match: bool
    arg_score: float
    ttfs_ms: Optional[int]
    ttf_tool_ms: Optional[int]
    ttf_request_to_call_ms: Optional[int]
    extra_calls: int
    duplicate_calls: int
    malformed_calls: int
    wrong_tool_first: bool
    no_call_made: bool
    negative_prompt_violation: bool
    is_negative: bool = False

    @property
    def passed(self) -> bool:
        if self.is_negative:
            return not self.negative_prompt_violation and not self.malformed_calls
        return self.tool_name_match and self.arg_score >= 0.8 and not self.malformed_calls

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "tool_name_match": self.tool_name_match,
            "arg_score": round(self.arg_score, 4),
            "ttfs_ms": self.ttfs_ms,
            "ttf_tool_ms": self.ttf_tool_ms,
            "ttf_request_to_call_ms": self.ttf_request_to_call_ms,
            "extra_calls": self.extra_calls,
            "duplicate_calls": self.duplicate_calls,
            "malformed_calls": self.malformed_calls,
            "wrong_tool_first": self.wrong_tool_first,
            "no_call_made": self.no_call_made,
            "negative_prompt_violation": self.negative_prompt_violation,
        }
