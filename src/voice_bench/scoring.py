from typing import Any, Optional
import json
from .models import TurnResult, Score


SYNONYMS: dict[str, list[str]] = {
    "true": ["on", "yes", "enable", "enabled", "1", "active"],
    "false": ["off", "no", "disable", "disabled", "0", "inactive"],
}


def _normalize(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).lower().strip()
    for canonical, alts in SYNONYMS.items():
        if s in alts:
            return canonical
    return s


def _arg_score(actual: dict, expected: dict) -> tuple[bool, float]:
    """Returns (confident, score 0..1). confident=True when score is clearly 0 or 1."""
    if not expected:
        return True, 1.0
    if not actual:
        return True, 0.0

    scores: list[float] = []
    for key, exp_val in expected.items():
        act_val = actual.get(key)
        if act_val is None:
            scores.append(0.0)
            continue
        norm_exp = _normalize(exp_val)
        norm_act = _normalize(act_val)
        if norm_exp == norm_act:
            scores.append(1.0)
        elif isinstance(exp_val, (int, float)):
            try:
                exp_f, act_f = float(exp_val), float(act_val)
                if exp_f == 0:
                    scores.append(1.0 if act_f == 0 else 0.0)
                else:
                    rel_err = abs(exp_f - act_f) / abs(exp_f)
                    scores.append(max(0.0, 1.0 - rel_err / 0.05))
            except (ValueError, TypeError):
                scores.append(0.0)
        else:
            scores.append(0.0)

    final = sum(scores) / len(scores)
    confident = final >= 0.99 or final <= 0.01
    return confident, final


def score_turn(
    result: TurnResult,
    expected_tool: Optional[str],
    expected_args: Optional[dict],
    is_negative_prompt: bool = False,
) -> Score:
    calls = result.tool_calls

    if is_negative_prompt:
        return Score(
            tool_name_match=False,
            arg_score=1.0,
            ttfs_ms=result.timeline.ttfs_ms,
            ttf_tool_ms=result.timeline.ttf_tool_ms,
            extra_calls=len(calls),
            duplicate_calls=0,
            malformed_calls=0,
            wrong_tool_first=False,
            no_call_made=len(calls) == 0,
            negative_prompt_violation=len(calls) > 0,
        )

    if not calls:
        return Score(
            tool_name_match=False,
            arg_score=0.0,
            ttfs_ms=result.timeline.ttfs_ms,
            ttf_tool_ms=None,
            extra_calls=0,
            duplicate_calls=0,
            malformed_calls=0,
            wrong_tool_first=False,
            no_call_made=True,
            negative_prompt_violation=False,
        )

    first_call = calls[0]
    tool_name_match = first_call.tool_name == expected_tool
    wrong_tool_first = not tool_name_match

    matching_call = next((c for c in calls if c.tool_name == expected_tool), None)
    if matching_call:
        _, arg_score = _arg_score(matching_call.args, expected_args or {})
    else:
        arg_score = 0.0

    seen: set[str] = set()
    duplicates = 0
    for c in calls:
        key = f"{c.tool_name}:{json.dumps(c.args, sort_keys=True)}"
        if key in seen:
            duplicates += 1
        seen.add(key)

    return Score(
        tool_name_match=tool_name_match,
        arg_score=arg_score,
        ttfs_ms=result.timeline.ttfs_ms,
        ttf_tool_ms=result.timeline.ttf_tool_ms,
        extra_calls=max(0, len(calls) - 1),
        duplicate_calls=duplicates,
        malformed_calls=0,
        wrong_tool_first=wrong_tool_first,
        no_call_made=False,
        negative_prompt_violation=False,
    )
