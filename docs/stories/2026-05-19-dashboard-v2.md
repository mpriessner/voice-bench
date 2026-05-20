# Story: Dashboard v2

**ID:** 2026-05-19-dashboard-v2
**Status:** Reviewed — awaiting approval

## Prerequisites (depend on Story 1)

This story assumes Story 1 has shipped:
- `TurnTimeline.model_kind: Literal["voice", "text"]` exists (in
  `models.py`) with default `"voice"`.
- `TurnTimeline.ttf_request_to_call_ms` property exists (text-only).
- `Score.ttf_request_to_call_ms` field exists in `scoring.py`.
- `TerminalReason.OUT_OF_TOOL_SCOPE` enum value exists in `models.py`.
- `runner.py` excludes `OUT_OF_TOOL_SCOPE` rows from
  `passed`/`total` in the summary (currently `runner.py:170-172`
  counts every row — fix as part of Story 1).
- `runner.py` writes the `manifest` field into each JSONL line so
  this story can read it directly.

If any of these are missing when implementation begins, complete
them as part of Story 1 first. Do not re-implement them here.
**Created:** 2026-05-19

## Goal

Rebuild `results/dashboard.html` so it scales to the post-Story-1 world of
6 text models × 3 tool counts (≈ 18 runs) plus the existing 2 voice models.
Add the three views the user explicitly asked for: an **accuracy heatmap**
(model × tool count), a **latency comparison** (split by `model_kind`), and
a **failure explorer** (utterance → expected tool → actual tool called →
args diff). Keep `voice-bench probe`/`run` outputs as the only sources of
truth; the dashboard is a pure read-side artifact.

## Context

The current dashboard at `results/dashboard.html` + `results/dashboard_template.html`
+ `scripts/build_dashboard.py` is a single-page Chart.js view with a
manifest v1/v2 toggle, agent-colored badges, and a flat results table.
It was built for 4 agents and ~5 tool counts. With Story 1 it will need
to surface:

