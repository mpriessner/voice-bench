from pathlib import Path
from typing import Protocol, runtime_checkable
from ..models import TurnResult
from ..tools import DummyTool

DEFAULT_TIMEOUTS: dict[str, float] = {
    "connect": 15.0,     # seconds to establish connection and reach setup-complete
    "first_tool": 20.0,  # seconds from end-of-audio to first tool call (or audio)
    "quiet": 5.0,        # seconds of silence after last event before forcing turn-end
    "teardown": 5.0,
}


@runtime_checkable
class NativeVoiceAdapter(Protocol):
    async def probe(self) -> dict:
        """Connect, confirm setup-complete, disconnect. Returns probe metadata."""
        ...

    async def run_turn(
        self,
        audio_wav_path: Path,
        tools: list[DummyTool],
        system_prompt: str,
        turn_id: str,
        prompt_id: str,
        timeouts: dict | None = None,
    ) -> TurnResult:
        """Run one benchmark turn. Returns TurnResult with full timeline."""
        ...
