"""Scoring for the dynamic pool-swap benchmark."""

from dataclasses import dataclass
from typing import Optional

from .models import TurnResult, TerminalReason
from .scoring import _arg_score, score_turn


@dataclass
class SwapScore:
    """Score for a single turn in a swap scenario."""
    passed: bool
    tool_correct: bool
    arg_score: float
    is_swap_turn: bool          # was the expected action a switch_toolset call?
    swap_happened: bool         # did the model actually call switch_toolset?
    swap_target_correct: bool   # if swap: did it name the right toolset?
    extra_tool_calls: int       # non-meta calls beyond the expected one
    no_call: bool
    toolset_at_call: str | None  # which pool was active when the task tool fired
    swap_rtt_ms: int | None      # session.update round-trip (None if no swap this turn)
    terminal_reason: str

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "tool_correct": self.tool_correct,
            "arg_score": round(self.arg_score, 4),
            "is_swap_turn": self.is_swap_turn,
            "swap_happened": self.swap_happened,
            "swap_target_correct": self.swap_target_correct,
            "extra_tool_calls": self.extra_tool_calls,
            "no_call": self.no_call,
            "toolset_at_call": self.toolset_at_call,
            "swap_rtt_ms": self.swap_rtt_ms,
            "terminal_reason": self.terminal_reason,
        }


_SWAP_META_TOOLS = {"switch_toolset", "list_toolsets"}


def score_swap_turn(
    result: TurnResult,
    expected_tool: str,
    expected_args: dict,
) -> SwapScore:
    """Score a single swap-scenario turn result."""
    is_swap_turn = (expected_tool == "switch_toolset")
    terminal = result.terminal_reason.value
    swap_rtt = result.timeline.swap_rtt_ms

    if is_swap_turn:
        # The adapter handles switch_toolset as a meta call — it never appears in
        # tool_calls. Success = session.update was sent (swap_rtt is not None).
        swap_happened = swap_rtt is not None
        return SwapScore(
            passed=swap_happened,
            tool_correct=swap_happened,
            arg_score=1.0 if swap_happened else 0.0,
            is_swap_turn=True,
            swap_happened=swap_happened,
            swap_target_correct=swap_happened,  # adapter validates the name before swapping
            extra_tool_calls=len(result.tool_calls),  # any task calls in a swap turn are extra
            no_call=False,  # use swap_happened to check if the swap occurred; no_call is task-centric
            toolset_at_call=None,
            swap_rtt_ms=swap_rtt,
            terminal_reason=terminal,
        )

    if not result.tool_calls:
        return SwapScore(
            passed=False,
            tool_correct=False,
            arg_score=0.0,
            is_swap_turn=False,
            swap_happened=swap_rtt is not None,
            swap_target_correct=False,
            extra_tool_calls=0,
            no_call=True,
            toolset_at_call=None,
            swap_rtt_ms=swap_rtt,
            terminal_reason=terminal,
        )

    # Task turn: expected a real tool call (not swap meta)
    task_calls = [tc for tc in result.tool_calls if tc.tool_name not in _SWAP_META_TOOLS]
    swap_happened = swap_rtt is not None

    if not task_calls:
        return SwapScore(
            passed=False,
            tool_correct=False,
            arg_score=0.0,
            is_swap_turn=False,
            swap_happened=swap_happened,
            swap_target_correct=False,
            extra_tool_calls=0,
            no_call=True,
            toolset_at_call=None,
            swap_rtt_ms=swap_rtt,
            terminal_reason=terminal,
        )

    first_task = task_calls[0]
    tool_correct = (first_task.tool_name == expected_tool)

    matching = next((c for c in task_calls if c.tool_name == expected_tool), None)
    _, arg_s = _arg_score(matching.args if matching else {}, expected_args)

    passed = tool_correct and arg_s >= 0.8

    return SwapScore(
        passed=passed,
        tool_correct=tool_correct,
        arg_score=arg_s,
        is_swap_turn=False,
        swap_happened=swap_happened,
        swap_target_correct=False,
        extra_tool_calls=max(0, len(task_calls) - 1),
        no_call=False,
        toolset_at_call=first_task.toolset_at_call,
        swap_rtt_ms=swap_rtt,
        terminal_reason=terminal,
    )