- **8 agents** (2 voice + 6 text) — colors and legends need to scale.
- **`model_kind` field** (added in Story 1's `TurnTimeline`) — latency
  must be plotted by kind, not on a single shared axis.
- **`ttf_request_to_call_ms` vs `ttf_tool_ms`** — two distinct latency
  fields. Default chart uses each according to row's `model_kind`.
- **`OUT_OF_TOOL_SCOPE` terminal reason** (added in Story 1) — those
  rows are excluded from accuracy but should still be visible in the
  failure explorer with a clear label.

Existing pipeline: each run writes `results/<run_id>.jsonl`.
`scripts/build_dashboard.py` reads all `*.jsonl`, rescores them with the
current scoring logic via `_rescore()` (so changes to scoring don't
require re-running benchmarks), and inlines the resulting rows into the
HTML template. **Keep this pipeline.** The user expressed appreciation
that the dashboard rescores rather than caches stale scores.

### What's wrong with the v1 dashboard for the new scale

1. Manifest detection by `len(run_tools) >= 15` (`build_dashboard.py:96`)
   is brittle. Story 1 introduces `manifest_text_eval.json` which has
   30 distinct expected_tools — that would be misclassified.
2. The current accuracy chart is a single bar per agent per tool count.
   With 8 agents × 3 tool counts that becomes 24 bars — unreadable.
   A heatmap is dense and scannable.
3. Latency chart plots `ttf_tool_ms` across all rows. For text rows
   that field is now None (per Story 1's design), so text models would
   simply disappear from the chart.
4. The failure table shows up to N rows but has no filter for "show me
   wrong-tool calls only" or "show me one specific prompt across all
   models."
5. There's no model-vs-model side-by-side view, which is the question
   the user actually wants to answer for the orchestrator pick.

## Acceptance Criteria

- [ ] **Accuracy heatmap** view (default landing): rows = agent, cols =
      tool count, cells colored by pass rate (red 0% → green 100%) with
      the pass-rate number printed in each cell. Hover shows
      `passed/total` and the run_id.
- [ ] **Latency chart** split by `model_kind`:
      - voice models: `ttf_tool_ms` (P50, P95) per agent at each tool count
      - text models: `ttf_request_to_call_ms` (P50, P95) per agent at each tool count
      - Two separate panels, side-by-side or stacked — no shared y-axis,
        no mixed-modality bars.
- [ ] **Failure explorer**:
      - Table with filters: agent, tool_count, manifest, failure_kind
        (`wrong_tool`, `arg_mismatch`, `no_tool`, `out_of_scope`,
        `provider_error`).
      - Columns: prompt_id, prompt_text, expected_tool,
        called_tool, expected_args, actual_args, arg_score, ttf, run_id.
      - Click a row → expanded JSON view with full raw_events for that
        turn.
- [ ] **Model comparison** view: pick any two agents from a dropdown,
      see their accuracy + latency side-by-side at every tool count
      they share, with a third column highlighting where they disagreed
      on the same `prompt_id` (one passed, the other failed).
- [ ] **Manifest detection** updated to be explicit, not heuristic. Each
      JSONL row gets its manifest name embedded at run time
      (`runner.py` already knows the manifest path); the dashboard
      reads that field instead of guessing from the tool count.
- [ ] **`OUT_OF_TOOL_SCOPE` rows visible** in the failure explorer with
      a distinct color/badge, but excluded from accuracy calculations
      (`runner.py` already excludes them from `passed/total`; verify
      the dashboard mirrors that).
- [ ] **Stats header** updated: total turns, total agents, total runs,
      manifest count, last-run timestamp. The current header at
      `dashboard_template.html` has 4 stat cards; keep that layout.
- [ ] **No new build dependencies** beyond the current Chart.js CDN.
      No React, Vue, htmx, etc.
- [ ] **Multi-file static layout** (revised after Gemini review): the
      dashboard is no longer a single 14 MB HTML file. Files:
      `results/dashboard.html` (lean template, ~100 lines),
      `results/dashboard.js` (presentation logic),
      `results/dashboard.css` (styles),
      `results/data.js` (build-generated `window.__DATA__ = [...]`).
      The HTML loads the three siblings via `<script src>` and
      `<link href>` — works under `file://` because they're loaded as
      script/style, not fetched.
- [ ] **Phase 2A vs 2B delivery split** (revised after Gemini review).
      Treat the four views as two implementation milestones — both
      ship under this story ID, just sequentially, with a separate
      smoke run between them:
      - **Phase 2A**: data pipeline + heatmap + latency split.
      - **Phase 2B**: failure explorer + model comparison.
      The user gets the aggregate views first (which they'll use
      most), then the drill-downs.
- [ ] All existing tests still pass.
- [ ] `uv run python scripts/build_dashboard.py` regenerates the page
      cleanly from all `results/*.jsonl` files in <10s.

## Implementation Plan

## Phase 2A — pipeline + aggregate views

### Step 1 — Embed manifest name in JSONL rows

In `runner.py:148-154` add a `manifest` key to the JSONL line dict, set
from the manifest path (`manifest_path.stem`). Update `build_dashboard.py`
to read this key directly, with the heuristic at line 86-96 kept as a
fallback for rows written before this change.

### Step 2 — Rescore handles `model_kind`, latency, terminal reasons

Update `_rescore` in `scripts/build_dashboard.py:26-63` to:
- Pass `model_kind` from JSONL into the reconstructed `TurnTimeline`
  (the `_TL_FIELDS` filter at line 22 will need refresh).
- Surface both `ttf_tool_ms` and `ttf_request_to_call_ms` in the
  resulting `Score`.
- Default `model_kind = "voice"` for old rows that don't have it.

Fix `_rescore` line 43-46: currently unknown `terminal_reason` values
fall through to `TURN_COMPLETE` silently. Change this to **preserve
the raw string** if it's not a known enum value, so historical and
forward-written rows like `OUT_OF_TOOL_SCOPE` are not lost.

Extend `_failure_kind` (`build_dashboard.py:66-73`) to recognize:
- `out_of_scope` when `terminal_reason == "OUT_OF_TOOL_SCOPE"`
- `provider_error` when `terminal_reason == "PROVIDER_ERROR"`
- `timeout` when `terminal_reason in {"TIMEOUT_CONNECT",
  "TIMEOUT_FIRST_TOOL", "TIMEOUT_FIRST_AUDIO"}`
- existing `pass` / `no_tool` / `wrong_tool` / `arg_mismatch`

Add `terminal_reason` and `turn_id` to the row dict emitted by
`load_all_rows` (currently `build_dashboard.py:115-132` omits both).
`turn_id` is needed for unambiguous detail-row lookup.

### Step 3 — Heatmap component (HTML+CSS, no plugin)

**Decision change after Codex review**: skip `chartjs-chart-matrix`.
The matrix plugin doesn't print labels in cells by default (would need
a custom plugin), and adding any new CDN runtime dep conflicts with
the "no new build dependencies" AC. The HTML+CSS grid fallback was
already on the table — promote it to the default.

Implementation: a `<table>` with cells styled via inline CSS:
```javascript
const pct = passed / total;
const hue = Math.round(120 * pct);  // 0 (red) → 120 (green)
cell.style.backgroundColor = `hsl(${hue}, 60%, 35%)`;
cell.textContent = `${Math.round(pct * 100)}%`;
cell.title = `${passed}/${total}\nrun_id: ${run_id}`;
```

Heatmap source data: aggregate rows by `(agent, tool_count, manifest)`,
compute `passed_count / total_count`, render one matrix per manifest
(stacked vertically with a heading). Sort agents within each manifest
by their highest-tool-count accuracy descending so the best models
float to the top. Exclude `OUT_OF_TOOL_SCOPE` rows from the
denominator.

### Step 4 — Split latency charts by `model_kind`

In `dashboard.js` (new sibling file per Step 9), split rows into
`voice_rows` and `text_rows`, build two separate Chart.js bar/line
charts. Voice chart uses `ttf_tool_ms`; text chart uses
`ttf_request_to_call_ms`.

P50 and P95 computed in JS from the per-agent-per-tool-count array of
latency values. Use a `quantile(sorted, p)` helper.

**Phase 2A endpoint**: after Step 4, the dashboard supports heatmap +
latency split. Smoke run, demo to user, then start Phase 2B.

## Phase 2B — drill-downs

### Step 5 — Failure explorer with filters

> *Phase 2B work.*

**Filter dropdowns are generated dynamically from `allRows`**, not
hard-coded (Codex flagged the existing template at
`dashboard_template.html:135` hard-codes 4 agents and 3 failure types):

```javascript
function populateFilters(allRows) {
  fillSelect('agent-filter', uniqueSorted(allRows, 'agent'));
  fillSelect('toolcount-filter', uniqueSorted(allRows, 'tool_count'));
  fillSelect('manifest-filter', uniqueSorted(allRows, 'manifest'));
  fillSelect('kind-filter', uniqueSorted(allRows, 'failure_kind'));
}
```

Failure kinds visible in dropdown: `pass`, `no_tool`, `wrong_tool`,
`arg_mismatch`, `out_of_scope`, `provider_error`, `timeout` (all
from Step 2's expanded `_failure_kind`).

**Filter curation** (Gemini flagged scale concern): runs auto-named
with a `test-` or `scratch-` prefix in `--run-id` are excluded from
the filter dropdowns by default; a "show all" toggle includes them.
Also default the agent filter to the 5 agents with the most recent
runs in the last 14 days (`run_ts`-based); a "show legacy" toggle
brings the rest back. Prevents the dropdown from accumulating stale
entries as the benchmark grows.

- Filter row: 5 inputs total (4 dropdowns + 1 prompt-substring search).
- Table body re-rendered on filter change (vanilla JS — no framework).
- Row click toggles a sibling `<details>` element showing the raw
  `prompt`, `result`, and `score` from the JSONL. Detail data is
  **fully inlined** at build time (see Step 9 — single inline data
  strategy), looked up by `(run_id, turn_id)` composite key.

For arg-diff visualization in the expanded view: a two-column
side-by-side JSON view with red/green highlights on keys that differ.
Simple JS — no diff library — because args are flat 1-3 key objects.

### Step 6 — Model comparison view

> *Phase 2B work.*

Add a new top-level tab/section. Two `<select>` dropdowns populated
with all agents that have at least one run. On selection:
- Show accuracy table: rows = `(manifest, tool_count)` pairs present
  in both runs, cols = agent A pass rate, agent B pass rate, delta.
- Show latency table: same layout, with the right field per
  `model_kind`. If A is voice and B is text, show them in separate
  columns (P50/P95 each) and **do not compute a delta** — they're
  not comparable.
- Show disagreement table: prompts where one passed and the other
  failed at the same `(manifest, tool_count, prompt_id)`. Columns:
  prompt_text, A's call, B's call. If a prompt is only present in one
  of the two runs (e.g. filtered out by `runner.py:91-95`), it does
  not appear in the disagreement table.

### Step 7 — Restyle to handle 8+ agent colors

Current CSS at `dashboard_template.html:33-42` hard-codes 4 badge
classes (`badge-gemini`, `badge-openai`, `badge-claude`, `badge-gpt`).
Add classes for `badge-claude-sonnet`, `badge-gpt-4o`,
`badge-gemini-pro`, `badge-gemini-flash`. Pick palette-spread colors
that survive both light and dark backgrounds.

Or simpler: hash the agent name to an HSL color, render badge inline
style. Eliminates the per-agent class entirely. **Decision:** use the
hash approach — less work, scales to any number of agents.

### Step 8 — Replace manifest toggle with dynamic dropdown

> *Phase 2A work (lifted up — needed for heatmap labels).*

The current template at `dashboard_template.html:230` hard-codes
manifest names `v1`/`v2` and the active default to `v2`. With
`manifest_text_eval`, `manifest_v3`, etc. landing later, this is a
ticking time bomb.

Replace the manifest toggle pills with a `<select>` populated from
`uniqueSorted(allRows, 'manifest')`. Default to the manifest with the
most recent run (by JSONL mtime, exposed from the build script as a
per-row `run_ts` field — see Step 9).

### Step 9 — Sibling `data.js` instead of inlined HTML

**Gemini correctly flagged** that 14 MB inlined into `dashboard.html`
ruins IDE syntax highlighters and forces parse-on-load main-thread
stutter. Resolve by writing data to a sibling file:

- `scripts/build_dashboard.py` writes
  `results/data.js` with `window.__DATA__ = [...]; window.__DETAILS__ = {...};`.
- `dashboard.html` loads it: `<script src="data.js"></script>` — works
  under `file://` because it's a script tag, not a `fetch()`. Tested
  pattern, well-known.
- HTML stays under 200 lines. CSS in sibling `dashboard.css`. JS in
  sibling `dashboard.js`. Build script touches only `data.js`.

Splitting `__DATA__` (lightweight per-row summary) from `__DETAILS__`
(per-turn raw_events keyed by `(run_id, turn_id)`) lets the heatmap
and latency charts render before `__DETAILS__` finishes parsing —
the user sees results immediately, detail expansion comes a beat
later.

Row keying: every row dict gets `turn_id` and `run_ts` (file mtime
of the source JSONL) so detail lookup and "most recent" sorting are
unambiguous.

### Step 10 — Run aggregation keying

Fix `aggregateRuns()` at `dashboard_template.html:194` to key by
`(run_id, agent, tool_count, manifest)`. Run IDs from
`--run-id` overrides can repeat across agents/manifests; the existing
single-key aggregator silently merges them.

### Step 11 — Acceptance smoke

```bash
uv run python scripts/build_dashboard.py
open results/dashboard.html
```

Verify each view renders without console errors with the post-Story-1
data set (8 agents × 1-3 tool counts) and with the pre-existing
voice-bench result set (130+ JSONL files). Specifically:
- Heatmap renders both manifests stacked.
- Latency split shows voice and text panels populated.
- Failure explorer's filter dropdowns include every agent and every
  failure_kind present in data.
- Model comparison handles voice vs text selection (latency: no
  delta column when modalities differ).
- Old rows without `model_kind` default to voice and still pass-fail
  correctly.

## Risks & Open Questions

1. **Inline data size**. ~14 MB expected with all detail data inlined
   (Step 9). `file://` lazy fetch is ruled out — Codex confirmed
   browser CORS blocks it in Chrome/Safari/Firefox. **Mitigation:**
   accept 14 MB; if it crosses 30 MB add `DecompressionStream`-based
   gzipped inline data as a follow-up. The dashboard loads slowly
   once at start and stays interactive; that's the tradeoff.

2. **Old result rows lack `model_kind`, `manifest`, `turn_id` fields**.
   `_rescore` defaults to voice for `model_kind`; uses the existing
   heuristic as fallback for `manifest`; treats missing `turn_id` as
   `prompt_id` (best-effort key — may produce duplicate detail lookups
   on reruns of the same prompt). **Verify on the existing 130+ JSONL
   files in `results/` before shipping.**

3. **The `_rescore` path is a feature** (user appreciation noted), but
   it means schema changes to `models.py` propagate to every historical
   row. Adding optional fields with defaults (per Story 1) is the
   safe pattern; required new fields would silently break old runs.
   Keep all new fields optional.

4. **Single-file constraint conflicts with code organization**. The
   dashboard is 1 HTML file with inline JS+CSS. After Story 2 it'll
   have ~600 lines of JS. **Mitigation:** split JS into named blocks
   with `// === SECTION: heatmap ===` comments. Don't split into
   multiple files — the open-in-browser ergonomic is the whole point.

5. **Run-ID overrides break ordering** (Codex flagged). Existing code
   parses the trailing timestamp out of the auto-generated run_id
   (`<agent>-<tools>t-<ts>`); a user passing `--run-id custom-name`
   has no timestamp. **Mitigation:** the build script attaches each
   row's source-file mtime as `run_ts`, used for "most recent"
   sorting and the stat header's last-run display. The trailing-`-`
   parse logic is dropped from the frontend.

6. **Custom run-IDs and aggregation**. Codex flagged that
   `aggregateRuns()` keying only on `run_id` would silently merge
   distinct runs that happen to share an override. Step 10 changes
   the key to `(run_id, agent, tool_count, manifest)`.

## Out of Scope

- A backend server (`voice-bench serve`). The user explicitly wants
  the open-in-browser static file workflow.
- Authentication / sharing. Local-only artifact.
- Time-series view of accuracy across reruns. Each rerun is a new
  JSONL so it would be doable, but no one has asked for it.
- Cost tracking per model. Story 4 may surface this if the orchestrator
  cost matters; not here.
- Export to PDF / CSV from the dashboard. The underlying CSVs already
  exist next to each JSONL.
- gzipped inline data via DecompressionStream — only if total payload
  crosses 30 MB. Sibling-file split (Step 9) gets us most of the way.

## Reviewer Feedback

### Codex (round 1)

**Critical issues raised:**
1. `file://` lazy `fetch()` for JSONL raw events likely fails due to
   browser CORS.
2. `model_kind`, `ttf_request_to_call_ms`, `OUT_OF_TOOL_SCOPE` are
   Story-1 schema work, not dashboard-only changes.
3. Failure kinds at `build_dashboard.py:66-73` cannot produce
   `out_of_scope` or `provider_error` without expansion.
4. Manifest names from `manifest_path.stem` don't match the current
   `v1`/`v2` UI assumptions.

**Nice-to-have raised:**
- Prefer HTML/CSS heatmap unless matrix labels are implemented.
- Generate filter options dynamically from `allRows`.
- Add tests for `load_all_rows()` edge cases.

**Resolution:**
- Added Prerequisites section pulling Story-1 dependencies forward.
- Step 9 commits to fully-inlined data (no fetch); Risk #1 rewritten.
- Step 3 commits to HTML+CSS heatmap (no plugin).
- Step 5 specifies dynamic filter population.
- Step 8 replaces manifest pills with dynamic dropdown.
- Step 10 fixes aggregation keying.
- Step 2 fixes `_failure_kind` to recognize the missing categories,
  and `_rescore` to preserve unknown terminal_reason strings.

### Gemini Pro (round 2)

**Architectural concerns raised:**
1. 14 MB inlined in HTML destroys IDE ergonomics; main-thread parse
   stutter on load.
2. Four complex UI features + pipeline changes in one story is too
   much for clean review and incremental ship.
3. Pure-dynamic filter dropdowns accumulate cruft as benchmark grows.
4. Single HTML file is an anti-pattern at 600+ lines of vanilla JS.

**Alternatives suggested:**
- Sibling `data.js` containing `window.__DATA__` (script-tag load
  sidesteps `file://` CORS).
- Phased delivery: 2A aggregates, 2B drill-downs.
- Filter curation (recency / prefix-exclude).
- Extract JS/CSS to sibling files.

**Resolution:**
- **Adopted all four.** Step 9 rewritten to write `data.js` instead
  of inlining; AC updated to allow sibling JS/CSS files; Steps 5+6
  marked as Phase 2B; filter curation added to Step 5.
- Single-file constraint relaxed — the user's requirement is
  "opens in browser locally," which sibling files satisfy via
  `<script src>`. The IDE / maintainability win outweighs the
  rigid single-file rule.

## Revision History

- 2026-05-19 — Initial draft
- 2026-05-19 — Round 1: Codex feedback (4 critical, 3 nice-to-haves
  adopted)
- 2026-05-19 — Round 2: Gemini Pro feedback (4 architectural concerns
  all adopted — sibling files, phased delivery, filter curation)
