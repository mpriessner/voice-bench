# Story: Dynamic Tool-Pool Swap (OpenAI Realtime)

**ID:** 2026-05-19-openai-realtime-dynamic-tool-pool-swap
**Status:** Reviewed — awaiting approval
**Created:** 2026-05-19

## Goal

Add an **opt-in** dynamic tool-pool swap mechanism to the OpenAI Realtime
adapter: a small always-loaded "core kit" (5–8 tools, including
`switch_toolset` and `list_toolsets`) plus a swappable pool of 15–20
specialised tools. The model itself drives swaps mid-session via
`session.update`. Existing single-turn benchmark paths must continue to
work unchanged.

## Context

Our existing benchmarks (`results/voice_analysis.html`, `results/dashboard.html`) show
voice agents collapse from 98–100% at ≤20 tools to ~40% at 30 tools — a
structural cliff caused by **positional schema confusion** (the model
selects the tool one or two slots away from the correct one in the
session schema). Keeping the visible schema below the cliff while still
exposing many tools requires either (a) two-tier routing with a separate
text model — already built in `runner.run_pipeline_benchmark` — or
(b) a dynamic pool that swaps in-place. (b) keeps everything inside the
voice model and lets us scale to 100+ tools while the model only ever
sees ~25.

OpenAI Realtime supports this natively. `session.update` can replace
`session.tools` at any time on an open WebSocket; the server confirms with
`session.updated`. Gemini Live does **not** support this and is deferred
to a separate story (requires session-resumption restart, ~2–5s latency
cost).

The pattern matches 2025–2026 research (ITR, ScaleMCP, ToolACE-MCP,
MemTool, Dynamic ReAct, ToolGen) where dynamic tool retrieval is the
mainstream answer to large tool registries.

### Why existing infrastructure does not test this

`runner.run_benchmark` opens a fresh connection per prompt via
`adapter.run_turn` (see `src/voice_bench/runner.py:119-188` and the
`async with self.client.realtime.connect(...)` at
`src/voice_bench/adapters/openai_realtime.py:155`). Each turn is a brand-new session.
A single-turn run can never observe a swap because the session is born,
the audio is sent, the tool is called, and the session is closed —
nothing to swap mid-flight. So we need a **second runner path** that
holds a session open across multiple turns. This is the largest piece
of new structure in the plan.

### Toolset inventory (current tools.py)

50 `DummyTool` definitions in two categories: **app** (27) and
**chemistry** (23). For a 15–20 tool pool we need finer slices than the
two existing categories. The tier groupings in `tools.py`
(`TIER_1_TOOLS` … `TIER_7_TOOLS`) are a more useful basis but are still
coarse. The plan defines four toolsets explicitly rather than auto-deriving
from category, so the test universe is stable across runs.

## Acceptance Criteria

- [ ] **AC1** — `uv run voice-bench probe --agent openai-realtime` still passes
  (no regression on the existing adapter path).
- [ ] **AC2** — `uv run voice-bench run --agent openai-realtime --tools 10 --mode smoke`
  still produces the same shape of JSONL/CSV/dashboard rows as today
  (no regression on the existing benchmark path).
- [ ] **AC3** — A new `uv run voice-bench probe-swap --agent openai-realtime-swap`
  command verifies that `session.update` with a new tools array on an
  already-open session causes the server to emit `session.updated` and
  the model can subsequently call a tool from the new set.
- [ ] **AC4** — A new `uv run voice-bench swap --agent openai-realtime-swap --manifest manifest_swap`
  command runs a multi-turn scenario benchmark and writes results to a
  `…_swap.jsonl` file with one row per **turn within a scenario**, plus
  `scenario_id` / `turn_index` / `toolset_at_call` columns.
- [ ] **AC5** — Each scenario in `manifest_swap.json` exercises a swap
  at least once. Scoring captures, per scenario:
  - `scenario_passed` — final task tool fired correctly with right
    args
  - `swap_precision` — % of `switch_toolset` calls that targeted the
    expected toolset on the first attempt
  - `extra_swaps` — count of redundant swap calls beyond the
    minimum needed
  - `end_to_end_latency_ms` — wall time from first user input to
    final task tool call within the scenario (the metric a voice UX
    actually cares about)
- [ ] **AC6** — Dashboard reads new swap rows without crashing.
  Showing them prettily in the heatmap is out of scope; minimal display
  is fine. The swap rows must use a shape that the pipeline benchmark
  runner (`run_pipeline_benchmark`) could also emit, so a future
  follow-up can plot **swap vs pipeline** side-by-side on the same
  axes.
- [ ] **AC7** — `manifest_swap.json` scenarios are designed so the
  existing two-tier pipeline benchmark (`run_pipeline_benchmark`) can
  also score them. Concretely: each scenario's final turn has the same
  fields a pipeline benchmark needs (`expected_tool`,
  `expected_args`, and the corresponding `expected_category`). Running
  pipeline mode on `manifest_swap` is **not** part of this story, but
  the data shape must permit it.
- [ ] **AC8** — A `--pool-size N` flag on the `swap` command lets the
  benchmark be re-run with auto-bucketed pools of size 5/10/15/20/25
  drawn from `ALL_TOOLS` (parametric mode). The hand-picked toolsets
  remain the default; the parametric mode is the rigorous degradation-curve
  test.
- [ ] **AC9** — README has a 5-line "Dynamic tool-pool swap" section
  pointing at the probe script and the new CLI command.

## Implementation Plan

### Phase 0 — Standalone prototype probe (RISK GATE)

