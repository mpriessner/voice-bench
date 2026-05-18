from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class TerminalReason(str, Enum):
    TURN_COMPLETE = "TURN_COMPLETE"
    TIMEOUT_CONNECT = "TIMEOUT_CONNECT"
    TIMEOUT_FIRST_TOOL = "TIMEOUT_FIRST_TOOL"
    TIMEOUT_FIRST_AUDIO = "TIMEOUT_FIRST_AUDIO"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    NO_TOOL_CALLED = "NO_TOOL_CALLED"
    DISCONNECTED = "DISCONNECTED"


@dataclass
class TurnTimeline:
    turn_id: str
    agent: str
    prompt_id: str
    ts_connect_start: Optional[float] = None
    ts_setup_complete: Optional[float] = None
    ts_input_audio_start: Optional[float] = None
    ts_input_audio_end: Optional[float] = None
    ts_first_event_received: Optional[float] = None
    ts_first_tool_call_emitted: Optional[float] = None
    ts_tool_response_sent: Optional[float] = None
    ts_first_output_audio: Optional[float] = None
    ts_turn_complete: Optional[float] = None

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

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "agent": self.agent,
            "prompt_id": self.prompt_id,
            "ts_connect_start": self.ts_connect_start,
            "ts_setup_complete": self.ts_setup_complete,
            "ts_input_audio_start": self.ts_input_audio_start,
            "ts_input_audio_end": self.ts_input_audio_end,
            "ts_first_event_received": self.ts_first_event_received,
            "ts_first_tool_call_emitted": self.ts_first_tool_call_emitted,
            "ts_tool_response_sent": self.ts_tool_response_sent,
            "ts_first_output_audio": self.ts_first_output_audio,
            "ts_turn_complete": self.ts_turn_complete,
            "ttf_tool_ms": self.ttf_tool_ms,
            "ttfs_ms": self.ttfs_ms,
        }


@dataclass
class ToolCallEvent:
    turn_id: str
    tool_name: str
    args: dict
    call_id: str
    ts_called: float

    def to_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "tool_name": self.tool_name,
            "args": self.args,
            "call_id": self.call_id,
            "ts_called": self.ts_called,
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

    def to_dict(self) -> dict:
        return {
            "timeline": self.timeline.to_dict(),
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "raw_events": [re.to_dict() for re in self.raw_events],
            "transcripts": self.transcripts,
            "terminal_reason": self.terminal_reason.value,
        }


@dataclass
class Score:
    tool_name_match: bool
    arg_score: float
    ttfs_ms: Optional[int]
    ttf_tool_ms: Optional[int]
    extra_calls: int
    duplicate_calls: int
    malformed_calls: int
    wrong_tool_first: bool
    no_call_made: bool
    negative_prompt_violation: bool

    @property
    def passed(self) -> bool:
        return self.tool_name_match and self.arg_score >= 0.8 and not self.malformed_calls

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "tool_name_match": self.tool_name_match,
            "arg_score": round(self.arg_score, 4),
            "ttfs_ms": self.ttfs_ms,
            "ttf_tool_ms": self.ttf_tool_ms,
            "extra_calls": self.extra_calls,
            "duplicate_calls": self.duplicate_calls,
            "malformed_calls": self.malformed_calls,
            "wrong_tool_first": self.wrong_tool_first,
            "no_call_made": self.no_call_made,
            "negative_prompt_violation": self.negative_prompt_violation,
        }
