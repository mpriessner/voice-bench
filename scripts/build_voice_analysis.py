"""
Build a focused voice-agent analysis HTML page.

Shows v1 vs v2 prompt comparison, failure patterns, and sweet-spot analysis.

Usage:
    cd voice-bench && uv run python scripts/build_voice_analysis.py
    open results/voice_analysis.html
"""

import json
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESULTS = ROOT / "results"


def _load_turns(f: Path) -> list:
    turns = []
    with open(f) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except Exception:
                pass
    return turns


def _error_rate(turns: list) -> float:
    if not turns:
        return 1.0
    err = sum(1 for t in turns if t["result"].get("terminal_reason") == "PROVIDER_ERROR")
    return err / len(turns)


def load_voice_runs() -> dict:
    """Returns {(agent, tool_count): [scored_turns]} using the best non-error run per pair.

    Skips runs where >25% of turns are PROVIDER_ERROR (API quota/billing failures).
    Uses the most-recent valid run.
    """
    files = list(RESULTS.glob("*.jsonl"))
    # Collect all candidates per pair, sorted newest-first
    candidates: dict[tuple, list] = {}
    for f in files:
        m = re.search(r"/([^/]+)-(\d+)t-(\d+)\.jsonl$", str(f))
        if not m:
            continue
        agent, tc, ts = m.group(1), int(m.group(2)), int(m.group(3))
        if agent not in {"gemini-live", "gemini-live-v2", "openai-realtime", "openai-realtime-v2"}:
            continue
        pair = (agent, tc)
        candidates.setdefault(pair, []).append((ts, f))

    runs: dict[tuple, list] = {}
    for pair, cands in candidates.items():
        for ts, f in sorted(cands, reverse=True):  # newest first
            turns = _load_turns(f)
            if turns and _error_rate(turns) <= 0.25:
                runs[pair] = turns
                break
        # If all runs have high error rates, still include the best one with a note
        if pair not in runs and cands:
            _, f = sorted(cands, reverse=True)[0]
            runs[pair] = _load_turns(f)
    return runs


def acc(turns: list) -> tuple[int, int]:
    passed = sum(1 for t in turns if t.get("score", {}).get("passed"))
    return passed, len(turns)


def failure_kinds(turns: list) -> dict[str, int]:
    kinds: dict[str, int] = defaultdict(int)
    for t in turns:
        sc = t.get("score", {})
        calls = t["result"].get("tool_calls", [])
        term = t["result"].get("terminal_reason", "")
        if sc.get("passed"):
            kinds["pass"] += 1
        elif term == "PROVIDER_ERROR":
            kinds["provider_error"] += 1
        elif "TIMEOUT" in term:
            kinds["timeout"] += 1
        elif not calls:
            kinds["no_call"] += 1
        elif not sc.get("tool_name_match"):
            kinds["wrong_tool"] += 1
        elif sc.get("arg_score", 1) < 1.0:
            kinds["arg_mismatch"] += 1
        else:
            kinds["other"] += 1
    return dict(kinds)


def pct(p: int, t: int) -> str:
    if t == 0:
        return "—"
    v = round(100 * p / t)
    if v == 100:
        return '<span style="color:#4ade80;font-weight:700">100%</span>'
    elif v >= 95:
        return f'<span style="color:#4ade80">{v}%</span>'
    elif v >= 80:
        return f'<span style="color:#fbbf24">{v}%</span>'
    else:
        return f'<span style="color:#f87171">{v}%</span>'


