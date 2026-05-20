# Story: Text Model Multi-Tool Benchmark

**ID:** 2026-05-18-text-model-multi-tool-benchmark
**Status:** Reviewed — awaiting approval
**Created:** 2026-05-18

## Goal

Extend voice-bench to benchmark frontier **text models** (not voice) on the
same tool-calling task at 10/20/30 tools, so we have data to pick the best
model for the future orchestrator + sub-router layers of a multi-agent voice
architecture. Today we only have one weak text adapter (`gpt-text` →
`gpt-5.5`) and one strong text adapter (`claude-opus` → `claude-opus-4-7`).
We need Sonnet 4.6, Gemini Pro, and Gemini Flash for a meaningful comparison
across providers.

## Context

The benchmark already proved native voice models (Gemini Live, OpenAI
Realtime) plateau at ~30% accuracy with 5 tools and collapse further as the
tool count climbs. Production voice architectures (e.g. SciSymbioLens
Android) sidestep this by exposing the voice model to a single meta-tool
that internally delegates to a text-mode router with many sub-tools. The
next architectural step in voice-bench is to test that two-layer pattern —
**but first** we need to know which text model to put underneath. That's
this story.

Existing text adapters:
- `src/voice_bench/adapters/claude_text.py` — `claude-opus-4-7` default.
- `src/voice_bench/adapters/gpt_text.py` — `gpt-5.5` default with `gpt-4o`
  fallback.
- No Gemini text adapter exists.
- The Sonnet variant is not addressable without an env override and
  `runner.py` only knows the `claude-opus` agent name.

The text adapters share an interface with the voice adapters via
`NativeVoiceAdapter` (Protocol in `adapters/base.py`) and set
`REQUIRES_AUDIO = False`. `runner.py:74-86` is the dispatch site; it filters
prompts by `expected_tool ∈ loaded_tool_names` (line 91-95) so progressive
tool-count runs already work.

### Models to add

| Agent name        | Provider  | Model ID (initial guess — verified in Step 0) | Notes |
|-------------------|-----------|----------------------------------------------|-------|
| `claude-sonnet`   | Anthropic | `claude-sonnet-4-6`                          | Faster/cheaper than Opus. |
| `claude-opus`     | Anthropic | `claude-opus-4-7` (already in repo)          | Existing — keep. |
| `gpt-5` (renamed) | OpenAI    | latest GPT-5 family ID from `models.list()`  | Codex flagged `gpt-5.5` as invalid — the current `gpt-text` default. Rename the agent to `gpt-5` and resolve the actual current GPT-5 ID at Step 0. |
| `gpt-4o`          | OpenAI    | latest dated `gpt-4o` snapshot               | Separate agent for an apples-to-apples test. |
| `gemini-pro`      | Google    | `gemini-3.1-pro-preview`                     | Latest Gemini text. Used by `/story` reviewer so it works. |
| `gemini-flash`    | Google    | `gemini-2.5-flash`                           | Fast tier, expected to be 3-4x faster/cheaper than Pro. |

Per user direction Haiku is excluded — assumption is it's too weak at >10
tools. Worth confirming in a follow-up.

> **Naming clarification**: The current `gpt-text` agent is misleading
> because its default model (`gpt-5.5`) does not exist on the OpenAI API.
> Step 0 (model-ID verification) will resolve this. If `gpt-5.5` truly
> doesn't exist we rename the agent to `gpt-5` and point it at whatever
> the current GPT-5 family member is (likely `gpt-5.2`). Otherwise we
> keep `gpt-text` and add `gpt-4o` as a parallel.

## Acceptance Criteria

- [ ] **Fixed prompt pool**: a frozen 30-prompt subset
      (`prompts/manifest_text_eval.json`) is used at all tool counts so
      the denominator stays constant. At 10 tools, 20 of the prompts'
      `expected_tool` are loaded; the other 10 are distractor-coverage
      prompts that *cannot* succeed and are excluded from the
      denominator with an explicit "out of scope at this tool count"
      label. At 30 tools, all 30 prompts are loaded. This makes the
      accuracy curve a clean test of "same task, more distractors"
      rather than the current "different task, more tools."