Before any adapter changes, write `scripts/probe_session_update.py`
(model on the `probe_openai_realtime.py` pattern).

**Critical SDK facts captured up-front** (verified from
`.venv/.../openai/types/beta/realtime/`):

- `response.function_call_arguments.done` does **not** carry the
  function `name`. It has `arguments`, `call_id`, `item_id`,
  `output_index`, `response_id`. Function name lives on the
  containing item; track it from `response.output_item.added` (which
  carries `item.name` and `item.call_id` on a `function_call`-typed
  item) and key by `call_id`.
- `session.update` accepts a client `event_id`. The matching
  `session.updated` echoes that id, which is the **only** reliable way
  to know which update an ack belongs to.
- `function_call_output` requires the matching function-call item to
  already exist in conversation history. Emit it only after observing
  `response.output_item.added` for the call (or the equivalent
  `conversation.item.created`).

The probe must:

1. Open a session with **toolset A** (3 tools: `toggle_flash`,
   `toggle_grid_overlay`, `toggle_macro_mode`).
2. Drive a synthetic text turn:
   - `conversation.item.create(item={"type": "message", "role":
     "user", "content": [{"type": "input_text", "text": "Turn on the
     flash"}]})`
   - `response.create()`
   - Receive loop until `response.done`. Track tool calls via
     `response.output_item.added` (capture `name` + `call_id`) →
     `response.function_call_arguments.done` (capture `arguments` by
     `call_id`).
3. Send `session.update` with **toolset B** (3 tools: `set_exposure`,
   `set_zoom`, `set_iso`) using an explicit `event_id`. Drain events
   until a `session.updated` with that `event_id` arrives. Record
   round-trip latency. Reject any other `session.updated` events that
   may belong to earlier client updates (e.g. the initial setup).
4. Drive a second text turn ("Set ISO to 400"). Confirm a tool call to
   `set_iso`. Confirm the model does **not** call a toolset-A tool.
5. Repeat one more swap: B → A. Then ask "Hide the flash overlay" —
   confirm the model swaps or politely declines but does NOT call a
   removed tool from B (test the R2 / R3 risks).
6. Print: `session.update` round-trip latency (3 samples), total
   scenario duration, and whether the model ever invoked an
   out-of-pool tool name.

**Gate condition**: if the probe fails (server ignores tool changes,
model gets confused or stops calling tools, latency much worse than
docs imply, or the model attempts to call removed tools), stop here
and revisit the approach. If it succeeds, continue to Phase 1.

The probe uses synthetic text input rather than audio, to isolate the
swap behaviour from audio plumbing. Audio is added later in the
benchmark.

### Phase 0.5 — Wiring prerequisites (BEFORE Phase 1 can compile)

These changes are mandatory blockers and must land first, even though
they are tiny:

- Add `"openai-realtime-swap"` to `VOICE_AGENTS` in
  `src/voice_bench/cli.py:14`.
- Add the registry entry in
  `src/voice_bench/adapters/registry.py` (point at the new adapter,
  which initially can be an empty subclass so imports work).
- Extend `Literal["voice", "text"]` for `model_kind` in
  `src/voice_bench/models.py` to include `"voice_swap"`. This is a
  contract change that several places assume; touch all sites:
  `TurnTimeline`, dashboard, scoring.
- Extend `ToolCallEvent` with optional `toolset_at_call: str | None =
  None`, and `TurnTimeline` with optional `swap_request_ms: int |
  None = None`, `swap_ack_ms: int | None = None`. Defaults preserve
  current behaviour for non-swap runs.

Without these, `click.Choice(VOICE_AGENTS)` rejects the agent name
before any new code runs, and JSON serialisation refuses the new
fields.

### Phase 1 — Toolset definitions

Add `src/voice_bench/toolsets.py`:

```python
from .tools import DummyTool, ALL_TOOLS

# CORE: always loaded.  Keep small — 5–8.
# (Defined below the toolset map so we can reference both halves.)

# TOOLSETS: 4 toolsets, ~15 tools each, total ≤ 50.
TOOLSETS: dict[str, list[str]] = {
    "basic_capture":   [<list of tool names — toggle_flash, take_photo, switch_camera, ...>],
    "imaging_quality": [<set_exposure, set_iso, set_focus_distance, ...>],
    "chemistry":       [<configure_session, sync_to_eln, set_sample_label, ...>],
    "microscopy":      [<set_microscope_objective, toggle_focus_peaking, ...>],
}

CORE_TOOL_NAMES: list[str] = [
    "switch_toolset",       # meta — synthesised in toolsets.py
    "list_toolsets",        # meta — synthesised in toolsets.py
    "take_photo",           # universal — exists in tools.py
    "start_recording",      # universal — exists in tools.py
    "apply_preset",         # universal — exists in tools.py
    "configure_capture",    # universal — exists in tools.py
]
```

**Do not introduce `stop_recording`** — it is not in `tools.py` today.
The original draft listed it; adding it would alter `ALL_TOOLS`
ordering and tool counts, breaking existing benchmark snapshots.
Use only tools that already exist in `tools.py`.

`switch_toolset` and `list_toolsets` are new `DummyTool` instances
defined in `toolsets.py` (not in `tools.py`, to keep the existing tool
registry untouched). Their `.parameters` schema:

```json
// switch_toolset
{"type": "object",
 "properties": {"name": {"type": "string",
                          "enum": ["basic_capture", "imaging_quality",
                                   "chemistry", "microscopy"]}},
 "required": ["name"]}

// list_toolsets  → no params
{"type": "object", "properties": {}}
```

