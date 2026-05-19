from dataclasses import dataclass
from typing import Any, Optional
import json
from .models import TurnResult, Score, TerminalReason


@dataclass
class PipelineScore:
    """Score for a two-layer pipeline turn (voice routing + text sub-tool)."""
    bucket_match: bool      # voice called the right meta-tool
    subtool_match: bool     # text called the right sub-tool within the bucket
    arg_score: float        # argument accuracy on the sub-tool call
    end_to_end_pass: bool   # both layers correct and arg_score >= 0.8
    voice_decision_ms: Optional[int]   # voice adapter latency
    text_decision_ms: Optional[int]    # text adapter latency
    pipeline_wall_ms: Optional[int]    # voice + text combined
    no_route: bool          # voice failed to route at all
    wrong_route: bool       # voice called wrong meta-tool

    LATENCY_CEILING_MS = 2000  # flag if pipeline_wall_ms exceeds this

    @property
    def latency_ok(self) -> bool:
        return self.pipeline_wall_ms is None or self.pipeline_wall_ms <= self.LATENCY_CEILING_MS

    def to_dict(self) -> dict:
        return {
            "bucket_match": self.bucket_match,
            "subtool_match": self.subtool_match,
            "arg_score": round(self.arg_score, 4),
            "end_to_end_pass": self.end_to_end_pass,
            "voice_decision_ms": self.voice_decision_ms,
            "text_decision_ms": self.text_decision_ms,
            "pipeline_wall_ms": self.pipeline_wall_ms,
            "latency_ok": self.latency_ok,
            "no_route": self.no_route,
            "wrong_route": self.wrong_route,
        }


SYNONYMS: dict[str, list[str]] = {
    "true": ["on", "yes", "enable", "enabled", "1", "active"],
    "false": ["off", "no", "disable", "disabled", "0", "inactive"],
}

# Number-word → digit mapping for label normalization. ASR engines commonly
# transcribe spoken "one" as "1"; the scorer should treat both as equivalent.
_NUMBER_WORDS: dict[str, str] = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20",
}


def _normalize(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v).lower().strip()
    for canonical, alts in SYNONYMS.items():
        if s in alts:
            return canonical
    # Treat spaces, hyphens, and underscores as equivalent in label-like strings
    import re
    s = re.sub(r"[\s\-_]+", "_", s)
    # Insert underscore at letter-digit boundaries so "trial1" normalizes the
    # same as "trial 1" / "trial_1" / "trial one" → all → "trial_1".
    s = re.sub(r"(?<=[a-z])(?=\d)", "_", s)
    s = re.sub(r"(?<=\d)(?=[a-z])", "_", s)
    # Replace spelled-out number tokens with digit form: "trial_one" → "trial_1"
    tokens = s.split("_")
    tokens = [_NUMBER_WORDS.get(tok, tok) for tok in tokens]
    s = "_".join(tokens)
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
            ttf_request_to_call_ms=result.timeline.ttf_request_to_call_ms,
            extra_calls=len(calls),
            duplicate_calls=0,
            malformed_calls=0,
            wrong_tool_first=False,
            no_call_made=len(calls) == 0,
            negative_prompt_violation=len(calls) > 0,
            is_negative=True,
        )

    if not calls:
        return Score(
            tool_name_match=False,
            arg_score=0.0,
            ttfs_ms=result.timeline.ttfs_ms,
            ttf_tool_ms=None,
            ttf_request_to_call_ms=None,
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
        ttf_request_to_call_ms=result.timeline.ttf_request_to_call_ms,
        extra_calls=max(0, len(calls) - 1),
        duplicate_calls=duplicates,
        malformed_calls=0,
        wrong_tool_first=wrong_tool_first,
        no_call_made=False,
        negative_prompt_violation=False,
    )