- [ ] **`model_kind` field** added to `TurnTimeline` (values: `"voice"`
      or `"text"`). Dashboard latency charts split by `model_kind`
      instead of comparing voice TTFT to text request-RTT on the same
      axis.
- [ ] **Separate latency field for text adapters**:
      `ttf_request_to_call_ms` = ms from request send to first tool
      call. Keep `ttf_tool_ms` voice-only (it stays None for text
      rows). No faking of `ts_input_audio_end`.
- [ ] `voice-bench run --agent <name>` works for all six text agents.
- [ ] `voice-bench probe --agent <name>` works for the same six **and** the
      probe actually exercises tool calling (not just a "ping" with no
      tools — the existing probes would not catch a malformed tool schema).
- [ ] `VALID_AGENTS` in `cli.py` updated.
- [ ] A new adapter `GeminiTextAdapter` exists in
      `src/voice_bench/adapters/gemini_text.py`, with the same interface as
      `claude_text.py` and `gpt_text.py`.
- [ ] `ClaudeTextAdapter` and `GPTTextAdapter` accept `model=` and
      `agent_name=` constructor kwargs; the kwarg flows into the
      `TurnTimeline.agent` field and the `probe()` return dict so Opus
      and Sonnet (and `gpt-text` vs `gpt-4o`) are distinguishable in
      results.
- [ ] The **effective** model returned by the SDK is captured in each row
      (e.g. `response.model`) so that silent model-alias resolution is
      visible.
- [ ] `ttf_tool_ms` is non-None for every successful text-adapter turn.
      Implemented by setting `ts_input_audio_end = ts_connect_start` in
      text adapters so the existing `TurnTimeline.ttf_tool_ms` arithmetic
      at `models.py:31-34` keeps working. (Failed/no-call turns still
      yield `None` by design — that mirrors the existing voice-adapter
      semantic and `scoring.py:82-94`.)
- [ ] `GPTTextAdapter` fallback behavior is fixed: a fallback either
      fails the run fast OR records a `model_fallback` flag on each
      affected row. No more silent `self.model = FALLBACK_MODEL`
      mutation that contaminates subsequent prompts in the same run
      with no audit trail (current bug in `gpt_text.py:117`).
- [ ] Text adapters honor a per-turn timeout (`asyncio.timeout` wrapping
      the SDK call), default 60s. A stalled provider call must not hang
      the whole run.
- [ ] System prompts for new agents: either a per-agent file exists at
      `prompts/system/<agent>.md`, OR `_load_system_prompt()` in
      `runner.py:27` falls back to a sibling alias (e.g. `claude-sonnet`
      uses `claude-opus.md`). Aliasing is simpler and what we'll do; the
      alias map lives in `runner.py`.
- [ ] `README.md` documents the new env vars (`ANTHROPIC_MODEL`,
      `GOOGLE_API_KEY`, `GEMINI_TEXT_MODEL`, `GPT_TEXT_MODEL`).
- [ ] Smoke test: a 5-prompt run on each of the six agents at 10 tools
      succeeds end-to-end with at least one `passed` per agent (sanity —
      not an accuracy requirement, just a wiring check). Smoke mode is
      "all prompts tagged `smoke: true`" — there is no fixed count;
      `runner.py:61-63` selects them.
- [ ] All existing tests still pass: `uv run pytest tests/ -v`.

## Implementation Plan

### Step 0 — Verify model IDs via a probe script

Before writing any adapter code, write a one-shot script
`scripts/verify_model_ids.py` that hits each provider's `models.list()`
endpoint (or equivalent) and prints the available IDs. Resolve:

- The current GPT-5 family ID (Codex flagged `gpt-5.5` as invalid — the
  default in `gpt_text.py:25` is silently failing over to `gpt-4o`).
- The current `gpt-4o` dated snapshot.
- That `claude-sonnet-4-6` is accepted by the Anthropic API.
- That `gemini-3.1-pro-preview` and `gemini-2.5-flash` are addressable.

