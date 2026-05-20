# Story: Meta-Category Routing Benchmark

**ID:** 2026-05-19-meta-category-routing-benchmark
**Status:** Reviewed — awaiting approval

## Prerequisites

This story depends on Story 1 (Text Model Multi-Tool Benchmark) for the
six text-model adapters and the `model_kind` field. **Do not start this
story until Story 1's adapters are wired.** The voice agents
(gemini-live, openai-realtime) already exist and could be benchmarked
on routing alone if needed — but the full comparison this story aims
for needs the text models too.
**Created:** 2026-05-19

## Goal

Measure how reliably voice and text models can route a user utterance
into one of **3 meta-tool categories** before any sub-tool selection.
This is the upstream half of the two-layer architecture: the voice model
sees only 3 broad "buckets" (chemistry/camera control, app/session
control, assistant tasks) and picks one. The downstream sub-router then
selects a specific tool inside that bucket — but the sub-router work is
Story 4. This story validates the upper-layer hypothesis in isolation.

## Context

The current voice-bench setup proved that voice models collapse to
~30% accuracy at 5 tools and below 10% at 30. The Android BioVia
deployment works in production because all ELN operations go through a
single tool (`agent_nexus_eln`) — effectively a 1-tool scenario from the
voice model's perspective. The proposed multi-agent architecture
generalizes this:

```
Voice model (3 meta-tools)
    ├── chemistry_tools  → set_zoom, set_iso, set_exposure, ...
    ├── app_control      → toggle_flash, start_recording, ...
    └── assistant        → ask_clawdbot, query_knowledge_base, ...
```

If voice models hit close to 100% on the 3-bucket pick, the architecture
is viable. If they fail at even 3 buckets, no amount of text-model
power downstream saves it. This is a small, clean test that
de-risks Story 4.

### Why text models too, not just voice

The user explicitly asked to test "even those three" with text models.
Two reasons:
1. **Baseline ceiling**: if a text model can't reliably pick 1 of 3
   buckets either, the buckets themselves are poorly designed.
2. **Mixed orchestrator option**: an alternative architecture has a
   text model do the bucket pick (voice agent → ASR → text orchestrator
   → sub-router). Need data to know if the latency penalty buys real
   accuracy.

### Existing infrastructure reusable

- The 30 dummy tools in `src/voice_bench/tools.py` cleanly partition
  into the 3 buckets — that's the original tiering scheme. The
  category-to-tools mapping needs to be made explicit.
- `prompts/manifest_v2.json` has 50 prompts spanning all 30 tools. The
  `expected_tool` of each prompt determines which bucket the model
  *should* pick. We add an `expected_category` field per prompt and
  score against that.
- Adapters already accept a `tools: list[DummyTool]` parameter; this
  story replaces the 30 specific tools with 3 meta-tools whose
  `description` says "use this for all X" and whose only parameter is
  the raw user utterance forwarded as-is.

## Acceptance Criteria

- [ ] **Three meta-tools defined** in `src/voice_bench/tools.py`:
      `chemistry_tools`, `app_control`, `assistant`. Each takes a
      single string param `utterance` (the verbatim user request).
      Descriptions are hand-tuned to maximize bucket separation; the
      tuning iteration is part of this story.
- [ ] **Category mapping** added to each existing `DummyTool` (a
      `category: Literal["chemistry", "app", "assistant"]` field) so
      we can look up which bucket any sub-tool belongs to and verify
      the manifest's `expected_category` matches automatically.