Hand-pick the tool names in each set so each set is ≤17 tools and they
don't fully overlap.

**Two pool generation strategies, both supported by the runner:**

1. **Static (default)**: hand-picked `TOOLSETS` dict above. Easier
   to reason about, scenarios written against named toolsets.
2. **Parametric** (`--pool-size N` flag): the runner auto-buckets
   `ALL_TOOLS` into chunks of size N (default 15). Toolset names
   become `pool_1`, `pool_2`, etc. This lets us plot a degradation
   curve as a function of pool size (5/10/15/20/25) — the rigorous
   structural test. Scenarios for parametric mode are simpler ("call
   tool X" without semantic-toolset-name dependencies).

Add helpers, and **hard-fail on invalid configuration at import
time**: Add helpers, and **hard-fail on invalid
configuration at import time**:

```python
def _validate() -> None:
    """Called at import. Raises if toolset config is invalid."""
    all_names = {t.name for t in ALL_TOOLS}
    for toolset_name, names in TOOLSETS.items():
        missing = [n for n in names if n not in all_names]
        if missing:
            raise ValueError(f"Toolset {toolset_name}: unknown tools {missing}")
        if len(names) > 17:
            raise ValueError(f"Toolset {toolset_name}: {len(names)} > 17 cap")
    # Detect schema collisions: same name in core + a pool with diff schema
    # Strategy: just disallow shared names between core and any pool.
    pool_names = {n for names in TOOLSETS.values() for n in names}
    if overlap := (pool_names & set(CORE_TOOL_NAMES)):
        raise ValueError(f"Tool names appear in both core and pool: {overlap}")

_validate()


def build_pool(toolset_name: str) -> list[DummyTool]:
    names = TOOLSETS[toolset_name]
    return [t for t in ALL_TOOLS if t.name in names]


def build_core() -> list[DummyTool]:
    """Returns DummyTool instances for CORE_TOOL_NAMES, synthesising
    the meta-tools switch_toolset and list_toolsets in-place."""
```

### Phase 2 — Swap adapter

Add `src/voice_bench/adapters/openai_realtime_swap.py`. Compose (do
**not** inherit) `OpenAIRealtimeAdapter` — the existing single-turn
state machine's invariants (one `response.create`, count two
`response.done` events, break) are incompatible with a multi-call,
multi-response-per-turn session. The swap adapter shares helpers
(`_build_tools`, `_session_config`, `_serialize`) by composition.

Key additions:

- **Session abstraction**: a new method `async def run_scenario(scenario,
  …) -> ScenarioResult`. Holds the connection open for multiple turns.
- **Current-toolset tracking**: instance attribute `_current_toolset:
  str | None` updated when `switch_toolset` completes acked.
- **Function-call name tracking** (critical): build a `dict[call_id,
  name]` from `response.output_item.added` events whose
  `item.type == "function_call"`. The args.done event references the
  `call_id`; resolve `name` via this map. **Never** rely on `ev.name`
  on the args.done event.
- **Tool-call dispatch**: when args.done arrives, look up `name` from
  the map. Branch on name:
  - `switch_toolset(args)`: validate `args["name"]` against
    `TOOLSETS`. On invalid, emit synthetic `function_call_output`
    `{"error": "unknown_toolset", "available": [...]}` and call
    `response.create()` to let the model recover. Do NOT call
    `session.update`.
  - `switch_toolset` valid:
    1. Emit `function_call_output` `{"result": "ok", "active":
       "<name>"}`.
    2. **Wait for the original `response.done`** before issuing the
       `session.update`. Sending the update mid-response leaves the
       schema-vs-history state ambiguous; the docs allow it but it's
       not worth the bug surface for a benchmark.
    3. Send `session.update` with a freshly generated client
       `event_id` (e.g. `f"swap-{uuid4().hex[:8]}"`). Body: full
       session config with `tools = core + pool[name]`.
    4. Drain events until receiving `session.updated` with the
       matching `event_id`. Buffer (do not discard) any other events
       that arrive in that window — put them onto the main event
       queue for the receive loop to process after the swap completes.
    5. Record `swap_request_ms` (when the update was sent) and
       `swap_ack_ms` (when the matching ack arrived).
    6. Set `self._current_toolset = name`.
    7. Issue a continuation `response.create()` so the model can carry
       on with the user's intent under the new pool.
  - `list_toolsets()`: synthesise output `{"toolsets": [{"name":
    "...", "summary": "..."}, ...]}` and `function_call_output`. The
    model continues via the original response (no new
    `response.create` needed — the response is still active).
  - Any other tool call: emit `function_call_output {"result": "ok"}`
    and let the existing response play out. Do **not** trigger a
    second `response.create` for these — that behaviour is specific
    to the single-turn adapter (which uses it to get the spoken
    confirmation TTFS). In the swap adapter, the next user turn
    drives the next `response.create`.
- **Per-turn receive loop**: each scenario turn runs a loop that
  consumes events until `response.done` for the active response *plus*
  any settled output audio (`response.output_audio.done` or
  `response.audio.done` if observed). On entering a turn:
  1. Optionally `input_audio_buffer.clear()` to flush any residue.
  2. For audio turns: append PCM chunks → `input_audio_buffer.commit`.
     Wait for `input_audio_buffer.committed` before issuing
     `response.create`.
  3. For text turns: `conversation.item.create` with role=user,
     `input_text` content. Wait for the matching
     `conversation.item.created`. Then `response.create`.
  4. Tool calls within the turn are handled as described above and
     may issue a continuation `response.create` (only in the
     switch_toolset valid path). The turn does not end until the
     **last** `response.done` for the chain.