Output: a printout of confirmed IDs that get pasted into the agent
registry in Step 5. This step's output is a code-comment table; the
adapter defaults are sourced from this table, not from the doc table
above.

### Step 1 — Refactor `claude_text.py` to support multiple agent names

`runner.py` currently constructs `ClaudeTextAdapter()` with no args when
the agent is `claude-opus`. Make three changes:

1. **Add `agent_name` constructor kwarg** to `ClaudeTextAdapter` and use
   it throughout — replaces the hard-coded `"claude-opus"` literal at
   `claude_text.py:67` (probe return) and `claude_text.py:88` (timeline
   agent field). Default the kwarg to `"claude-opus"` for backwards
   compatibility with old result rows.
2. **`model` kwarg is already honored** — confirmed reading
   `claude_text.py:42`.
3. **Capture `response.model`** (the effective model the API actually
   served) in a `RawProviderEvent` of kind `"response"` so the dashboard
   can show "requested vs served." Currently `claude_text.py:122-125`
   already logs `response.model` — verify it survives the dashboard's
   `_rescore` path in `scripts/build_dashboard.py`.

### Step 2 — Fix `gpt_text.py` fallback + add `gpt-4o` agent

Two things, both in `gpt_text.py`:

**(a) Fix the silent-fallback bug** at `gpt_text.py:112-129`. The current
code mutates `self.model = FALLBACK_MODEL` after the first failure, so
every subsequent prompt in that run quietly uses `gpt-4o` and the result
rows still say agent=`gpt-text`. Two acceptable fixes:

- **Fail-fast**: remove the fallback. If the requested model doesn't
  exist, the probe will have caught it in Step 0; at run time the error
  should surface cleanly.
- **Record-then-fall-back**: record a `model_fallback=True` flag on the
  row and reset to the original model at the start of each `run_turn`
  call. The fallback applies per-turn, not per-run.

Recommendation: **fail-fast.** The probe in Step 0 verifies the model
ID; we don't need silent fallbacks at run time, and they hide bugs.

**(b) Add `gpt-4o` agent name** wired in Step 5. Mirror the
`agent_name` constructor kwarg added in Step 1 to `GPTTextAdapter`.
Replace the hard-coded `"gpt-text"` literal at `gpt_text.py:69` and
`:90`.

### Step 3 — Create `GeminiTextAdapter`

New file `src/voice_bench/adapters/gemini_text.py`. Pattern mirrors
`claude_text.py`. Detailed shape:

```python
from google import genai
from google.genai import types

DEFAULT_MODEL = "gemini-3.1-pro-preview"

class GeminiTextAdapter:
    REQUIRES_AUDIO = False

    def __init__(self, api_key=None, model=None, agent_name="gemini-pro"):
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        self.model = model or os.environ.get("GEMINI_TEXT_MODEL", DEFAULT_MODEL)
        self.agent_name = agent_name
        self.client = genai.Client(api_key=self.api_key)

    def _build_tools(self, tools):
        # Reuse the schema converter — see Step 3a below.
        ...

    async def run_turn(self, ..., prompt_text=None) -> TurnResult:
        # client.aio.models.generate_content(
        #     model=self.model,
        #     contents=prompt_text,
        #     config=types.GenerateContentConfig(
        #         system_instruction=system_prompt,
        #         tools=gemini_tools,
        #         # NOTE: tool_config IS supported on generate_content
        #         # (the gap is only on LiveConnectConfig).
        #         # We deliberately leave mode at AUTO — see risk #4.
        #     ),
        # )
        ...
```

**Response parsing details** (Codex flagged these gaps):

The Gemini SDK returns a `GenerateContentResponse` with `candidates` (list).
A robust parser must handle:
- `response.candidates is None or empty` → record as `PROVIDER_ERROR`.
- `candidates[0].content.parts` — iterate parts; some are `text`, some
  are `function_call`.
- `response.function_calls` — a convenience accessor on the SDK that
  flattens function calls across parts. Use this as the primary
  extraction path; fall back to iterating parts only if it's None on
  the installed SDK version.
