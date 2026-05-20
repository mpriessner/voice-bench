"""
Build dashboard data files from all JSONL result files.

Usage:
    cd voice-bench && uv run python scripts/build_dashboard.py
    open results/dashboard.html
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESULTS = ROOT / "results"

sys.path.insert(0, str(ROOT / "src"))
from voice_bench.scoring import score_turn
from voice_bench.models import (
    TurnResult, TurnTimeline, ToolCallEvent, TerminalReason, Score
)
import inspect

_tl_sig = inspect.signature(TurnTimeline.__init__)
_TL_FIELDS = set(_tl_sig.parameters) - {"self"}


def _rescore(d: dict) -> Score:
    """Rescore a JSONL row with the current scoring logic."""
    raw = d["result"]
    tl_data = raw.get("timeline", {})
    tl_kwargs = {k: v for k, v in tl_data.items() if k in _TL_FIELDS}
    tl = TurnTimeline(**tl_kwargs)

    tc_list = []
    for tc in raw.get("tool_calls", []):
        tc_list.append(ToolCallEvent(
            turn_id=tc.get("turn_id", ""),
            tool_name=tc["tool_name"],
            args=tc.get("args", {}),
            call_id=tc.get("call_id", ""),
            ts_called=tc.get("ts_called", 0.0),
        ))

    try:
        term = TerminalReason(raw["terminal_reason"])
    except (ValueError, KeyError):
        term = TerminalReason.TURN_COMPLETE

    result = TurnResult(
        timeline=tl,
        tool_calls=tc_list,
        terminal_reason=term,
        raw_events=[],
        transcripts={},
    )

    p = d["prompt"]
    return score_turn(
        result=result,
        expected_tool=p.get("expected_tool"),
        expected_args=p.get("expected_args"),
        is_negative_prompt=p.get("negative", False),
    )


def _failure_kind(score: Score, tc_list: list, expected_tool: str | None, terminal_reason: str) -> str:
    if score.passed:
        return "pass"
    if score.is_negative and score.negative_prompt_violation:
        return "false_positive"
    if terminal_reason in ("OUT_OF_TOOL_SCOPE",):
        return "out_of_scope"
    if terminal_reason == "PROVIDER_ERROR":
        return "provider_error"
    if "TIMEOUT" in terminal_reason:
        return "timeout"
    if not tc_list:
        return "no_tool"
    if not score.tool_name_match:
        return "wrong_tool"
    return "arg_mismatch"


def load_all_rows() -> list[dict]:
    rows = []
    for jsonl in sorted(RESULTS.glob("*.jsonl")):
        # Skip test/scratch runs
        if jsonl.stem.startswith("test-") or jsonl.stem.startswith("scratch-"):
            continue

        run_ts = jsonl.stat().st_mtime

        with open(jsonl) as f:
            lines = [line for line in f if line.strip()]
        if not lines:
            continue

        for line in lines:
            try:
                d = json.loads(line)

                # Skip swap-benchmark rows — they have a different schema
                if d.get("model_kind") == "voice_swap" or d.get("scenario_id"):
                    continue

                # Skip PROVIDER_ERROR rows — these are API outages or unsupported-option
                # errors, not model accuracy failures. They would pollute the heatmap.
                if d.get("result", {}).get("terminal_reason") == "PROVIDER_ERROR":
                    continue

                rid = d.get("run_id", "")

                # Extract tool_count from run_id (e.g. "claude-opus-5t-17xxx" → 5)
                tc = None
                for part in rid.split("-"):
                    if part.endswith("t") and part[:-1].isdigit():
                        tc = int(part[:-1])
                        break

                # Use embedded manifest name (new rows have it); fall back to heuristic
                manifest_name = d.get("manifest")
                if not manifest_name:
                    # Legacy heuristic: count distinct expected tools in the whole file
                    all_tools = set()
                    for ln in lines:
                        try:
                            row2 = json.loads(ln)
                            t = row2["prompt"].get("expected_tool")
                            if t:
                                all_tools.add(t)
                        except Exception:
                            pass
                    manifest_name = "v2" if len(all_tools) >= 15 else "v1"

                score = _rescore(d)
                tc_list = d["result"].get("tool_calls", [])
                terminal_reason = d["result"].get("terminal_reason", "TURN_COMPLETE")
                failure_kind = _failure_kind(score, tc_list, d["prompt"].get("expected_tool"), terminal_reason)
                called_tool = tc_list[0]["tool_name"] if tc_list else None
                actual_args = tc_list[0].get("args") if tc_list else None
                tl = d["result"].get("timeline", {})
                model_kind = d.get("model_kind") or tl.get("model_kind", "voice")

                # Derive agent from run_id prefix (everything before "-NNt-") so old
                # v2 runs that were stored under hardcoded names get properly attributed.
                _agent_from_rid = None
                for i, part in enumerate(rid.split("-")):
                    if part.endswith("t") and part[:-1].isdigit():
                        _agent_from_rid = "-".join(rid.split("-")[:i])
                        break
                _agent = _agent_from_rid or tl.get("agent") or rid.split("-")[0]

                rows.append({
                    "run_id": rid,
                    "run_ts": run_ts,
                    "agent": _agent,
                    "tool_count": tc,
                    "manifest": manifest_name,
                    "benchmark_mode": d.get("benchmark_mode", "needle"),
                    "model_kind": model_kind,
                    "prompt_id": d["prompt"]["id"],
                    "turn_id": tl.get("turn_id", ""),
                    "prompt_text": d["prompt"].get("text", ""),
                    "difficulty": d["prompt"].get("difficulty", "v1"),
                    "negative": d["prompt"].get("negative", False),
                    "expected_tool": d["prompt"].get("expected_tool", ""),
                    "expected_args": d["prompt"].get("expected_args"),
                    "called_tool": called_tool,
                    "actual_args": actual_args,
                    "passed": score.passed,
                    "failure_kind": failure_kind,
                    "terminal_reason": terminal_reason,
                    "arg_score": round(score.arg_score, 2),
                    "ttf_tool_ms": score.ttf_tool_ms,
                    "ttfs_ms": score.ttfs_ms,
                    "ttf_request_to_call_ms": score.ttf_request_to_call_ms,
                })
            except Exception as exc:
                pass  # skip malformed rows

    return rows


def main() -> None:
    rows = load_all_rows()
    print(f"Loaded {len(rows)} turns from {RESULTS}")

    # Write data.js — loaded as a script tag (avoids fetch CORS on file://)
    data_js_path = RESULTS / "data.js"
    with open(data_js_path, "w") as f:
        f.write("// Auto-generated by build_dashboard.py — do not edit\n")
        f.write(f"window.__DATA__ = {json.dumps(rows)};\n")
    print(f"Wrote {data_js_path}")

    # Also write data.json for programmatic consumption
    data_json_path = RESULTS / "data.json"
    with open(data_json_path, "w") as f:
        json.dump(rows, f, indent=2)
    print(f"Wrote {data_json_path}")

    print(f"\nOpen: open {RESULTS / 'dashboard.html'}")


if __name__ == "__main__":
    main()