- [ ] **Manifest extension**: `prompts/manifest_v2.json` gains an
      `expected_category` field per prompt (derived once from the
      tool→category map, then frozen so prompts don't drift). New
      manifest file `prompts/manifest_routing.json` if we don't want
      to mutate the existing one — decision below.
- [ ] **Bucket-routing scoring**: new function `score_routing_turn` in
      `src/voice_bench/scoring.py` that asks only "was the called
      meta-tool's name equal to `expected_category`?" — args are
      ignored (the `utterance` arg is the raw prompt, no extraction
      needed).
- [ ] **CLI mode**: `voice-bench run --agent <name> --mode routing`
      runs the bucket benchmark using the meta-tools and the routing
      scorer. `cli.py:57-59` Click choices list adds `"routing"`.
      `--tools` is accepted but ignored in routing mode (a warning is
      printed if it's anything other than the default).
- [ ] **Two routing sub-modes**: `--routing-mode forced` (default) and
      `--routing-mode auto`.
      - `forced`: text adapters set `tool_choice` to "any"/"required"
        (provider-specific). Measures **upper-bound bucket-discrimination
        ability** — assumes the model is willing to call a tool.
      - `auto`: text adapters keep `tool_choice="auto"`. Measures
        **production-realistic behavior** including the model's
        decision to NOT call a tool when uncertain.
      Both numbers are useful; reporting only the forced number
      (Gemini correctly flagged) would hide false-positive routing in
      production. Both modes run in Step 6.
- [ ] **All 8 agents supported** (post-Story-1): both voice models
      (gemini-live, openai-realtime) and the 6 text models.
- [ ] **Voice audio fixtures**: any routing-only prompts (assistant
      bucket additions) get WAV fixtures via
      `voice-bench gen-audio --manifest manifest_routing`.
      Voice agents skip non-audio prompts per `runner.py:108`.
- [ ] **Ambiguous-prompt subset**: 5-10 prompts in
      `manifest_routing.json` are flagged `ambiguous: true` and accept
      ≥2 valid `expected_category` values (e.g. "burst mode" plausibly
      maps to both `app` and `chemistry`). Routing scorer accepts any
      of the listed categories as correct. Lets us measure whether
      models genuinely fail or whether the partition is fuzzy
      (Gemini flagged this concern).
- [ ] **Out-of-route prompts**: 5-10 conversational prompts in
      `manifest_routing.json` flagged `negative: true` whose
      `expected_category=null`. In `auto` sub-mode, a no-tool-call is
      the correct behavior. In `forced` sub-mode, these are excluded
      from the accuracy denominator. Lets us measure the
      false-positive rate of forced routing.
- [ ] **Results recorded** with a new `model_kind`-friendly schema —
      i.e. routing accuracy is a separate metric column on the
      dashboard, not folded into the main accuracy heatmap (which
      measures sub-tool selection, a different thing).
- [ ] **Smoke run** for all 8 agents at `--mode routing` completes
      in <30 min wall time and writes one JSONL per agent.
- [ ] **Failure inspection**: for any agent that scores <90% on
      routing, the failure explorer (Dashboard v2) shows the
      mis-routed prompts so the meta-tool descriptions can be
      iterated.

## Implementation Plan

### Step 1 — Categorize existing tools

Add a `category` field to `DummyTool` in `src/voice_bench/tools.py`.
**All 30 existing constructors must be updated to pass `category=...`**
or the dataclass init will raise. Run `uv run pytest tests/ -v` after
the field is added to catch any test fixtures.

```python
@dataclass
class DummyTool:
    name: str
    description: str
    parameters: dict
    tier: int
    category: Literal["chemistry", "app", "assistant"]
    _call_log: list = field(default_factory=list, repr=False)
```

Categorize all 30 existing tools:
- **chemistry**: `set_zoom`, `set_iso`, `set_exposure`,
  `set_white_balance`, `set_focus_distance`, `set_shutter_speed`,
  `set_resolution`, `set_aspect_ratio`, `set_timer`, `set_color_profile`,
  `set_video_fps` (~11 tools — anything that adjusts a camera/optics
  parameter).
- **app**: `toggle_flash`, `toggle_grid_overlay`, `toggle_macro_mode`,
  `toggle_stabilization`, `toggle_voice_captions`, `toggle_hdr`,
  `toggle_location_tags`, `switch_camera`, `start_recording`,
  `start_documentation`, `take_photo`, `set_capture_burst`,
  `annotate_frame`, `set_review_mode`, `configure_capture`,
  `configure_session`, `apply_preset`, `export_session`, `sync_to_eln`
  (~19 tools — session, recording, UI, export).
- **assistant**: this category has zero existing tools in
  `tools.py` because the dummy lab-camera tools don't include
  "ask Clawdbot" / "query KB" surfaces. **We need to add 2-3 stub
  assistant tools** to populate this bucket: `ask_clawdbot`,
  `query_knowledge_base`, `agent_nexus_eln` (matching the production
  Android tool names so the manifest can include realistic prompts).

The exact partition between chemistry and app is up for review — some
tools are borderline (e.g. `take_photo` is app control, but
`set_capture_burst` could be either). Final partition lives in code as
the source of truth.

**Assistant tools are NOT added to `ALL_TOOLS`** (Codex flagged this).
The 30-tool benchmark stays at 30 tools; `VALID_TOOL_COUNTS` at
`cli.py:15` and the v2 manifest generator are unchanged. The assistant
bucket is populated only via routing-manifest prompts whose
`expected_category="assistant"` — no sub-tools required for routing
scoring.

### Step 2 — Define the three meta-tools

In `src/voice_bench/tools.py` (or a new `meta_tools.py`):

```python
META_TOOLS: list[DummyTool] = [
    DummyTool(
        name="chemistry_tools",
        description=(
            "Adjust camera or optics parameters: zoom, ISO, exposure, "
            "white balance, focus distance, shutter speed, resolution, "
            "aspect ratio, timer, color profile, frame rate. "
            "Use this tool whenever the user mentions numeric camera "
            "settings, photography terms, or optical adjustments."
        ),
        parameters={
            "type": "object",
            "properties": {
                "utterance": {"type": "string",
                              "description": "Verbatim user request"},
            },
            "required": ["utterance"],
        },
        tier=0,
        category="chemistry",  # the meta-tool's own category equals
                                # the bucket it serves
    ),
    DummyTool(
        name="app_control",
        description=(
            "Control the camera app's session: flash, grid, macro mode, "
            "stabilization, captions, HDR, location, camera lens, "
            "recording, documentation, photo capture, burst, annotation, "
            "review, presets, export, sync. "
            "Use this tool for any on/off toggle, session/recording "
            "lifecycle action, or capture-and-save command."
        ),
        ...
        category="app",
    ),
    DummyTool(
        name="assistant",
        description=(
            "Ask Clawdbot to perform a task, query the lab knowledge "
            "base, drive the BioVia ELN, look something up online, or "
            "answer a scientific question. "
            "Use this whenever the user asks a question, needs "
            "information, or wants the assistant to do something on "
            "the computer."
        ),
        ...
        category="assistant",
    ),
]
```

Description tuning is iterative. The first pass is the obvious
"set of nouns from each bucket"; second pass refines based on the
mis-routed prompts the smoke run produces.

### Step 3 — Add `expected_category` to the manifest

Two options:
1. **Mutate `manifest_v2.json` in place**, adding a category field to
   each prompt.
2. **Generate a new `manifest_routing.json`** by copying v2 + auto-
   populating `expected_category` from each prompt's `expected_tool`'s
   category.

**Choose option 2.** It keeps v2 untouched (the existing
non-routing benchmarks keep running) and the routing manifest can
include extra prompts (e.g. assistant-bucket prompts that didn't fit
the camera-only v2 manifest). Generation is a one-shot
`scripts/gen_routing_manifest.py` from the v2 manifest plus a few
hand-authored assistant prompts.

### Step 4 — Routing scorer + Score-shape compatibility

**Codex flagged** that `RoutingScore` as a separate class breaks
`runner.py:126-158` (which prints `score.tool_name_match` /
`score.arg_score`) and `scripts/build_dashboard.py:18` (which imports
only `score_turn`). Two ways to resolve:

(A) **Reuse `Score`** — emit a regular `Score` from `score_routing_turn`
where `tool_name_match` is set to `category_match` and `arg_score = 1.0`
when matched (no args to grade). Pro: zero changes to runner output
code and the dashboard.
(B) **Add `RoutingScore` and a discriminator field** — `mode: str` on
each JSONL row, runner branches on it for the status print, dashboard
filters routing rows into the new routing chart.

**Choose (A) for this story.** It's tighter; the routing-specific
fields (`expected_category`, `called_category`) get appended to the
row dict at the runner level, not on the Score itself. Dashboard
splits routing vs. accuracy via the `tool_mode` row field, not by
inspecting Score shape.

`src/voice_bench/scoring.py` gains:

```python
CATEGORY_TO_META_TOOL = {
    "chemistry": "chemistry_tools",
    "app":       "app_control",
    "assistant": "assistant",
}

def score_routing_turn(
    result: TurnResult,
    expected_category: str,
) -> Score:
    expected_meta = CATEGORY_TO_META_TOOL[expected_category]
    if not result.tool_calls:
        return Score(tool_name_match=False, arg_score=0.0, ...,
                     no_call_made=True)
    first = result.tool_calls[0]
    matched = (first.tool_name == expected_meta)
    return Score(
        tool_name_match=matched,
        arg_score=1.0 if matched else 0.0,
        ttfs_ms=result.timeline.ttfs_ms,
        ttf_tool_ms=result.timeline.ttf_tool_ms,
        extra_calls=max(0, len(result.tool_calls) - 1),
        ...
    )
```

Codex correctly flagged that the original `expected_category + "_tools"`
formula breaks for `app → app_control` and `assistant → assistant`.
Use the explicit `CATEGORY_TO_META_TOOL` dict above.

### Step 5 — Runner routing mode

Modify `src/voice_bench/cli.py` and `src/voice_bench/runner.py`:

1. `cli.py:57` Click choices: add `"routing"`.
2. **Routing branch happens BEFORE `load_tools(tool_count)`** at
   `runner.py:58`. Codex flagged this — `load_tools(tool_count)`
   currently runs unconditionally and would either load wrong tools or
   raise. The branch:

   ```python
   if mode == "routing":
       tools = META_TOOLS  # 3 buckets
       prompts_filter_by_tool = False
       scorer = lambda result, prompt: score_routing_turn(
           result, prompt["expected_category"]
       )
       manifest = _load_manifest(PROMPTS_DIR / "manifest_routing.json")
   else:
       tools = load_tools(tool_count)
       prompts_filter_by_tool = True
       scorer = lambda result, prompt: score_turn(
           result, prompt.get("expected_tool"),
           prompt.get("expected_args"),
           prompt.get("negative", False)
       )
   ```

3. **Disable the `expected_tool ∈ loaded_tool_names` filter** at
   `runner.py:91-95` when `mode == "routing"`. Use the
   `prompts_filter_by_tool` flag above.
4. **`tool_choice` forcing in routing mode**: pass a `force_tools=True`
   flag to the adapters' `_session_config` / `_build_config` /
   `messages.create` calls so text adapters set:
   - Anthropic: `tool_choice={"type": "any"}` (forces *some* tool, not
     a specific one).
   - OpenAI Chat (`gpt_text.py`): `tool_choice="required"`.
   - Gemini Text (`gemini_text.py`): `tool_config.function_calling_config.mode="ANY"`
     — supported on `generate_content`, NOT on Live (known SDK gap).
   - Voice models: already force tools via their existing config.
   This is a per-call flag, not a permanent change.
5. Emit a JSONL with `tool_mode="routing"`, plus `expected_category`
   and `called_category` (the category of the meta-tool the model
   actually called).

### Step 6 — Smoke run + tune descriptions

```bash
for agent in gemini-live openai-realtime claude-opus claude-sonnet \
             gpt-5 gpt-4o gemini-pro gemini-flash; do
  for mode in forced auto; do
    voice-bench run --agent $agent --mode routing --routing-mode $mode
  done
done
```

16 runs total. Inspect failure explorer for both routing sub-modes.

**Tuning loop**:
1. If `forced` accuracy is <90% across most agents, the **buckets
   themselves are wrong** — escalate to user. Don't try to fix with
   description engineering, which would mask the partition problem
   (Gemini's concern).
2. If `forced` is high (>90%) and `auto` is low (<70%), the buckets
   are fine but the models are conservative; reframe the descriptions
   to make the call-it-or-not signal stronger.
3. If both are high (>90%), the partition is solid and Story 4 can
   proceed.

**Description-bloat tripwire**: if a meta-tool's description exceeds
200 words to hit the accuracy target, that's a sign the partition is
forced. Flag in the findings doc and consider the
action-based-partition alternative (Risks #8).

### Step 7 — Dashboard awareness

Update `scripts/build_dashboard.py`:
- `load_all_rows` reads `tool_mode` (default `"accuracy"`) from each
  JSONL line and surfaces it in the row dict.
- `_rescore` branches on `tool_mode`: routing rows are scored against
  `expected_category` via `score_routing_turn`, accuracy rows against
  `expected_tool` via `score_turn`. Currently `_rescore` always uses
  `score_turn` (`build_dashboard.py:56-62`).
- Dashboard v2 (Story 2) gets a new "Routing" view as a sibling to
  the accuracy heatmap: one row per agent, one column for routing
  accuracy. Filter the failure explorer to routing rows when this
  view is active.

If Dashboard v2 has not yet shipped Phase 2B when this story
implements, the routing accuracy is still recorded — it just won't
have a dedicated view until then. Acceptable.

### Step 8 — Document findings

Append to `docs/findings/text-model-comparison.md` (or new
`docs/findings/routing-comparison.md`):
- Per-agent routing accuracy table
- A short list of the prompts each agent routed wrong (illustrates
  bucket-boundary ambiguity)
- One-line conclusion: "Voice models route at X%, text models at Y%
  — proceed to Story 4 / pause / rethink buckets."

## Risks & Open Questions

1. **Three buckets may not be enough**. The user proposed 3 but
   nothing prevents 4-5 if the existing categories have natural
   sub-groups. Mitigation: if smoke run shows persistent confusion
   between `chemistry` and `app` (the most ambiguous boundary), test
   a 4-bucket variant in a follow-up.

2. **Voice models still see "function calling" syntax for the meta-tools**.
   They might still default to position-zero bias and pick
   `chemistry_tools` for everything. Counter-hypothesis to the
   architecture. Smoke run answers it.

3. **Assistant bucket is thin** in v2 manifest (the existing prompts
   are camera-focused). Need 5-10 hand-authored assistant-bucket
   prompts ("what's the safety data for ammonium nitrate?", "open
   the BioVia editor for experiment 3") so the bucket has weight.
   Tracked in Step 3.

4. **Latency contribution**. The meta-tool call is round 1 in a
   two-layer system. If meta-tool routing alone takes 1.5-2s on
   voice models (we don't know yet), the full pipeline is
   uncomfortably slow. The latency captured here feeds directly into
   Story 4's pipeline-latency budget.

5. **Voice model's tool_choice setting**. For Gemini Live the
   force-tool-call mode is not supported on LiveConnectConfig (known
   SDK gap documented in `gemini_live.py`). The model may decline to
   call any meta-tool on prompts that sound conversational. The
   "no call" rate is a real failure mode here, not an edge case.

6. **The OUT_OF_TOOL_SCOPE concept doesn't apply in routing mode**
   (all 3 buckets are always loaded). The runner needs to skip that
   filter when `mode == "routing"`. Belt-and-suspenders: an assertion
   in the runner that the filter is bypassed in routing mode.

7. **Description drift across agents**. The meta-tool descriptions
   are shared across all 8 agents (one description string per
   meta-tool). Different models may need different wording. Out of
   scope for the first pass — measure with one shared description,
   add per-provider description overrides as a follow-up if needed.

8. **Domain-based vs action-based partitioning** (Gemini raised).
   The proposed partition (chemistry/app/assistant) is by domain.
   An alternative is action-based:
   `change_device_setting` (immediate, stateless),
   `manage_session` (stateful, long-running),
   `ask_assistant` (RAG / question-answering).
   Action-based aligns with the verb of the utterance, which models
   may discriminate more reliably than domain. **Decision**: ship
   the domain-based partition first because it maps cleanly to the
   existing tool tiers and the production Android tool surface.
   If Step 6 tuning fails the 90% gate, swap to action-based and
   re-run — that becomes the recovery plan, not a separate story.

9. **Isolation vs end-to-end testing** (Gemini raised). This story
   measures the upper layer in isolation. The risk is that wrong
   bucket choice in isolation may still lead to correct end-to-end
   behavior (e.g. a sub-router that handles cross-bucket overflow).
   **Mitigation**: the diagnostic value of this story is to localize
   failure — if routing is 100% but end-to-end fails, the problem is
   in the sub-router. If routing is 60% and end-to-end matches, the
   routing IS the bottleneck. Story 4 measures end-to-end; both
   numbers together tell the architecture story.

## Out of Scope

- The sub-router (Story 4). This story only measures bucket selection.
- Production wiring into SciSymbioLens. The benchmark is a research
  artifact; production migration is a separate stream.
- 4+ buckets — only if data from this story motivates it.
- Per-agent prompt overrides for the meta-tool descriptions.
- ASR-transcript input mode — the routing benchmark still uses clean
  manifest text. The same Gemini-flagged concern from Story 1
  applies, deferred to Story 4 to measure end-to-end with transcripts.

## Reviewer Feedback

### Codex (round 1)

**Critical issues raised:**
1. CLI `--mode` choices don't include `"routing"`.
2. Runner calls `load_tools(tool_count)` unconditionally before mode
   handling.
3. `expected_tool ∈ loaded_tool_names` filter would drop routing
   prompts.
4. Scoring name mapping `expected_category + "_tools"` breaks for
   `app` and `assistant`.
5. `RoutingScore` as a separate class breaks `runner.py:126-158`
   print/CSV output and dashboard ingestion.
6. Dashboard `_rescore` always uses `score_turn`; routing rows would
   be silently mis-scored.
7. Required `category` field on `DummyTool` breaks all 30 existing
   constructors until updated.
8. Adding assistant stubs to `ALL_TOOLS` makes the benchmark 33 tools
   and breaks `VALID_TOOL_COUNTS` assumptions.
9. Meta-tool schema is fine as long as it stays single-string-arg.
10. Tool-call forcing differs per adapter; text models default to
    `auto` and will produce many `NO_TOOL_CALLED` rows in routing.
11. Voice routing prompts need WAV fixtures from `gen-audio`.
12. "8 agents" is a Story-1 dependency, not present in this checkout.

**Nice-to-have raised:**
- Focused tests for routing helpers.
- `tool_mode` row discriminator.
- Force-tools only in routing mode for text adapters.

**Resolution:**
- Added Prerequisites section locking the Story-1 dependency.
- Step 4 collapsed `RoutingScore` into the existing `Score` shape and
  added explicit `CATEGORY_TO_META_TOOL` mapping.
- Step 5 rewritten with explicit routing branch BEFORE `load_tools`,
  the filter disable, and per-adapter `force_tools` flag.
- Step 1 callout for updating all 30 constructors.
- Step 7 added: dashboard awareness changes (routing-aware `_rescore`).
- AC updated to mandate forced tool calling in routing mode.
- AC added for WAV fixtures.
- Assistant stubs deliberately kept OUT of `ALL_TOOLS` per Codex item 8.

### Gemini Pro (round 2)

**Architectural concerns raised:**
1. Testing routing in isolation over-penalizes intermediate decisions
   that may succeed end-to-end.
2. Domain-based bucket boundaries (chemistry/app/assistant) are
   conceptually blurry; action-based may be cleaner.
3. Stuffing descriptions with noun lists masks poor partitioning.
4. `tool_choice="required"` hides false-positive routing rate.

**Alternatives suggested:**
- Action-based partitioning (`change_setting`, `manage_session`,
  `ask_assistant`).
- End-to-end evaluation only (merge with Story 4).
- Fuzzy buckets — accept ≥2 valid categories per prompt.
- Allow `auto` tool_choice + out-of-route prompts.

**Resolution:**
- **Adopted**: dual sub-modes (`forced` AND `auto`) — Step 6 runs
  both, dashboard reports both, conclusion uses both.
- **Adopted**: ambiguous-prompt and out-of-route subsets in the
  manifest with multi-category scoring.
- **Adopted**: description-bloat tripwire (200-word soft cap) as a
  signal of partition quality.
- **Partially adopted**: action-based partition is documented as the
  recovery plan (Risk #8) if domain-based misses the 90% gate, not
  a separate story.
- **Rejected**: merging with Story 4 — the diagnostic value of
  isolation is the whole point (Risk #9). Keeping separate.

## Revision History

- 2026-05-19 — Initial draft
- 2026-05-19 — Round 1: Codex feedback (all 12 critical, 3 of 3
  nice-to-haves adopted)
- 2026-05-19 — Round 2: Gemini Pro feedback (4 architectural concerns;
  3 adopted, 1 deferred as recovery plan, 1 rejected with rationale)