- Multiple function calls in one response — append all to `tool_calls`;
  scoring uses the first one as the "primary".
- `response.prompt_feedback.block_reason` → if non-None, the prompt was
  blocked (safety filter). Record as `PROVIDER_ERROR` with the reason.

**Step 3a — Extract `_schema_from_dict` helper.** Move the static method
`GeminiLiveAdapter._schema_from_dict` (currently `gemini_live.py:60-82`)
to a module-level function `_schema_from_dict(d: dict) → types.Schema` in
a new `src/voice_bench/adapters/_gemini_common.py`. Both
`gemini_live.py` and `gemini_text.py` import from there. Keep behavior
byte-identical to avoid regressing the voice adapter.

**Mode=AUTO decision** (was risk #4):  the original draft proposed
`mode="ANY"` to force tool calling. Codex correctly pointed out this
would inflate `negative_prompt_violation` counts in `scoring.py:68-80`
because Gemini would call a tool on every negative prompt. Stick with
`mode=AUTO` for fair cross-provider comparison. Documented in the
adapter docstring.

### Step 4 — Centralize adapter construction (shared by `cli.py` and `runner.py`)

Codex correctly pointed out that `cli.py:29-43` (probe dispatch) and
`runner.py:74-86` (run dispatch) are two separate if/elif chains. Adding
agents to only one breaks the other. Fix: extract a single registry.

Create `src/voice_bench/adapters/registry.py`:

```python
def build_adapter(agent: str):
    """Construct an adapter by agent name. Lazy-imports the provider SDK
    so a missing optional dep only breaks its own agent, not unrelated ones."""
    if agent == "gemini-live":
        from .gemini_live import GeminiLiveAdapter
        return GeminiLiveAdapter()
    if agent == "openai-realtime":
        from .openai_realtime import OpenAIRealtimeAdapter
        return OpenAIRealtimeAdapter()
    if agent == "claude-opus":
        from .claude_text import ClaudeTextAdapter
        return ClaudeTextAdapter(model="<verified-in-step-0>", agent_name="claude-opus")
    if agent == "claude-sonnet":
        from .claude_text import ClaudeTextAdapter
        return ClaudeTextAdapter(model="<verified-in-step-0>", agent_name="claude-sonnet")
    if agent == "gpt-5":  # renamed from gpt-text, see Step 0
        from .gpt_text import GPTTextAdapter
        return GPTTextAdapter(model="<verified-in-step-0>", agent_name="gpt-5")
    if agent == "gpt-4o":
        from .gpt_text import GPTTextAdapter
        return GPTTextAdapter(model="<verified-in-step-0>", agent_name="gpt-4o")
    if agent == "gemini-pro":
        from .gemini_text import GeminiTextAdapter
        return GeminiTextAdapter(model="<verified-in-step-0>", agent_name="gemini-pro")
    if agent == "gemini-flash":
        from .gemini_text import GeminiTextAdapter
        return GeminiTextAdapter(model="<verified-in-step-0>", agent_name="gemini-flash")
    raise ValueError(f"Unknown agent: {agent}")
```

Update `cli.py` and `runner.py` to call `build_adapter(agent)` instead
of maintaining their own if/elif chains. **Also remove the eager
top-of-file `from .adapters.gemini_live import GeminiLiveAdapter` in
`runner.py:11`** — push it inside the registry so a missing
`google-genai` install doesn't break Claude/GPT users.

```python
# cli.py
VALID_AGENTS = [
    "gemini-live", "openai-realtime",  # voice
    "claude-opus", "claude-sonnet",    # Anthropic text
    "gpt-5", "gpt-4o",                 # OpenAI text (renamed gpt-text→gpt-5)
    "gemini-pro", "gemini-flash",      # Google text
]
```

**Backwards-compatibility note**: the `gpt-text` agent name is removed.
Old result rows in `results/` keep `agent="gpt-text"` for the dashboard's
historical view; new runs use `gpt-5`. Document in revision history.

### Step 5 — Add `model_kind` + `ttf_request_to_call_ms` to the timeline

**Gemini's critique was correct**: faking `ts_input_audio_end` for text
adapters smushes two distinct things (audio→tool latency vs network
RTT→tool latency) onto the same metric and the same chart. Instead:

1. Add `model_kind: Literal["voice", "text"]` to `TurnTimeline` in
   `models.py`. Default value: `"voice"` (so existing JSONL rows
   reconstruct sensibly).
2. Add `ttf_request_to_call_ms` property to `TurnTimeline` —
   `(ts_first_tool_call_emitted - ts_connect_start) * 1000`. Only set
   when `model_kind == "text"`.
3. `Score` gets a corresponding `ttf_request_to_call_ms` field. Existing
   `ttf_tool_ms` stays voice-only (None for text rows).
4. The dashboard (Story 2) splits its latency chart by `model_kind`
   and uses the right field per kind. Until Story 2 lands, the existing
   dashboard will show `None` for text-row latency — acceptable
   regression because we're rebuilding the dashboard next.

**Per-turn timeout**: wrap the SDK call in `asyncio.timeout(t["first_tool"])`
(default 60s; use the `timeouts` dict passed into `run_turn`). If it
trips, record `TerminalReason.TIMEOUT_FIRST_TOOL`. Currently the text
adapters silently hang if the API stalls.

**Old-result compatibility**: `_rescore` in
`scripts/build_dashboard.py:26-63` reconstructs `TurnTimeline` from
JSONL via `TurnTimeline(**kwargs_filtered)`. Adding a new optional
field with a default value won't break existing rows. Verify with a
manual reload of `results/claude-opus-*.jsonl` after the refactor.

### Step 6 — Run the smoke benchmark

```bash
for agent in claude-opus claude-sonnet gpt-5 gpt-4o gemini-pro gemini-flash; do
  voice-bench run --agent $agent --tools 10 --mode smoke
done
```

Verify each finishes in <2 min and writes a JSONL with at least one
`passed` row. Smoke mode picks all prompts tagged `smoke: true` in the
manifest — verify the manifest has at least 5 smoke-tagged prompts that
hit the loaded tools at 10t (`runner.py:61-63`).

### Step 7 — Generate the fixed evaluation manifest

Address Gemini's "shifting denominator" critique. Build
`prompts/manifest_text_eval.json`: a 30-prompt subset hand-curated so
each prompt maps to a tool covered by all of the 10/20/30 tool tiers.
Source from the existing `manifest_v2.json` if it already has the
coverage; otherwise extend with new prompts.

The eval manifest is **fixed across runs**. At 10t, only prompts whose
`expected_tool` is in the first 10 tools are scored; the rest are
recorded with `terminal_reason="OUT_OF_TOOL_SCOPE"` and excluded from
the accuracy numerator and denominator. Add this terminal reason to
`models.py` and the filter logic to `runner.py:91-95`.

### Step 8 — Run the full progressive sweep

```bash
for agent in claude-opus claude-sonnet gpt-5 gpt-4o gemini-pro gemini-flash; do
  for tools in 10 20 30; do
    voice-bench run --agent $agent --tools $tools --mode full \
        --manifest manifest_text_eval
  done
done
```

That's 18 runs × ~30 prompts × (text-model latency ~2-5s each) ≈ 20-60
min of wall time. Run in background, capture totals in a summary.

**Rate-limit handling**: this story does NOT add automatic backoff to
`runner.py`. If a sweep 429s, the user re-runs from where it stopped.
Acceptable for a one-shot sweep. **A follow-up story** will add
resume-by-prompt-id when this becomes a routine operation.

### Step 9 — Document findings

Add a short `docs/findings/text-model-comparison.md` (1 page max) with:
- Accuracy table (model × tool-count grid)
- Latency P50 / P95 per model
- One-line conclusion: "Use X for orchestrator, Y for sub-router."

This feeds directly into Story 4 (Two-Layer Pipeline) where the model
choice gets locked in.

## Risks & Open Questions

1. **`gemini-3.1-pro-preview` may rate-limit aggressively**. The free
   tier on Google AI Studio is 5 RPM. 18 × 50 = 900 requests over the
   full sweep. Either run with longer sleeps between turns or upgrade
   to a paid tier before running. Mitigation: detect 429 and back off
   exponentially; cap total runtime at 60 min and resume on retry.

2. **`gpt-5.5` may not actually exist on the OpenAI API**. The fallback
   to `gpt-4o` is in place, but if `gpt-5.5` 404s on every call we're
   silently testing `gpt-4o` twice (once as `gpt-text` fallback, once as
   `gpt-4o` direct). Verify `client.models.retrieve("gpt-5.5")` succeeds
   in the probe; if not, drop the `gpt-text` agent or rename it.

3. **Sonnet 4.6 vs Opus 4.7 tool-calling**. Anthropic claims Sonnet is
   "near-Opus" on tool calling, but with smaller models the JSON arg
   extraction sometimes degrades on numeric / enum fields. The
   benchmark will show this if it's true. No mitigation needed —
   we're measuring it on purpose.

4. **Gemini text with `mode="ANY"` may force tool calls on negative
   prompts**. Our manifest doesn't currently have many negatives, but
   the few it has would always fail with mode=ANY. Two options:
   (a) set mode=AUTO and accept lower tool-call rate, or
   (b) set mode=ANY but skip negative prompts in scoring for Gemini.
   Picking (a) keeps the metric comparable across providers. **Decision:
   use mode=AUTO**, document that this gives Gemini a fair shot at
   declining to call tools.

5. **Latency definition asymmetry**. `ttf_tool_ms` for text models
   includes network RTT and TLS setup; voice models exclude session
   setup. This is an apples-to-oranges field name. Add a `model_kind`
   ("voice" or "text") column to the dashboard so the user knows not
   to compare them directly in a single chart. Story 2 (Dashboard v2)
   should formalize this.

6. **Anthropic SDK is sync-only in the existing adapter**. `claude_text.py`
   wraps it in `run_in_executor`. Fine for single-threaded sweeps; if
   we ever parallelize the benchmark this needs to change to
   `anthropic.AsyncAnthropic`. Not blocking this story.

7. **Clean text vs ASR transcripts** (Gemini raised this). The
   benchmark uses pristine `prompt.text` from the manifest. In
   production, the text sub-router will receive transcripts produced
   by the voice orchestrator — with hesitations, ASR errors, missing
   punctuation. So this benchmark measures an **upper bound** on text
   sub-router accuracy, not the actual production accuracy. **Mitigation:**
   accepted as a limitation for this story. Story 4 (Two-Layer
   Pipeline) measures the real thing by feeding the voice model's
   ASR transcript into the text sub-router. We use this story's data
   to *select* the model, knowing the absolute numbers will be lower
   in Story 4 but the relative ordering should hold.

8. **Provider/model CLI syntax — registry vs path-like** (Gemini
   raised this). The registry hard-codes agent name → model ID pairs
   in `registry.py`, which can't test arbitrary new models without
   code changes. Alternative: `--agent anthropic/claude-sonnet-4-6`
   parses provider+model from the flag.
   **Decision**: keep the registry for THIS story (it's simple, it
   makes the canonical 6 agents the visible vocabulary, and the
   benchmark publishes those as the result-row `agent` field). Add
   the path-like syntax as a follow-up when we have a second model
   from any provider worth pinning. The registry is not load-bearing
   — swapping later is a refactor not a rewrite.

## Out of Scope

- Anthropic Haiku — user-explicitly excluded as too weak.
- Parallelizing benchmark runs (currently serial; a 60-min sweep is
  acceptable).
- Adapter for xAI/Grok, Mistral, etc.
- Building the two-layer pipeline (Story 4).
- Re-running voice-bench against existing audio with these text adapters
  — they're text-only; audio is handled by Story 4.
- ASR transcript input mode (Gemini suggested using actual transcripts
  from prior voice runs). Acknowledged as the right way to measure
  production sub-router accuracy. Deferred — Story 4 closes this gap
  by running the full pipeline.
- Path-like `--agent provider/model_id` CLI syntax. Acknowledged as a
  cleaner long-term abstraction. Deferred — see Risk #8.

## Reviewer Feedback

### Codex (round 1)

**Critical issues raised:**
1. `gpt-5.5` likely invalid — current default in `gpt_text.py:25`
   silently falls back to `gpt-4o`.
2. Fallback path mutates `self.model` after one failure, contaminating
   subsequent prompts in the run without row-level visibility.
3. `ADAPTER_REGISTRY` in `runner.py` only would not fix the parallel
   adapter switch in `cli.py:29-43` (probe).
4. Lazy-import registry conflicts with eager import at `runner.py:11`.
5. `agent_name` plumbing incomplete — adapters hard-code labels at
   `claude_text.py:67/88` and `gpt_text.py:69/90`.
6. Gemini `mode="ANY"` breaks negative-prompt scoring at
   `scoring.py:68-80`.
7. Gemini response parsing needs explicit handling for missing
   candidates, multiple parts, `prompt_feedback.block_reason`.
8. Text adapters ignore the `timeouts` dict — sync SDK in
   `run_in_executor` can hang the run indefinitely.
9. `ttf_tool_ms` AC conflicts with `models.py:31-34` semantics for
   no-call turns (`scoring.py:82-94`).
10. New agents won't find their system prompt at
    `prompts/system/<agent>.md` (`runner.py:27`) without an alias map.
11. Model IDs need provider-verified IDs, not memorized strings.
12. `gemini-3.1-pro-preview` specifically should be tested with
    `models.list()` before the full run.

**Nice-to-have raised:**
- `VALID_TOOL_COUNTS` already has 30 (`cli.py:15`).
- Probe should exercise tool calling.
- Capture effective served model in result rows.
- Smoke mode is "tagged smoke", not "first five".
- No rate-limit backoff in `runner.py`.

**Resolution:** Added Step 0 (model-ID verification), centralized
adapter construction in a new registry module (Step 4), fixed the
silent-fallback bug (Step 2), added explicit Gemini response parsing
(Step 3), added per-turn timeouts (Step 5), added alias logic for
system prompts (Acceptance), removed stale `--tools 30` AC. Adopted
mode=AUTO for Gemini. Deferred rate-limit retries to a follow-up
story; documented in Step 7.

### Gemini Pro (round 2)

**Architectural concerns raised:**
1. Benchmark uses clean manifest text; production sub-router will see
   ASR-produced transcripts with errors and disfluencies. Inflates
   accuracy artificially.
2. Registry `agent_name → model_id` mapping is leaky/premature —
   can't test new models without code changes.
3. Modality smushing — text and voice latency on the same chart hides
   that text RTT includes setup that voice doesn't, and vice versa.
4. Shifting denominator — at 10t/20t/30t the prompt pool changes, so
   the accuracy curve isn't measuring "same task, more distractors."

**Alternatives suggested:**
- `--agent anthropic/claude-sonnet-4-6` path-like CLI.
- Decouple metrics by modality (`model_kind` field, separate latency
  field per kind).
- ASR transcript benchmarking — feed prior voice-run transcripts as
  input to text adapters.
- Static test set per tool tier — same 30 prompts, distractors as
  noise.

**Resolution:**
- **Adopted**: `model_kind` field + separate `ttf_request_to_call_ms`
  latency (Step 5 rewritten). Static test set with fixed denominator
  via `manifest_text_eval.json` and a new `OUT_OF_TOOL_SCOPE`
  terminal reason (Step 7).
- **Adopted with rationale**: registry stays for this story (Risk
  #8); path-like syntax is a follow-up. ASR transcript input is
  Story 4's concern (Risk #7).
- This story now produces a clean upper-bound number per model;
  Story 4 lands the production-realistic number.

## Revision History

- 2026-05-18 — Initial draft
- 2026-05-18 — Round 1: Codex feedback (12 critical, 4 of 5
  nice-to-haves, rate-limit retry deferred)
- 2026-05-19 — Round 2: Gemini Pro feedback (4 architectural concerns;
  2 adopted as in-scope, 2 deferred with rationale)