- **tool_choice**: in the swap session config, use
  `tool_choice="auto"`, **not** `"required"`. The current adapter
  forces `required` whenever tools exist
  (`openai_realtime.py:76`). `required` forces a tool call for every
  response, which is wrong for control turns ("the user just said
  hi") and for `list_toolsets` recovery flow.
- **Preserve `run_turn`**: forward unchanged to the parent class
  (composition) so existing single-turn benchmarks still work; only
  the new `run_scenario` path uses the multi-turn session.

Critically, this adapter is **session-stateful**, but a fresh adapter
instance is created per scenario (registry pattern). State lives on
the adapter instance, not in a module-level global. The runner is
responsible for spinning up one adapter per scenario.

### Phase 3 — Cross-toolset manifest

Add `prompts/manifest_swap.json`. Schema (each entry = one scenario):

```json
{
  "id": "sw001",
  "description": "Calibrate then measure",
  "initial_toolset": "imaging_quality",
  "turns": [
    {"id": "sw001-1", "text": "Set ISO to 400.",
     "expected_tool": "set_iso", "expected_args": {"iso": 400},
     "expected_toolset_at_call": "imaging_quality"},
    {"id": "sw001-2", "text": "Now sync this experiment to the lab notebook.",
     "expected_swap_to": "chemistry",
     "expected_tool": "sync_to_eln",
     "expected_args": {"experiment_id": "*"},
     "expected_toolset_at_call": "chemistry"}
  ]
}
```

`expected_swap_to` is optional. When set, the turn passes only if the
model called `switch_toolset` with the expected name BEFORE calling the
expected tool. `expected_toolset_at_call` is the toolset that must be
loaded when the expected tool fires (verifies the swap completed).

Start with ~8 scenarios covering: no-swap (control), single forward
swap, swap back to a previous set, swap with `list_toolsets` first,
unknown-toolset request (recovery test), and a 3-step chain.

`expected_args` is allowed `"*"` for "any value acceptable" — the
scorer treats `"*"` as a wildcard.

### Phase 4 — Pre-rendered audio

The scenarios need audio fixtures. The existing `cli.gen-audio`
assumes a flat manifest with top-level `id` and `text` (`cli.py:138`);
it will crash on the scenario-nested swap manifest. Branch the
implementation:

- Detect scenario-shaped manifests by presence of `turns` key on the
  top-level objects. Iterate `turns[i].id` / `turns[i].text` instead
  of the top-level entry.
- Use the same voice-by-difficulty logic. Default difficulty `v1` for
  scenario turns if unspecified.

This phase can be deferred during Phase 0 probe iteration (text-mode
is enough). Audio is required before AC4. Add a `--text-mode` flag to
the swap runner so iteration is fast without re-rendering.

### Phase 5 — Scoring

Add `src/voice_bench/scoring_swap.py`. The existing `score_turn`
(`scoring.py:133`) takes `first_call` as the expected tool, which is
wrong for swap turns where `switch_toolset` legitimately precedes the
task tool. The new scorer operates on the **ordered list of tool
calls within a turn** and applies these rules:

1. Split `tool_calls` into `meta_calls` (calls whose name is in
   {`switch_toolset`, `list_toolsets`}) and `task_calls` (everything
   else, preserving order).
2. If the scenario specifies `expected_swap_to`, require that exactly
   one `switch_toolset` call with `name == expected_swap_to` appears
   in `meta_calls`, **and** that it appears at a position before any
   `task_calls`. Set `swap_called_when_expected = True/False`.
3. The "scored task call" is the FIRST `task_calls` entry. Apply the
   existing name/args matching logic (with `"*"` wildcard) to it.
4. `toolset_at_call_correct`: at the time the scored task call fired
   (from `ToolCallEvent.toolset_at_call`), the active toolset must
   match `expected_toolset_at_call`.
5. `recovery_passed` is only set on scenarios tagged
   `tests_recovery: true`. It requires: a `switch_toolset` call with
   invalid args fired, the synthetic error was acknowledged, and the
   task tool still eventually fires correctly.
6. **Extra-call penalty**: if `task_calls` has length > 1, mark
   `extra_task_calls = len(task_calls) - 1` but do not fail outright —
   surface it as a soft signal. Extra `switch_toolset` calls beyond
   what's needed do count as soft-fail (`extra_swaps`).

Per-scenario aggregate: `scenario_passed = AND of all turn-level
correctness fields AND swap_called_when_expected per turn`. Latency
fields per swap: `swap_request_to_ack_ms`. The scorer also reports
per-turn `terminal_reason` so failures distinguish provider errors
from logic errors.

### Phase 6 — Swap runner

Add `src/voice_bench/swap_runner.py` with `run_swap_benchmark(...)`.
Mirrors `run_benchmark` but iterates scenarios → turns inside one
adapter instance.

**Design intent**: the swap runner is a stepping stone toward a
**unified Session abstraction** (see Gemini round 2 alternatives).
Today the codebase has three runner shapes:

- `run_benchmark` — single-turn-per-prompt
- `run_pipeline_benchmark` — two-adapter routing per prompt
- `run_swap_benchmark` — multi-turn within one adapter

The architecturally correct end state is **one runner** that drives a
`Session` object built from a strategy (single-turn / pipeline /
swap). Refactoring all three into one is a separate story; this
story builds the third runner with a clean enough interface that
the unification is a refactor, not a rewrite. Specifically:

- `swap_runner.run_scenario(scenario, adapter, …) -> ScenarioResult`
  is the only place that contains multi-turn loop logic, and it
  takes the adapter as a parameter rather than instantiating it
  inside the runner. The existing `run_benchmark` could later
  invoke it with a single-turn scenario shape.
- The JSONL output shape is a *superset* of the single-turn row
  shape — adds `scenario_id`, `turn_index`, `toolset_at_call`,
  `swap_events` but preserves `prompt`, `result`, `score` keys so
  the dashboard's existing indexer doesn't crash (Phase 8 still
  skips them in the heatmap to avoid double counting).

The new runner does NOT modify `runner.py` — it lives next to it.

Writes one JSONL row per turn plus one scenario-summary row per
scenario. Scenario summary captures `scenario_passed`,
`swap_precision`, `extra_swaps`, `end_to_end_latency_ms`.

### Phase 7 — CLI

In `cli.py`:

```python
VOICE_AGENTS = [..., "openai-realtime-swap"]

@cli.command()
def probe_swap(...): ...   # invokes scripts/probe_session_update.py logic

@cli.command()
def swap(...): ...         # invokes run_swap_benchmark
```

In `adapters/registry.py`:

```python
if agent == "openai-realtime-swap":
    from .openai_realtime_swap import OpenAIRealtimeSwapAdapter
    return OpenAIRealtimeSwapAdapter()
```

### Phase 8 — Dashboard read-through

`scripts/build_dashboard.py:load_all_rows()` indexes `d["result"]` and
`d["prompt"]["id"]` directly (lines ~124–145). Swap JSONL rows have a
different shape (no flat `prompt`; instead `scenario_id`,
`turn_index`, nested `turn`, etc.). The build script must:

- Detect swap rows by presence of `scenario_id` or `model_kind ==
  "voice_swap"` and **skip them** in the main heatmap aggregator —
  do not try to coerce them into the per-tool-count grid.
- Optionally emit a side-by-side JSON (e.g. `swap_data.json`) that the
  voice_analysis page can read separately.

Minimal new UI; full scenario-aware visualisation is a follow-up story.

### Phase 9 — Smoke verification

Run end-to-end:

```bash
uv run voice-bench probe-swap --agent openai-realtime-swap
uv run voice-bench gen-audio --manifest prompts/manifest_swap.json
uv run voice-bench swap --agent openai-realtime-swap --manifest manifest_swap
uv run python scripts/build_dashboard.py
uv run voice-bench run --agent openai-realtime --tools 5 --mode smoke   # regression
```

## Risks & Open Questions

- **R0 — Event correlation in the receive loop**: a naïve
  "next session.updated wins" wait would steal events the main turn
  state machine still needs (e.g. `response.done`,
  `conversation.item.created`, audio deltas). The implementation must
  send `session.update` with a client `event_id` and the swap-wait
  must buffer any non-matching events back onto the main queue for
  the receive loop to process. See Phase 2 implementation notes.
- **R1 — session.update latency**: docs imply it's "free" but the
  Phase 0 probe is the authority. If the round-trip is >500ms, the
  swap will be conversationally awkward. Mitigation: narrate the
  delay in the system prompt ("acknowledge that you're loading the
  right tools, then proceed").
- **R2 — Model forgetting the old toolset**: after a swap, the
  conversation history still references tools that are no longer
  loaded. OpenAI's docs claim the conversation is preserved
  independently of the schema. The probe should test this explicitly:
  after a swap, ask a question that would naturally reuse the old
  toolset and confirm the model either swaps back or politely
  declines.
- **R3 — Tool-call name collision**: if a tool name exists in both
  the previous and new pool, post-swap calls may be ambiguous about
  which schema the model was using. Mitigation: keep `name → schema`
  identical across toolsets for shared tools (e.g. `take_photo`
  appears in core, not in any pool).
- **R4 — Wildcard args scoring**: `"*"` may mask real arg-quality
  problems. Mitigation: use sparingly, only when the expected value
  is genuinely scenario-dependent (e.g. experiment IDs).
- **R5 — Audio fixture cost**: each scenario needs N audio files;
  scenarios with 3 turns need 3 files. Approve audio re-rendering
  before the benchmark exists in case the macOS `say` voice
  re-renders cost time. Mitigation: `--text-mode` flag for fast
  iteration, audio is required only for AC4.
- **R6 — Provider-error rerun cost**: today's runs already hit
  OpenAI quota limits at moderate scale. Scenarios are 3× heavier
  per logical "prompt". Budget accordingly; consider rate-limit
  pacing in the swap runner.
- **R7 — Scoring multi-step correctness**: there is no scoring
  precedent for multi-turn scenarios in voice-bench. The schema in
  Phase 5 is opinionated; reviewer feedback should shape it before
  implementation.
- **R8 — Audio buffer poisoning across turns**: if a turn ends in
  timeout or error with audio still in `input_audio_buffer`, the next
  turn will see stale bytes prepended. Phase 2 mandates an explicit
  `input_audio_buffer.clear()` at turn start and `committed`-event
  wait after commit. A failure to commit (empty buffer error) must
  short-circuit the turn rather than fall through.
- **R9 — Output audio overlap**: when a swap occurs mid-response, the
  model may still be streaming audio for the prior response while
  the schema flips. Per Phase 2: wait for the originating
  `response.done` before issuing `session.update`. This costs latency
  but eliminates the overlap and keeps tracking simple.
- **R10 — `tool_choice="required"` regression**: the existing adapter
  forces a tool call on every response. Inheriting that into the
  swap adapter would break listing/recovery/control turns. The swap
  adapter must override to `auto`. Single-turn agents are unaffected.
- **R11 — Models contract change**: extending `model_kind` to
  include `"voice_swap"` and adding optional fields to `ToolCallEvent`
  / `TurnTimeline` is a typed-contract change. Dashboard and scoring
  consumers must be updated in lockstep. Existing JSONL files do not
  contain the new fields; consumers must tolerate their absence.
- **R12 — Architecture proliferation**: building a third runner
  alongside `run_benchmark` and `run_pipeline_benchmark` fragments
  the codebase. Mitigation: design `run_scenario` to take the
  adapter as a parameter (not instantiate it) and emit a JSONL row
  shape that is a superset of the single-turn shape. A future
  unification story can refactor all three into a Strategy pattern
  without changing the on-disk data. **Not** addressed by this
  story; explicitly deferred.
- **R13 — Meta-schema confusion**: if `switch_toolset` enumerates 100
  toolsets via a long enum in `parameters`, we may simply move the
  positional confusion up to the meta-routing layer. Mitigation:
  cap the toolset count at ~8 (well below the cliff), and have
  `list_toolsets` return descriptions for discovery rather than
  baking them into the enum. If the universe grows past ~8 toolsets,
  a *second* meta-layer (groups of toolsets) is the next step — and
  that's exactly when the unified Session abstraction starts to
  matter.
- **R14 — Cross-adapter comparability**: we're measuring "can the
  agent reach this tool". The pipeline benchmark answers the same
  question via a different mechanism. AC7 requires that scenarios
  be shaped so the existing pipeline runner could also score them —
  the head-to-head comparison is the architecturally interesting
  output, and the data shape supports it now even though running
  the comparison is a follow-up.
- **R15 — Thrashing not adequately captured by `scenario_passed`**:
  a model that swaps 3 times before getting the right pool is still
  "correct" by binary measure but fails the realtime UX. AC5 now
  requires `swap_precision`, `extra_swaps`, and
  `end_to_end_latency_ms` to capture this — `scenario_passed` is
  necessary but not sufficient.
- **R16 — Latency math change**: `TurnTimeline.ttf_tool_ms` is
  anchored on a single input-audio-end timestamp. A multi-turn
  scenario has multiple inputs. The new
  `end_to_end_latency_ms = ts_first_user_input … ts_final_task_tool`
  is the meaningful realtime metric and lives on the
  `ScenarioSummary` (not the per-turn timeline).
- **Open Q1**: Should `list_toolsets` return descriptions per
  toolset, or just names? Descriptions help the model pick, but
  inflate per-call cost. Default: names only with a one-line hint
  baked into the system prompt.
- **Open Q2**: Should swap actually replace the previous pool, or
  *append* to it (so the model keeps access to previously loaded
  tools)? Replace is what we want for the cliff-avoidance goal, but
  append is closer to "agent memory" and may be easier to reason
  about. Default: replace.
- **Open Q3**: Where does the system prompt for `openai-realtime-swap`
  live? Default: `prompts/system/openai-realtime-swap.md`. Content
  needs to teach the model about the swap pattern — when to use it,
  how to enumerate, what each toolset is for.

## Out of Scope

- Gemini Live implementation (separate story; requires session-resumption
  restart pattern with ~2–5s latency).
- Vector / embedding tool retrieval (ITR-style).
- Multi-agent / hierarchical routing changes (the existing pipeline
  benchmark already covers two-tier routing).
- Dashboard UI rewrites — minimal "voice_swap" model_kind read-through
  only. A proper scenario-aware visualisation is a follow-up.
- Real MCP server integration (OpenAI Realtime supports `type: mcp`
  tools but that's a different scaling question).
- Changes to the existing `runner.run_benchmark` or
  `runner.run_pipeline_benchmark` codepaths. The swap path is
  side-by-side, not a refactor.

## Reviewer Feedback

### Codex (round 1) — 2026-05-19

Verbatim Codex critique focused on implementation gaps:

1. The plan assumes `response.function_call_arguments.done` has
   `name`, but the installed OpenAI SDK type does not. The event has
   `arguments`, `call_id`, `item_id`, `output_index`, `response_id`,
   but no `name`. The current adapter does `getattr(ev, "name", "")`,
   which means swap interception via `name == "switch_toolset"` will
   silently fail. Correlate with `response.output_item.added` /
   `response.output_item.done`, which carry `item.name`.
2. The generic post-tool path will fight the swap path unless
   explicitly split. Today every function call gets
   `function_call_output` then immediate `response.create()`. For
   `switch_toolset` the order is: capture item/name, send tool output,
   `session.update`, wait for matching `session.updated`, then the
   continuation `response.create`.
3. Waiting for `session.updated` needs event-id correlation; without
   it, the nested wait can steal `response.done`, audio deltas, or
   errors that the main state machine needs.
4. The plan does not define whether `session.update` is sent while
   the originating response is still active. The terminal logic that
   breaks after two `response.done` events is invalid for swap turns.
5. Synthetic text path is under-specified. The current adapter is
   audio-only (`load_pcm16` unconditional, `prompt_text` ignored). A
   text probe must use `conversation.item.create(user message)` →
   wait for `conversation.item.created` → `response.create`.
6. `conversation.item.create` vs `response.create` ordering: a
   `function_call_output` must reference an existing function-call
   item in conversation history. Observe `response.output_item.added`
   before emitting the output.
7. Audio buffer state after a tool call is unhandled. Multi-turn
   audio needs `input_audio_buffer.clear()` / `committed` waits
   between turns.
8. Output audio state / turn boundaries: the current adapter waits
   for `response.done` but not `response.audio.done` /
   `output_audio_buffer.stopped`. Multi-turn audio needs an explicit
   turn-boundary definition.
9. `tool_choice="required"` forces a call every response. Wrong for
   listing / recovery / control turns. Use `auto`.
10. `stop_recording` does not exist in `tools.py`. Adding it shifts
    `ALL_TOOLS` ordering and breaks counts.
11. `build_pool()` silently drops unknown tool names. Hard-fail at
    import time.
12. `model_kind` is typed as `"voice" | "text"`; `"voice_swap"`
    violates the annotation. `ToolCallEvent` has no `toolset_at_call`,
    `TurnTimeline` has no swap latency fields. Contract change.
13. Existing `score_turn` always judges first call as expected; swap
    turns where `switch_toolset` precedes the task tool need a new
    scorer that ignores meta calls.
14. CLI and registry changes are mandatory blockers, not Phase 7
    plumbing. `click.Choice(VALID_AGENTS)` rejects unknown agents
    before adapter construction.
15. `gen-audio` assumes flat manifest. Scenarios nest prompts under
    `turns`; will fail or render scenario-level objects incorrectly.
16. Dashboard "read-through" is more than tolerating columns.
    `build_dashboard.py` indexes `d["result"]`, `d["prompt"]["id"]`
    directly. Swap rows have different shape; skip or normalise.

### Codex critical issues addressed

- (1) Phase 0 + Phase 2 now mandate name resolution via
  `response.output_item.added` keyed by `call_id`. The plan
  explicitly says **never** use `ev.name` on args.done.
- (2) Phase 2 now describes the swap dispatch sequence in full:
  capture name → emit tool output → wait for **originating
  `response.done`** → `session.update` with client `event_id` →
  drain to matching `session.updated` → continuation
  `response.create`. The generic immediate-`response.create` path is
  explicitly NOT used in the swap adapter.
- (3) R0 added covering event correlation. Phase 2 mandates
  `event_id` on `session.update` and buffered drain semantics.
- (4) Phase 2 chooses "wait for originating response.done before
  session.update". The single-turn "two response.done events" logic
  is explicitly NOT inherited.
- (5) Phase 0 now specifies the text path: `conversation.item.create`
  with `input_text` content → `response.create`.
- (6) Phase 2 mandates observing `response.output_item.added` (the
  call_id → name map) before emitting `function_call_output`.
- (7) R8 added; Phase 2 mandates `input_audio_buffer.clear()` and
  `committed` wait.
- (8) R9 added; Phase 2 waits for originating `response.done` before
  `session.update`.
- (9) Phase 2 explicitly sets `tool_choice="auto"` in the swap session
  config and notes the override.
- (10) `stop_recording` removed from CORE_TOOL_NAMES. Replaced with
  `configure_capture` (exists in tools.py).
- (11) `_validate()` helper added in Phase 1 raising at import time
  on unknown names, oversize toolsets, and core/pool overlap.
- (12) New Phase 0.5 covers model_kind extension + new fields. R11
  added.
- (13) Phase 5 rewritten to handle ordered tool calls explicitly,
  splitting meta vs task calls and applying scoring rules to the
  first task call.
- (14) Phase 0.5 reordered to make CLI/registry/models changes
  blockers BEFORE Phase 1 (which depends on the new model fields).
- (15) Phase 4 now describes the gen-audio branch on `turns` key.
- (16) Phase 8 mandates skipping swap rows in the main aggregator
  and emitting a side-by-side JSON for the voice_analysis page.

### Codex nice-to-have items addressed

- Hard-validate at startup → adopted (Phase 1 `_validate()`).
- Recorded event-stream unit tests → noted but **deferred** to a
  follow-up. The probe (Phase 0) is the working substitute for
  spot-checking event sequences during implementation; unit tests
  with fake streams would catch regressions but are not necessary
  for the first working version. Add if the implementation reveals
  flakiness in CI.
- Track `event_id`, `response_id`, `item_id`, `call_id` in raw
  records → already present in `raw_events`. Keep capturing
  `event_id` in `swap_events` for the swap-specific latency
  tracking.
- Explicit `tool_choice` decision → adopted (`auto`).
- `stop_recording` out of `ALL_TOOLS` → adopted (removed from core).

### Gemini (round 2) — 2026-05-19

Verbatim Gemini architectural critique:

**(a) Parallel runner vs. extending the existing runner**: Creating
a parallel runner, scorer, and manifest creates an isolated testing
silo. The core evaluation loop is identical across runners. Instead,
the existing `run_benchmark` should evolve to process a Scenario or
Session abstraction, where a single-prompt benchmark is just a
1-turn scenario. This allows all paths to share the robust timing
logic already built into TurnTimeline.

**(b) Interaction with the two-tier pipeline**: Dynamic swapping
solves the **same problem** the existing pipeline benchmark
solves. Building a parallel architecture for swaps means you cannot
easily compare the two. The empirical question is "Is a single voice
model with dynamic swapping faster/more accurate than a voice-to-text
pipeline?" — both adapters should be testable against the same
multi-step manifest, scored by the same engine.

**(c) Toolset granularity**: Hardcoding 4 hand-picked toolsets only
proves the basic API mechanism works. It does not teach you how the
architecture scales. The benchmark should dynamically generate pools
based on parameters (testing accuracy at pool sizes of 5, 10, 15,
20, 25). This parametric approach lets you plot a degradation curve
and find the optimal sub-pool size.

**(d) Are we measuring the right thing?**: A binary `scenario_passed`
is insufficient. Key risks of dynamic swapping are "thrashing"
(endlessly swapping pools) and severe latency compounding. The true
metrics of interest are **End-to-End Task Latency** (time-to-final-
tool across the entire multi-turn session) and **Swap Precision**
(did it guess the right pool on the first try?).

### Gemini architectural concerns

1. **State management leakage**: The current adapter resets state
   gracefully by opening a new connection per turn. Multi-turn
   swapping requires holding it open. If a swap scenario fails or
   times out mid-flight, the adapter must guarantee a clean reset
   before the next scenario.
2. **Meta-schema confusion**: If `switch_toolset` enumerates names
   and descriptions of all available toolsets, a 100-toolset universe
   just shifts the positional confusion from the tool layer up to
   the meta-routing layer.
3. **Diverging latency math**: `models.py` anchors `ttf_tool_ms` to
   a single input audio end event. A multi-turn swap has multiple
   audio inputs and intermediate responses, requiring a fundamental
   update to how we define start/end metrics across a scenario.

### Gemini alternatives worth considering

1. **Unify under a 'Session' abstraction**: Refactor `runner.py` so
   standard, pipeline, and swap benchmarks are strategies executed
   within a single Session loop, using a unified manifest.
2. **Parameterised pool generation**: Modify `load_tools()` to
   dynamically bucket the existing 50+ DummyTools into configurable
   chunk sizes during benchmark setup, allowing automated scale
   testing.
3. **Client-side semantic routing**: Instead of forcing the model to
   explicitly call `switch_toolset` (incurring a full turn's
   latency), use a fast local embedding lookup on the realtime
   transcript (`conversation.item.input_audio_transcription.completed`)
   to swap the tool pool instantly via `session.update` while the
   model is listening. Removes the routing turn entirely.

### Gemini concerns addressed

- (1, state leakage) — R8/R9 already mandate audio-buffer reset
  semantics; **R17 added below** for connection-level reset between
  scenarios.
- (2, meta-schema confusion) — **R13 added** capping toolset count
  at ~8 and using `list_toolsets` for descriptions rather than
  baking long enums into `switch_toolset.parameters`.
- (3, latency math) — **R16 added**, AC5 updated to record
  `end_to_end_latency_ms` on the ScenarioSummary instead of
  shoehorning it into per-turn TurnTimeline.

### Gemini alternatives — adoption decisions

- (1, unified Session) — **partially adopted**. Full refactor is
  out of scope. Phase 6 now mandates that `run_scenario` takes the
  adapter as a parameter and the JSONL row shape is a superset of
  the single-turn shape, so a future unification is a refactor not
  a rewrite. R12 captures the deferred work.
- (2, parametric pool generation) — **adopted as AC8**. Phase 1 now
  describes two strategies: static (hand-picked, default) and
  parametric (`--pool-size N` flag auto-buckets ALL_TOOLS). The
  parametric mode is the rigorous degradation-curve test; static
  mode is for semantic scenarios.
- (3, client-side semantic routing) — **considered and rejected for
  this story**, captured in Risks. The user's stated design is that
  the agent itself drives swaps. Client-side routing is a different
  architecture (and a different research line — closer to ITR);
  worth its own story once we have baseline data from the
  agent-driven path. Adding it here would muddle the experiment.

### Additional risk added

- **R17 — Cross-scenario contamination**: holding the WebSocket open
  for the duration of a scenario means a mid-flight failure can leak
  state (uncommitted audio, in-progress responses, pool mismatch)
  into a subsequent run. Mitigation: the swap runner instantiates a
  fresh adapter (and therefore a fresh connection) per scenario. If
  a scenario crashes inside the receive loop, the connection is
  torn down by the `async with` exit, ensuring isolation.

### AC7 added — pipeline-comparability

Acceptance criterion AC7 was added: scenarios must be shaped so the
existing pipeline runner can also score them. This makes the
"swap vs pipeline" head-to-head a future-run away rather than a
rewrite.

## Revision History

- 2026-05-19 — Initial draft
- 2026-05-19 — Round 1 revision after Codex critique. Major changes:
  added Phase 0.5 (CLI/registry/models blockers); Phase 0 probe now
  specifies SDK event invariants (no `ev.name` on args.done) and
  text-input path; Phase 2 fully rewrites the swap dispatch state
  machine with explicit `event_id` correlation and wait-for-prior-
  response-done ordering; Phase 5 scorer redesigned around ordered
  tool calls; Phase 8 dashboard treats swap rows as a separate
  channel; risks R0/R8/R9/R10/R11 added.
- 2026-05-19 — Round 2 revision after Gemini architectural critique.
  Major changes: AC5 expanded with swap_precision, extra_swaps,
  end_to_end_latency_ms; AC7 added (pipeline-comparable scenario
  shape); AC8 added (parametric pool generation via `--pool-size`);
  Phase 1 now supports two pool strategies (static + parametric);
  Phase 6 mandates `run_scenario(adapter, …)` interface for future
  unification; R12 (architecture proliferation), R13
  (meta-schema confusion), R14 (cross-adapter comparability), R15
  (thrashing), R16 (latency math), R17 (cross-scenario contamination)
  added. Client-side semantic routing alternative considered and
  rejected with rationale.

Status updated to: **Reviewed — awaiting approval**.