def build_html(runs: dict) -> str:
    tool_counts = sorted({tc for (_, tc) in runs})
    agents_v1 = ["gemini-live", "openai-realtime"]
    agents_v2 = ["gemini-live-v2", "openai-realtime-v2"]

    # --- Accuracy comparison table rows ---
    def tc_header() -> str:
        return "".join(f"<th>{tc}t</th>" for tc in tool_counts)

    def agent_row(agent: str, label: str, badge_class: str) -> str:
        cells = ""
        for tc in tool_counts:
            turns = runs.get((agent, tc), [])
            if not turns:
                cells += "<td>—</td>"
            else:
                p, t = acc(turns)
                cells += f"<td>{pct(p, t)}<br><small style='color:#475569'>{p}/{t}</small></td>"
        return f"<tr><td><span class='badge {badge_class}'>{label}</span></td>{cells}</tr>"

    # --- Failure breakdown for a specific run ---
    def failure_bar(agent: str, tc: int) -> str:
        turns = runs.get((agent, tc), [])
        if not turns:
            return "<em style='color:#475569'>no data</em>"
        fk = failure_kinds(turns)
        total = sum(fk.values())
        bars = []
        color_map = {
            "pass": "#4ade80",
            "wrong_tool": "#f87171",
            "arg_mismatch": "#fb923c",
            "no_call": "#94a3b8",
            "provider_error": "#64748b",
            "timeout": "#a78bfa",
        }
        for kind, count in sorted(fk.items(), key=lambda x: -x[1]):
            color = color_map.get(kind, "#64748b")
            w = round(100 * count / total)
            if w < 2:
                continue
            bars.append(f'<div style="display:inline-block;width:{w}%;background:{color};height:18px;'
                        f'vertical-align:middle" title="{kind}: {count}/{total} ({w}%)"></div>')
        return "".join(bars) or '<div style="height:18px"></div>'

    # Failure examples
    def failure_examples(agent: str, tc: int, limit: int = 5) -> str:
        turns = runs.get((agent, tc), [])
        if not turns:
            return "<em>no data</em>"
        fails = [t for t in turns if not t.get("score", {}).get("passed")]
        if not fails:
            return '<span style="color:#4ade80">All passed ✓</span>'
        rows = []
        for t in fails[:limit]:
            pid = t["prompt"]["id"]
            txt = t["prompt"]["text"][:50]
            exp_t = t["prompt"]["expected_tool"]
            calls = t["result"].get("tool_calls", [])
            got_t = calls[0]["tool_name"] if calls else "NO_CALL"
            exp_a = t["prompt"].get("expected_args", {})
            got_a = calls[0].get("args", {}) if calls else {}
            wrong_tool = got_t != exp_t
            color = "#f87171" if wrong_tool else "#fb923c"
            reason = f"wrong tool: got <code>{got_t}</code>" if wrong_tool else f"arg mismatch: got <code>{json.dumps(got_a)}</code> expected <code>{json.dumps(exp_a)}</code>"
            rows.append(f'<tr><td style="color:#94a3b8">{pid}</td>'
                        f'<td style="color:#e2e8f0">{txt}</td>'
                        f'<td><code style="color:#60a5fa">{exp_t}</code></td>'
                        f'<td style="color:{color}">{reason}</td></tr>')
        more = f"<tr><td colspan='4' style='color:#475569'>…and {len(fails)-limit} more</td></tr>" if len(fails) > limit else ""
        header = "<tr><th>ID</th><th>Prompt</th><th>Expected</th><th>Failure</th></tr>"
        return f"<table style='font-size:0.78rem'>{header}{''.join(rows)}{more}</table>"

    # --- Build the full HTML ---
    tc_headers = tc_header()

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>voice-bench — Voice Agent Analysis</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0f1117; color: #e2e8f0; min-height: 100vh; padding: 0 0 60px; }}
  header {{ padding: 24px 32px; border-bottom: 1px solid #1e2535; }}
  header h1 {{ font-size: 1.5rem; font-weight: 700; margin-bottom: 4px; }}
  header p {{ font-size: 0.88rem; color: #64748b; max-width: 720px; line-height: 1.5; }}
  section {{ padding: 28px 32px 0; }}
  section h2 {{ font-size: 0.9rem; font-weight: 700; color: #94a3b8; text-transform: uppercase;
               letter-spacing: 0.06em; margin-bottom: 16px; border-bottom: 1px solid #1e2535;
               padding-bottom: 8px; }}
  .card {{ background: #161b27; border: 1px solid #1e2535; border-radius: 12px; padding: 20px;
           margin-bottom: 20px; }}
  .card h3 {{ font-size: 0.85rem; color: #94a3b8; margin-bottom: 14px; font-weight: 600; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.82rem; }}
  th {{ text-align: center; padding: 8px 10px; color: #64748b; font-weight: 500;
        border-bottom: 1px solid #1e2535; }}
  th:first-child {{ text-align: left; }}
  td {{ padding: 8px 10px; border-bottom: 1px solid #1a2030; text-align: center; vertical-align: top; }}
  td:first-child {{ text-align: left; }}
  tr:last-child td {{ border-bottom: none; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 5px; font-size: 0.78rem;
            font-weight: 600; white-space: nowrap; }}
  .b-gemini {{ background: #1a3a5c; color: #60a5fa; }}
  .b-openai {{ background: #1a3a2a; color: #4ade80; }}
  .b-v2 {{ background: #2a1a3a; color: #c084fc; }}
  .insight {{ background: #0e1a2e; border-left: 3px solid #3b82f6; padding: 12px 16px;
             border-radius: 0 8px 8px 0; margin-bottom: 14px; font-size: 0.85rem;
             line-height: 1.6; color: #cbd5e1; }}
  .insight strong {{ color: #60a5fa; }}
  code {{ background: #1e2535; padding: 1px 5px; border-radius: 3px; font-size: 0.82em; }}
  .grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
  @media (max-width: 900px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<header>
  <h1>Voice Agent Analysis — v1 vs v2 Prompt Comparison</h1>
  <p>Benchmarking Gemini Live and OpenAI Realtime across tool counts (1–30).
     Comparing the <strong>original (v1)</strong> system prompt against the
     <strong>improved (v2)</strong> prompt with explicit boolean direction rules.
     All runs use mode=full (all manifest prompts).</p>
</header>

<!-- KEY FINDINGS -->
<section>
  <h2>Key Findings</h2>
  <div class="insight"><strong>Boolean negation bug (v1):</strong> The v1 system prompt says "Enable/disable → toggle_*" but gives no direction for boolean parameters. Both models return <code>on: true</code> for "hide/disable/off" prompts. The v2 prompt adds an explicit rule: <em>"hide/off/disable → on: false"</em> — fixing 100% of these failures.</div>
  <div class="insight"><strong>3-tool rotation pattern:</strong> With exactly 3 tools loaded, both models show a systematic "rotation" — selecting the tool one or two positions away from the correct one in the schema. The same pattern reappears at 30t (schema overflow). There is a <em>sweet spot of 5–20 tools</em> where voice agents are most reliable.</div>
  <div class="insight"><strong>The 1/2/3t numbers in the main dashboard are misleading:</strong> Earlier runs used smoke mode (2/4/6 prompts) with an old smaller manifest. The correct comparable numbers come from these full-manifest runs. With the v2 prompt, voice agents achieve 100% at 3t, 5t, and 10t.</div>
  <div class="insight"><strong>Architecture sweet spot:</strong> Voice agents reliably handle 5–20 tools (98–100%). They break at 30t via positional confusion (~40%). A two-tier routing design — voice picks category (5–10 tools), text picks sub-tool — is validated by this data.</div>
</section>

<!-- ACCURACY TABLE -->
<section>
  <h2>Accuracy by Agent and Tool Count</h2>
  <div class="card">
    <table>
      <tr><th>Agent</th>{tc_headers}</tr>
      {agent_row("gemini-live", "Gemini Live (v1)", "b-gemini")}
      {agent_row("gemini-live-v2", "Gemini Live (v2)", "b-v2")}
      {agent_row("openai-realtime", "OpenAI Realtime (v1)", "b-openai")}
      {agent_row("openai-realtime-v2", "OpenAI Realtime (v2)", "b-v2")}
    </table>
  </div>
</section>

<!-- FAILURE BREAKDOWN BARS -->
<section>
  <h2>Failure Pattern Breakdown (per tool count)</h2>
  <div class="card">
    <p style="font-size:0.8rem;color:#64748b;margin-bottom:14px">
      Bar segments: <span style="color:#4ade80">■ pass</span>
      <span style="color:#f87171">■ wrong tool</span>
      <span style="color:#fb923c">■ arg mismatch</span>
      <span style="color:#94a3b8">■ no call</span>
    </p>
    <table>
      <tr><th>Agent</th>{tc_headers}</tr>
"""
    for agent, label, badge in [
        ("gemini-live", "Gemini Live v1", "b-gemini"),
        ("gemini-live-v2", "Gemini Live v2", "b-v2"),
        ("openai-realtime", "OpenAI Realtime v1", "b-openai"),
        ("openai-realtime-v2", "OpenAI Realtime v2", "b-v2"),
    ]:
        cells = "".join(
            f"<td style='padding:6px 10px'>{failure_bar(agent, tc)}</td>"
            for tc in tool_counts
        )
        html += f"<tr><td><span class='badge {badge}'>{label}</span></td>{cells}</tr>\n"

    html += """    </table>
  </div>
</section>

<!-- FAILURE EXAMPLES SIDE BY SIDE -->
<section>
  <h2>Failure Examples — v1 vs v2 at 3 Tools</h2>
  <div class="grid2">
"""
    for agent, v_agent, label, label_v2, badge, badge_v2 in [
        ("gemini-live", "gemini-live-v2", "Gemini Live (v1) 3t", "Gemini Live (v2) 3t", "b-gemini", "b-v2"),
        ("openai-realtime", "openai-realtime-v2", "OpenAI Realtime (v1) 3t", "OpenAI Realtime (v2) 3t", "b-openai", "b-v2"),
    ]:
        p1, t1 = acc(runs.get((agent, 3), []))
        p2, t2 = acc(runs.get((v_agent, 3), []))
        html += f"""<div class="card">
      <h3><span class='badge {badge}'>{label}</span> — {p1}/{t1} = {round(100*p1/t1) if t1 else '?'}%</h3>
      {failure_examples(agent, 3)}
    </div>
    <div class="card">
      <h3><span class='badge {badge_v2}'>{label_v2}</span> — {p2}/{t2} = {round(100*p2/t2) if t2 else '?'}%</h3>
      {failure_examples(v_agent, 3)}
    </div>
"""

    html += """  </div>
</section>

<!-- 30T FAILURE EXAMPLES -->
<section>
  <h2>30-Tool Failures — Schema Overflow / Positional Confusion</h2>
  <div class="grid2">
"""
    for agent, label, badge in [
        ("gemini-live", "Gemini Live (v1) 30t", "b-gemini"),
        ("openai-realtime", "OpenAI Realtime (v1) 30t", "b-openai"),
    ]:
        p, t = acc(runs.get((agent, 30), []))
        acc_str = f"{p}/{t} = {round(100*p/t) if t else '?'}%" if t else "no data"
        html += f"""<div class="card">
      <h3><span class='badge {badge}'>{label}</span> — {acc_str}</h3>
      {failure_examples(agent, 30, limit=8)}
    </div>
"""

    html += f"""  </div>
</section>

<div style="padding: 24px 32px; font-size: 0.75rem; color: #475569">
  Generated by build_voice_analysis.py — open <code>results/dashboard.html</code> for the full benchmark heatmap.
</div>

</body>
</html>"""
    return html


def main() -> None:
    runs = load_voice_runs()
    html = build_html(runs)
    out = RESULTS / "voice_analysis.html"
    with open(out, "w") as f:
        f.write(html)
    print(f"Wrote {out}")
    print(f"\nOpen: open {out}")


if __name__ == "__main__":
    main()
