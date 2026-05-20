"""Unit tests for voice_bench.scoring.score_turn."""

import pytest
from voice_bench.models import (
    TurnTimeline, TurnResult, ToolCallEvent, RawProviderEvent, TerminalReason
)
from voice_bench.scoring import score_turn


def _timeline():
    return TurnTimeline(
        turn_id="t1", agent="test", prompt_id="p1",
        model_kind="text",
    )


def _result(tool_calls=None):
    return TurnResult(
        timeline=_timeline(),
        tool_calls=tool_calls or [],
        raw_events=[],
        transcripts={},
        terminal_reason=TerminalReason.TURN_COMPLETE,
    )


def _call(name="set_flash", args=None):
    return ToolCallEvent(
        turn_id="t1", tool_name=name, args=args or {},
        call_id="c1", ts_called=0.0,
    )


# ── negative prompt scoring ────────────────────────────────────────────────────

class TestNegativePrompt:
    def test_no_call_passes(self):
        """Negative prompt that correctly produces no tool call should pass."""
        score = score_turn(_result([]), expected_tool=None, expected_args=None,
                           is_negative_prompt=True)
        assert score.passed is True
        assert score.negative_prompt_violation is False
        assert score.is_negative is True

    def test_tool_call_fails(self):
        """Negative prompt where model calls a tool should fail."""
        score = score_turn(_result([_call()]), expected_tool=None, expected_args=None,
                           is_negative_prompt=True)
        assert score.passed is False
        assert score.negative_prompt_violation is True
        assert score.is_negative is True


# ── positive prompt scoring ────────────────────────────────────────────────────

class TestPositivePrompt:
    def test_correct_tool_passes(self):
        score = score_turn(
            _result([_call("set_flash", {"enabled": True})]),
            expected_tool="set_flash",
            expected_args={"enabled": True},
        )
        assert score.passed is True
        assert score.tool_name_match is True

    def test_no_call_fails(self):
        score = score_turn(_result([]), expected_tool="set_flash", expected_args={})
        assert score.passed is False
        assert score.no_call_made is True

    def test_wrong_tool_fails(self):
        score = score_turn(
            _result([_call("set_zoom")]),
            expected_tool="set_flash",
            expected_args={},
        )
        assert score.passed is False
        assert score.tool_name_match is False