def score_routing_turn(
    result: TurnResult,
    expected_category: Optional[str],
    category_to_meta_tool: dict[str, str],
) -> Score:
    """Score a routing turn: did the model call the correct meta-tool?"""
    calls = result.tool_calls
    expected_meta = category_to_meta_tool.get(expected_category or "", "") if expected_category else None

    if not calls:
        return Score(
            tool_name_match=False,
            arg_score=0.0,
            ttfs_ms=result.timeline.ttfs_ms,
            ttf_tool_ms=result.timeline.ttf_tool_ms,
            ttf_request_to_call_ms=result.timeline.ttf_request_to_call_ms,
            extra_calls=0,
            duplicate_calls=0,
            malformed_calls=0,
            wrong_tool_first=False,
            no_call_made=True,
            negative_prompt_violation=False,
        )

    first_call = calls[0]
    tool_name_match = (first_call.tool_name == expected_meta) if expected_meta else False

    return Score(
        tool_name_match=tool_name_match,
        arg_score=1.0 if tool_name_match else 0.0,
        ttfs_ms=result.timeline.ttfs_ms,
        ttf_tool_ms=result.timeline.ttf_tool_ms,
        ttf_request_to_call_ms=result.timeline.ttf_request_to_call_ms,
        extra_calls=max(0, len(calls) - 1),
        duplicate_calls=0,
        malformed_calls=0,
        wrong_tool_first=not tool_name_match,
        no_call_made=False,
        negative_prompt_violation=False,
    )


def score_pipeline_turn(
    voice_result: TurnResult,
    text_result: Optional[TurnResult],
    expected_category: Optional[str],
    expected_tool: Optional[str],
    expected_args: Optional[dict],
    category_to_meta_tool: dict[str, str],
) -> "PipelineScore":
    """Score a two-layer pipeline: voice routing + text sub-tool selection."""
    expected_meta = category_to_meta_tool.get(expected_category or "", "") if expected_category else None

    voice_calls = voice_result.tool_calls
    voice_lat = voice_result.timeline.ttf_tool_ms or voice_result.timeline.ttf_request_to_call_ms

    if not voice_calls:
        return PipelineScore(
            bucket_match=False, subtool_match=False, arg_score=0.0,
            end_to_end_pass=False, voice_decision_ms=voice_lat, text_decision_ms=None,
            pipeline_wall_ms=voice_lat, no_route=True, wrong_route=False,
        )

    called_meta = voice_calls[0].tool_name
    bucket_match = (called_meta == expected_meta) if expected_meta else False
    wrong_route = not bucket_match

    if text_result is None:
        return PipelineScore(
            bucket_match=bucket_match, subtool_match=False, arg_score=0.0,
            end_to_end_pass=False, voice_decision_ms=voice_lat, text_decision_ms=None,
            pipeline_wall_ms=voice_lat, no_route=False, wrong_route=wrong_route,
        )

    text_calls = text_result.tool_calls
    text_lat = text_result.timeline.ttf_request_to_call_ms
    pipeline_wall = (voice_lat or 0) + (text_lat or 0)

    if not text_calls:
        return PipelineScore(
            bucket_match=bucket_match, subtool_match=False, arg_score=0.0,
            end_to_end_pass=False, voice_decision_ms=voice_lat, text_decision_ms=text_lat,
            pipeline_wall_ms=pipeline_wall, no_route=False, wrong_route=wrong_route,
        )

    called_subtool = text_calls[0].tool_name
    subtool_match = (called_subtool == expected_tool) if expected_tool else False
    if subtool_match:
        _, arg_score = _arg_score(text_calls[0].args, expected_args or {})
    else:
        arg_score = 0.0

    end_to_end = bucket_match and subtool_match and arg_score >= 0.8

    return PipelineScore(
        bucket_match=bucket_match, subtool_match=subtool_match, arg_score=arg_score,
        end_to_end_pass=end_to_end, voice_decision_ms=voice_lat, text_decision_ms=text_lat,
        pipeline_wall_ms=pipeline_wall, no_route=False, wrong_route=wrong_route,
    )
