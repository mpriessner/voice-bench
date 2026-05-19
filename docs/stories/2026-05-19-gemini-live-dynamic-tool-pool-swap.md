# Story: Dynamic Tool-Pool Swap (Gemini Live)

**ID:** 2026-05-19-gemini-live-dynamic-tool-pool-swap
**Status:** Reviewed — awaiting approval
**Created:** 2026-05-19

## Goal

Add a Gemini Live equivalent to the just-shipped OpenAI Realtime
swap, so the user has the same model-driven mid-conversation
tool-pool swap on both providers. The Gemini adapter
(`gemini-live-swap`) uses session restart with session resumption —
the closest analog to OpenAI's `session.update` available on
Gemini's SDK.

Per user decision (2026-05-19), this story delivers the swap
mechanism only. The originally-drafted "pre-routing" adapter was
considered and rejected because the user's use case explicitly
requires mid-conversation swaps ("I work in a lab and then need
chemistry tools, then change to camera tools"). A ~5s wait with
the model verbally confirming "okay, the lab tools are loaded now"
is acceptable to the user; the architectural objections that
motivated pre-routing apply to consumer voice UX, not to deliberate
lab-context-switching.

The two adapters must produce **directly comparable** JSONL rows so
we can score them head-to-head on `manifest_swap.json`, **with
explicit per-row labelling** of the swap mechanism used (since
OpenAI's `session.update` and Gemini's session-restart-with-
resumption are structurally different operations).

## Context

### What's already shipped (OpenAI swap)

The OpenAI Realtime swap is implemented and validated:
- 10/11 = 90.9% accuracy on `manifest_swap.json`
- 4/4 swap turns succeeded with RTT 198–239ms via `session.update`
- Architecture: composition (not inheritance), one fresh adapter per
  scenario, `model_kind="voice_swap"` rows in JSONL output
- Reusable assets:
  - `src/voice_bench/toolsets.py` — 4 named pools (camera_basics,
    camera_advanced, lab_imaging, lab_data), all ≤17 tools, plus
    `build_core()` returning the always-loaded set + 2 swap primitives
  - `src/voice_bench/scoring_swap.py` — `SwapScore` dataclass with
    `is_swap_turn`/`swap_happened`/`swap_rtt_ms`/`toolset_at_call`
  - `prompts/manifest_swap.json` — 6 scenarios, 11 turns
  - `src/voice_bench/models.py` — already extended:
    `model_kind="voice_swap"`, `ts_swap_request`/`ts_swap_ack`/
    `swap_rtt_ms` on `TurnTimeline`, `toolset_at_call` on `ToolCallEvent`
  - `scripts/build_dashboard.py` — already skips swap rows in the
    main heatmap aggregator

### Why we need Gemini parity

User-stated requirement: a runtime fallback. If OpenAI quota /
availability / API surface breaks, the iOS app should be able to switch
to Gemini Live and keep the same dynamic-pool behaviour. Building parity
in the benchmark first lets us:
- Verify the swap mechanism works on Gemini Live before we put it on
  user devices
- Quantify the latency / accuracy delta between the two providers so
  the fallback choice is informed, not blind

### Why this is harder than OpenAI

The OpenAI swap relies on `session.update` — a documented client event
that replaces `session.tools` on an open WebSocket in ~200ms.

Gemini Live's installed SDK (`google-genai==2.4.0`, pinned via
`uv.lock`) does **not** expose any equivalent operation. Empirical
inspection of the SDK (`.venv/lib/python3.11/site-packages/google/genai/
live.py`) confirms:
- `AsyncSession` exposes only: `close`, `receive`, `send`,
  `send_client_content`, `send_realtime_input`, `send_tool_response`,
  `start_stream`. **There is no `update` method.**
- `send_tool_response`'s docstring states tools are configured through
  `config.tools` at `connect` time, not mid-session.
- `LiveClientMessage.setup` exists in the type system but is annotated
  "SDK users should not send this message" — sending it directly would
  require unsupported raw websocket writes.
- `client.aio.live.connect(...)` returns an **async context manager**;
  the yielded `AsyncSession` itself is NOT a context manager (no
  `__aenter__` / `__aexit__`). To manually manage the lifecycle we
  must retain the connect-cm object and call its `__aexit__` to tear
  down.

**Two plausible implementation paths**, given the SDK reality:

1. **Session restart with session resumption** — Gemini Live exposes
   `session_resumption: SessionResumptionConfig` in `LiveConnectConfig`
   (`handle: Optional[str]`, `transparent: Optional[bool]`). Server
   sends `session_resumption_update` messages with a handle the client
   can later replay. **The crucial unknown**: whether resumption
   preserves conversation context AND accepts a different `tools`
   array on the new session. This is exactly what the Phase-0 probe
   must determine.
2. **Clean session restart with synthetic context replay** — close
   the current session, open a new one with new tools, replay the
   user's last transcribed utterance as an `input_text` content item.
   Loses native conversation memory; relies on a transcript replay.
   Always available as a fallback.

A third option — **pre-warmed parallel sessions** — is out of scope:
4× the connection count, race conditions on audio routing, complex
teardown.

The Phase-0 probe is the **single most important deliverable** in
this story. It must determine:
- Whether session resumption preserves conversation context across
  reconnect (path 1 viable for context preservation).
- Whether the resumed session uses the new tools array provided at
  reconnect time (path 1 viable for swap).
- The observed restart latency for both paths.

### Risky assumption to call out loudly

That **session resumption with a different tools array actually works**
on Gemini Live in May 2026. The SDK type annotations for
`SessionResumptionConfig` describe disconnect recovery, not tool
swapping. There is no public guarantee that:
- (a) the server accepts a resumption with a different `tools` config,
- (b) the model treats the new tools as authoritative,
- (c) the conversation history remains semantically intact across
  the reconnect.

Codex's critique flagged additional risks (now captured in Risks &
Open Questions): resumption is documented as possibly **impossible
while function-call execution is in flight** (R11 below). We must
wait for a `session_resumption_update` where `resumable == true`
after sending the `switch_toolset` tool response, BEFORE attempting
to close and reopen. Otherwise the resumption may silently fail or
the next session may lose data.

If (a)/(b)/(c) cannot all be verified empirically by the probe, the
implementation **defaults to path 2 (clean restart with transcript
replay)**. Path 1 is an optimization layered on top, gated on probe
confirmation per-test.

## Architectural framing — what we are NOT measuring

Gemini round-2 review pushed hard on whether we're building a usable
fallback or a benchmark target. The honest answer is: **both, and the
distinction must be visible in the output**.

- The **swap mechanism** (session restart + resumption) is structurally
  different from OpenAI's `session.update`. OpenAI keeps a hot WebSocket
  and replaces the tools array in-place (~200ms). Gemini tears down
  the TCP/TLS connection and re-handshakes (3–6s). Calling both
  "swap_rtt_ms" in the same column would be a category error.
  **Mitigation**: the benchmark output explicitly records, per swap,
  the `swap_mechanism` field (`session_update` |
  `session_resumption` | `clean_restart`), so the dashboard and any
  downstream analysis can distinguish.
- **Session granularity differs**: OpenAI holds one WebSocket across
  scenario turns; Gemini opens a new session per turn because
  `session.receive()` terminates at `turn_complete`. Whether this
  meaningfully affects scoring is open — the model's stateful memory
  is the API server's, not the client's. **Mitigation**: AC adds an
  explicit no-swap control run for Gemini (`gemini-live-swap` on a
  scenario where `initial_toolset` already covers all expected tools)
  to isolate the swap-mechanism cost from the session-granularity cost.
- **Audio context differs across swap paths**: resumption preserves
  audio context server-side (we believe — probe TEST A continuity
  check is the empirical check); `clean_restart` replays a text
  transcript, which strips prosody. **Mitigation**: row includes
  `mechanism_used` per swap event so downstream consumers can split
  metrics by mechanism.

What we ARE measuring honestly:
- Whether Gemini's session-resumption mechanism can carry a swap at
  all (probe TEST A).
- The accuracy delta between OpenAI swap and Gemini swap on identical
  scenarios — with the asterisks above.
- The user-visible swap latency for Gemini (including the verbal
  readiness confirmation, AC13) versus the OpenAI single-frame
  swap. This is the metric that informs whether the lab-UX
  "wait ~5s then hear 'ready'" pattern is acceptable in practice.

## Acceptance Criteria

- [ ] **AC1** — `uv run voice-bench probe --agent gemini-live` still
  passes (no regression on the existing single-turn adapter).
- [ ] **AC2** — `uv run voice-bench run --agent gemini-live --tools 5
  --mode smoke` still produces identical-shape JSONL/CSV rows (no
  regression on the existing benchmark path).
- [ ] **AC3** — A new `uv run voice-bench probe-gemini-swap` command
  empirically determines whether session resumption with a different
  `tools` array works on the installed SDK, and prints the observed
  swap RTT (median over ≥3 attempts) for whichever mechanism is
  selected (resumption vs clean restart). The probe must print the
  installed `google-genai` version and the exact model used.
- [ ] **AC4** — A new agent `gemini-live-swap` is registered in
  `VOICE_AGENTS` and `build_adapter`. `uv run voice-bench swap --agent
  gemini-live-swap --manifest manifest_swap` runs the same 6
  scenarios used for the OpenAI swap and writes results to a
  `…_swap.jsonl` file with the **same row shape** as the OpenAI
  swap output. `model_kind="voice_swap"` and `agent="gemini-live-swap"`
  must appear on each row (overriding the inherited
  `gemini-live`/`voice` defaults from `gemini_live.py:120`).
- [ ] **AC5** — The swap runner (`src/voice_bench/swap_runner.py`) is
  refactored to be **adapter-agnostic** without losing scenario
  config. It receives an **adapter factory callable** (e.g.
  `partial(build_adapter, agent_name)` that the runner then calls
  with scenario-specific `initial_toolset=...` kwargs). Passing
  `agent_name` alone is **not sufficient** — that would lose
  `initial_toolset` and break scenarios like `ss004` which start in
  `lab_imaging`. The OpenAI swap path must produce identical JSONL
  byte-for-byte pre/post refactor (regression diff captured).
- [ ] **AC6** — `scoring_swap.score_swap_turn` works **unchanged** for
  Gemini swap rows. The Gemini adapter populates the same fields on
  `TurnResult` (`tool_calls`, `timeline.ts_swap_request`,
  `timeline.ts_swap_ack`, `toolset_at_call`) that the scorer reads.
  `toolset_at_call` must be captured **at the moment the tool call
  lands** (before the swap mutates `_current_toolset`).
- [ ] **AC7** — `scripts/build_dashboard.py` reads both
  `openai-realtime-swap` and `gemini-live-swap` runs without crashing
  and produces a swap-comparison panel (or section) that lists, per
  adapter: total turns, passed turns, median swap RTT, terminal-reason
  breakdown. This is **not a stretch goal** — it is the only way to
  see the head-to-head comparison the user asked for.
- [ ] **AC8** — Offline tests in `tests/test_swap_adapter_contract.py`
  (no API key, no network):
  - `_visible_tools()` on both swap adapters returns
    `core + pool[current_toolset]` after construction.
  - Mutating `_current_toolset` updates `_visible_tools()`.
  - Constructor with `initial_toolset="bogus"` raises `ValueError`.
  - Tests parametrise across both adapter classes and use a
    `monkeypatch` to set `OPENAI_API_KEY` / `GEMINI_API_KEY` to dummy
    values so constructors don't bail out. (Alternative: add a
    `_skip_client_init: bool = False` constructor kwarg to both
    adapters for test-only no-client construction — the simpler
    fix.)
- [ ] **AC9** — `prompts/system/gemini-live-swap.md` exists with
  swap-specific instructions tuned for Gemini's voice. The
  `swap_runner._load_system_prompt` function must be refactored to
  load the agent-specific prompt (currently hard-coded to
  `openai-realtime-swap.md` at `swap_runner.py:19`).
- [ ] **AC10** — README (or a `docs/swap.md` if README is too long)
  has a short "Dynamic tool-pool swap on Gemini" section pointing at
  the probe + CLI command and noting the observed latency delta vs
  OpenAI.
- [ ] **AC11** — `swap_runner._run_scenario` passes the turn's
  `text` field through to `adapter.run_turn` as `prompt_text=`. The
  current signature drops it (`swap_runner.py:99-105`); the Gemini
  clean-restart fallback requires it for synthetic context replay.
- [ ] **AC12** — Per-swap mechanism labelling. Contract:
  - Each **swap event** within a turn has a scalar `mechanism`
    field: `"session_update"` (OpenAI), `"session_resumption"`
    (Gemini — resumption path worked), or `"clean_restart"`
    (Gemini — resumption path errored, fell back to clean session).
    Stored on `TurnResult` as a new `swap_events: list[dict]`
    field. Each dict has keys: `from_pool`, `to_pool`, `mechanism`,
    `swap_rtt_ms`, `swap_mechanism_ms`.
  - The **JSONL row** (one row per turn) has a top-level
    `mechanisms_used: list[str]` field that is the deduplicated
    list of mechanism values from `swap_events`. For turns with no
    swap event, `mechanisms_used = []`.
  - The dashboard's aggregator (Phase 8) reads
    `swap_events` rather than `mechanisms_used` so it can group
    per-event by mechanism. `mechanisms_used` is for human eyeballing
    and quick filtering, not for the aggregator.
- [ ] **AC13** — After a swap completes (new Gemini session is
  open with the new toolset), the adapter MUST trigger the model
  to verbally confirm readiness before the next user utterance.
  Concretely:
  1. The new session's `LiveConnectConfig` must include
     `output_audio_transcription: AudioTranscriptionConfig()` in
     addition to the existing `input_audio_transcription`. The
     existing `GeminiLiveAdapter._build_config` does NOT set this
     (`gemini_live.py:82`); the swap adapter overrides
     `_build_config` to add it. Without this, the confirmation
     speech is audio-only (`msg.data`) and the scorer cannot read
     the transcript.
  2. After `__aenter__` returns on the new session, send a
     synthetic `input_text` content item along the lines of
     "You've just switched to the <toolset_name> toolset. In one
     short sentence, tell the user the new tools are ready."
     with `turn_complete=True`.
  3. Iterate `session.receive()` until
     `server_content.turn_complete`. Capture the confirmation
     transcript from `msg.server_content.output_transcription.text`
     (primary) with `msg.text` as a fallback for any text-modality
     edge cases.
  4. Also record `confirmation_audio_bytes` (sum of
     `len(msg.data)` across the confirmation turn) so we have an
     audio-success signal independent of the transcript.
  5. Append the captured transcript to `transcripts["ai"]` for the
     turn that triggered the swap.
  6. **ts_swap_ack semantics**: set at `turn_complete` of the
     confirmation, NOT at `__aenter__`. This means
     `TurnTimeline.swap_rtt_ms` (= `ts_swap_ack - ts_swap_request`)
     measures **user-visible swap readiness latency** — the time
     from "switch_toolset tool call lands" to "model has finished
     saying it's ready". This is the metric that matters for the
     lab UX. (See AC13b for the pure mechanism cost, which is the
     primary cross-provider KPI.)
  7. **Bounded receive loop** (Gemini round 2 — rambling guard):
     the confirmation-drain loop must enforce TWO time bounds —
     the existing per-message `quiet` timeout (default 5s) AND a
     hard wall-clock cap of `8s` on the total confirmation receive
     duration. If the model rambles past 8s, break out, record
     `confirmation_truncated=True` in the swap event, and proceed
     with `ts_swap_ack=time.time()`. This prevents a hallucinating
     model from inflating swap RTT indefinitely.
  8. **Refusal/no-response handling**: if the model never produces
     any output (no `output_transcription`, no `msg.data`, no
     `msg.text`) before the 8s cap, record
     `confirmation_text_received=False`, `confirmation_audio_bytes=0`,
     and still set `ts_swap_ack` so the turn proceeds. The scorer
     surfaces this as a soft failure (the swap mechanism worked
     mechanically but the UX cue was missing).
- [ ] **AC13b** — Capture two additional timestamps on
  `TurnTimeline` to separate mechanism cost from confirmation cost:
  - `ts_swap_session_opened: Optional[float]` — set when
    `__aenter__` returns on the new session (pure reconnect cost).
  - `swap_mechanism_ms` derived property =
    `(ts_swap_session_opened - ts_swap_request) * 1000`, rounded.
  - `swap_ux_delay_ms` derived property =
    `(ts_swap_ack - ts_swap_session_opened) * 1000`, rounded —
    the time spent on the verbal-readiness confirmation alone.
  This lets the dashboard report three numbers cleanly:
  total user-visible latency (`swap_rtt_ms`), pure mechanism cost
  (`swap_mechanism_ms`), and confirmation-generation cost
  (`swap_ux_delay_ms`). Required model change in
  `src/voice_bench/models.py:TurnTimeline`.
- [ ] **AC13c** — **`swap_mechanism_ms` is the primary
  cross-provider KPI** (Gemini round 2 architectural ask).
  OpenAI's `swap_mechanism_ms` ≈ its `swap_rtt_ms` (single
  WebSocket frame). Gemini's `swap_mechanism_ms` measures the
  TCP/TLS reconnect, comparable apples-to-apples with OpenAI's
  metric. The dashboard's primary headline number is
  `swap_mechanism_ms` (median per agent). `swap_rtt_ms` becomes a
  secondary Gemini-specific UX metric. README and dashboard
  footnote (AC14) call out this hierarchy explicitly.
- [ ] **AC14** — The dashboard's swap-comparison panel (Phase 8)
  must include a footnote that explicitly states `swap_rtt_ms`
  measures different things across providers (single WebSocket
  frame for OpenAI; full TCP/TLS reconnect + verbal readiness
  confirmation for Gemini). `tests/test_dashboard_footnote.py`
  builds the dashboard from a fixture JSONL containing both
  providers' rows and asserts that the rendered HTML contains the
  required footnote text. This is a real test, not a no-op
  assertion — silently dropping the footnote in a future refactor
  must fail CI.

## Implementation Plan

### Phase 0 — Empirical probe (RISK GATE — must pass before any other work)

Add `scripts/probe_gemini_session_swap.py`, modelled on the existing
`scripts/probe_session_update.py`. **TEST 1 (mid-session reconfigure)
has been dropped from the probe** — Codex verified the installed SDK
(`google-genai==2.4.0`) has no method to mutate tools on a live
session, and `LiveClientMessage.setup` is annotated "SDK users should
not send this message". Pursuing TEST 1 would require unsupported raw
WebSocket writes; not worth the bug surface for a fallback adapter.

**Phase 0a — Pre-probe doc check (zero code):**
1. The probe script's header must print, at runtime: installed
   `google-genai` version (`google.genai.__version__` → "2.4.0"
   today), the model name in use (env-overridable, default
   `gemini-3.1-flash-live-preview`), and the platform / Python
   version. This lets reviewers reproduce the result against the
   exact SDK that was probed.
2. Optionally (recommended, not blocking): use the `context7` MCP
   server (resolve-library-id → query-docs) to pull current Gemini
   Live API docs at probe time. Search terms: "session resumption",
   "tools change", "live api reconnect". Record findings as a
   comment block in the probe output.

**Phase 0b — Probe script (text-mode, no audio):**

The probe runs two tests sequentially:

```
TEST A: Session resumption swap (preferred path)
  - Open session_A with:
      tools = [toggle_flash, toggle_grid_overlay, toggle_macro_mode]
      session_resumption = SessionResumptionConfig(transparent=True)
  - Send a text turn via send_client_content (input_text content):
      "Turn on the flash"
  - Iterate async for message in session_A.receive():
      - Capture the first session_resumption_update message and
        record handle = update.new_handle. CRITICAL: only attempt
        the swap once we see an update where resumable == true (the
        SDK type docs say resumption is impossible during in-flight
        function-call execution).
      - When message.tool_call arrives, send_tool_response.
      - When server_content.turn_complete arrives, break.
  - At this point: handle is captured and the response stream is
    complete.
  - Tear down session_A:
      await connect_cm.__aexit__(None, None, None)
      # We retained the *connect context manager* object, not just
      # the session, because AsyncSession is not itself a context
      # manager in google-genai 2.4.0.
  - Open session_B with:
      tools = [set_exposure, set_zoom, set_iso]                # NEW pool
      session_resumption = SessionResumptionConfig(handle=handle)
  - Wait for setup_complete (or first message) — record latency.
  - Send a NEW text turn via send_client_content:
      "Set ISO to 400"
  - Pass conditions (ALL must hold):
      (a) session_B opened without error,
      (b) the model called set_iso (a new-pool tool),
      (c) the model did NOT call toggle_flash or any session_A tool,
      (d) optionally probe continuity by sending:
          "Which toolset did I just switch from?"
          The model should answer with a flash/camera reference,
          not "I don't know" or hallucination.
  - Record swap_rtt = (ts after session_B's setup_complete) -
                     (ts before connect_cm_B.__aenter__)

TEST B: Clean restart with transcript replay (fallback path)
  - Open session_A with tools_A, NO resumption.
  - Send "Turn on the flash". Capture the user transcript via
    conversation.item.input_audio_transcription.completed (or, since
    we're text-mode, just remember the literal text).
  - On tool_call, respond, drain to turn_complete.
  - Tear down session_A.
  - Open session_B with tools_B (new pool), NO resumption.
  - Inject synthetic context as input_text:
      "Continuing from your last turn — the user said:
       'Turn on the flash' and you've now switched to a different
       toolset. Now they say: 'Set ISO to 400'"
  - Pass conditions:
      (a) session_B opened,
      (b) the model called set_iso,
      (c) model did NOT call any session_A tool.
  - Record clean_restart_rtt.
```

**Gate condition**: TEST B must succeed (clean restart always works
unless the API is fundamentally broken). TEST A success is **a
bonus** — if it passes, the production adapter uses resumption for
context continuity; if it fails, the adapter falls back to clean
restart with transcript replay. **Either way**, Phase 1 onwards
proceeds.

**Probe output schema** (printed as JSON at end of run):
```json
{
  "sdk_version": "2.4.0",
  "model": "gemini-3.1-flash-live-preview",
  "test_a_resumption_passed": true,
  "test_a_swap_rtt_ms_median": 4200,
  "test_a_swap_rtt_ms_samples": [4100, 4300, 4200],
  "test_a_continuity_check_passed": true,
  "test_b_clean_restart_passed": true,
  "test_b_swap_rtt_ms_median": 3800,
  "test_b_swap_rtt_ms_samples": [3700, 3800, 3900],
  "selected_mechanism": "resumption"
}
```

The probe ≥3 attempts per test so the median RTT is meaningful.
The selected_mechanism is written into the swap adapter at Phase 2
as a default; can be overridden via env var.

### Phase 0.5 — Wiring prerequisites

Mandatory blockers, must land first so subsequent code compiles:

- Add `"gemini-live-swap"` to `VOICE_AGENTS` in
  `src/voice_bench/cli.py:14`.
- Add `gemini-live-swap` registry entry in
  `src/voice_bench/adapters/registry.py` pointing at the new adapter
  class (initially can be a no-op subclass so imports work).
- Confirm `models.py` already has `model_kind="voice_swap"` and the
  swap timeline fields (verified — added during OpenAI swap story).
- Verify the system prompt path
  `prompts/system/gemini-live-swap.md` exists or is created in Phase 7
  (gated; the adapter has a fallback prompt for now).

### Phase 1 — Refactor swap_runner to be adapter-agnostic (AC5)

The current `swap_runner.py:11` does `from .adapters.openai_realtime_swap
import OpenAIRealtimeSwapAdapter` and instantiates it at line 150
with the scenario-specific `initial_toolset=initial_ts` kwarg.
**Codex flagged that a naïve refactor to `build_adapter(agent_name)`
would lose `initial_toolset` and silently regress `ss004`** (which
declares `initial_toolset: lab_imaging` in `manifest_swap.json`).

The refactor must preserve per-scenario constructor kwargs.
**Pattern: adapter factory callable.**

1. Define an `AdapterFactory` protocol in
   `src/voice_bench/adapters/registry.py`:
   ```python
   class SwapAdapterFactory(Protocol):
       def __call__(self, initial_toolset: str) -> Any: ...
   ```
2. Add `build_swap_adapter_factory(agent_name: str) -> SwapAdapterFactory`
   to `registry.py` that returns a factory closure binding `agent_name`
   and accepting `initial_toolset` at call time:
   ```python
   def build_swap_adapter_factory(agent_name: str) -> SwapAdapterFactory:
       if agent_name == "openai-realtime-swap":
           from .openai_realtime_swap import OpenAIRealtimeSwapAdapter
           from ..toolsets import TOOLSETS, build_core
           return lambda initial_toolset: OpenAIRealtimeSwapAdapter(
               toolsets=TOOLSETS,
               core_tools=build_core(),
               initial_toolset=initial_toolset,
           )
       if agent_name == "gemini-live-swap":
           from .gemini_live_swap import GeminiLiveSwapAdapter
           from ..toolsets import TOOLSETS, build_core
           return lambda initial_toolset: GeminiLiveSwapAdapter(
               toolsets=TOOLSETS,
               core_tools=build_core(),
               initial_toolset=initial_toolset,
           )
       raise NotImplementedError(f"No swap factory for {agent_name!r}")
   ```
3. `swap_runner.run_swap_benchmark` takes a new `agent_name: str`
   kwarg (default `"openai-realtime-swap"` to preserve current
   behaviour) and uses `build_swap_adapter_factory(agent_name)` to
   construct per-scenario adapters. The factory is called with the
   scenario's `initial_toolset`.
4. `swap_runner._load_system_prompt` becomes
   `_load_system_prompt(agent_name: str) -> str` and loads the
   agent-specific markdown:
   ```python
   path = PROMPTS_DIR / "system" / f"{agent_name}.md"
   if path.exists():
       return path.read_text()
   return _DEFAULT_SWAP_PROMPT  # the fallback string already in code
   ```
5. `swap_runner._run_scenario` is updated to pass `prompt_text=turn.get("text")`
   into `adapter.run_turn(...)` (currently dropped). This makes
   the user transcript available to adapters that need to replay
   it (Gemini clean-restart fallback).
6. The runner reads `_current_toolset` for `toolset_at_turn_start`
   on the adapter (already does this; both adapter classes must
   expose this attribute).
7. JSONL rows already include `agent` only implicitly via
   `result.to_dict()`. The runner now adds an explicit `agent`
   top-level field on every row so the dashboard can group by
   provider.
8. `cli.swap_cmd` (in `cli.py:207`) gets a `--agent` flag,
   `type=click.Choice(["openai-realtime-swap", "gemini-live-swap"])`
   default `"openai-realtime-swap"`.

**Backward-compat regression check** (mandatory): run
`uv run voice-bench swap` (no `--agent` flag) before and after the
refactor on the same machine, against the same `manifest_swap.json`.
Compare the generated JSONL files. Differences allowed:
- `run_id` timestamp
- `ts_*` timestamps
- `swap_rtt_ms` values (live API jitter)

Differences NOT allowed:
- field names, field order, nesting structure
- `model_kind`, `manifest`, `initial_toolset`, `toolset_at_turn_start`,
  `prompt.*`, `score.*` field values
- The set of present/absent rows

This is the gate that proves the refactor didn't regress the OpenAI
swap. If the regression diff finds anything in the "not allowed"
list, revert and rethink.

### Phase 2 — Gemini Live swap adapter

Add `src/voice_bench/adapters/gemini_live_swap.py`. Composition over
inheritance (mirror the OpenAI swap structure).

**Constructor:**

```python
class GeminiLiveSwapAdapter:
    REQUIRES_AUDIO = True

    def __init__(
        self,
        toolsets: dict[str, list[DummyTool]] | None = None,
        core_tools: list[DummyTool] | None = None,
        initial_toolset: str = "camera_basics",
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        swap_mechanism: str | None = None,  # "resumption" | "clean_restart"
        _skip_client_init: bool = False,    # test-only escape hatch
    ) -> None:
        self._toolsets = toolsets if toolsets is not None else TOOLSETS
        self._core_tools = core_tools if core_tools is not None else build_core()
        self._current_toolset = initial_toolset
        if initial_toolset not in self._toolsets:
            raise ValueError(
                f"Unknown initial_toolset {initial_toolset!r}. "
                f"Valid: {sorted(self._toolsets)}"
            )
        self._swap_mechanism = swap_mechanism or os.environ.get(
            "GEMINI_SWAP_MECHANISM", "resumption"
        )

        # Test-only: skip client construction so unit tests can exercise
        # _visible_tools() / state mutation without env vars.
        if _skip_client_init:
            self.api_key = None
            self.model = model or DEFAULT_MODEL
            self.client = None
            return

        # ... API key / model / voice setup (mirror GeminiLiveAdapter) ...
```

**Why `_skip_client_init`**: Codex flagged that
`OpenAIRealtimeSwapAdapter.__init__` raises `ValueError` without
`OPENAI_API_KEY`. The same will happen here. AC8 offline tests need
a way to construct the adapter without a real client. The
`_skip_client_init` escape hatch is the simplest path; add it to the
OpenAI adapter too as part of this story so the parametrised tests
work on both.

**Critical state held across the swap:**

```python
self._connect_cm = None        # the connect-cm OBJECT (not the session)
self._session: AsyncSession | None = None
self._resumption_handle: str | None = None
self._fallback_locked: bool = False  # circuit breaker (Gemini r2)
                                     # — once True, all subsequent
                                     # swaps in THIS adapter instance
                                     # skip resumption. Reset only by
                                     # constructing a new adapter
                                     # (per-scenario factory pattern).
self._swap_events: list[dict] = []   # per-event mechanism log for AC12
self._call_id_to_name: dict[str, str] = {}   # not needed for Gemini —
                                              # fc.name is always present.
                                              # Keep for symmetry with OpenAI.
```

**SDK lifecycle (CRITICAL — Codex flagged this):**
The `AsyncSession` in `google-genai 2.4.0` has **no `__aenter__` /
`__aexit__`**. Only `client.aio.live.connect(...)` is a context
manager. To support mid-scenario teardown without using `async with`:

```python
self._connect_cm = self.client.aio.live.connect(
    model=f"models/{self.model}", config=config
)
self._session = await self._connect_cm.__aenter__()
# ... use session ...
await self._connect_cm.__aexit__(None, None, None)   # explicit teardown
self._session = None
self._connect_cm = None
```

This pattern is well-defined in Python — async context managers can be
driven manually. Document this clearly in the adapter; it's
non-obvious from the SDK docs.

**The receive loop is single-session-bound** (Codex critical issue):
`async for message in session.receive()` is an async generator bound
to one WebSocket and yields **only until** `server_content.turn_complete`.
You cannot continue iteration on a new session by rebinding
`self._session`. The receive loop must explicitly:

1. Break out of the current `session.receive()` iterator when a swap
   is decided.
2. Tear down the old connect-cm.
3. Open a new connect-cm.
4. Start a fresh `new_session.receive()` loop.

**run_turn behaviour — branches on `self._swap_mechanism`:**

#### `swap_mechanism = "resumption"` (preferred path, if probe TEST A passed)

```python
async def run_turn(self, audio_wav_path, tools, system_prompt,
                   turn_id, prompt_id, timeouts=None,
                   prompt_text=None) -> TurnResult:
    # 1. Open new session FOR THIS TURN (Gemini's session.receive
    #    semantics are turn-bound; we don't try to reuse a session
    #    across run_turn calls).
    config = self._build_config(self._visible_tools(), system_prompt,
                                resumption_handle=self._resumption_handle)
    self._connect_cm = self.client.aio.live.connect(
        model=..., config=config)
    self._session = await self._connect_cm.__aenter__()

    try:
        # 2. Send audio
        await self._session.send_client_content(turns={...},
                                                turn_complete=True)
        # 3. Receive loop
        pending_swap_to = None
        async for message in self._session.receive():
            # 3a. Capture resumption handle (whenever it updates)
            if message.session_resumption_update:
                upd = message.session_resumption_update
                if upd.resumable and upd.new_handle:
                    self._resumption_handle = upd.new_handle
            # 3b. Tool calls
            if message.tool_call:
                # CRITICAL: Codex flagged multi-call-in-one-message risk.
                # Gemini may include switch_toolset + a task tool in the
                # SAME message.function_calls list. We must:
                #   - Respond to EVERY function_call in this message
                #     before tearing down (server expects matching ids).
                #   - If switch_toolset appears, defer the swap until
                #     ALL responses for this message are sent AND we've
                #     observed a session_resumption_update with
                #     resumable=true.
                # ToolCallEvent rows are populated with toolset_at_call
                # captured BEFORE any swap mutation.
                for fc in message.tool_call.function_calls:
                    toolset_at_this_call = self._current_toolset
                    tool_calls.append(ToolCallEvent(
                        ..., toolset_at_call=toolset_at_this_call))
                    if fc.name == "switch_toolset":
                        target = (fc.args or {}).get("name", "")
                        if target in self._toolsets:
                            pending_swap_to = target
                            response = {"result": "ok",
                                        "switched_to": target}
                        else:
                            response = {"error": "unknown_toolset",
                                        "available": sorted(self._toolsets)}
                    elif fc.name == "list_toolsets":
                        response = {"toolsets": [
                            {"name": k, "description": v}
                            for k, v in TOOLSET_DESCRIPTIONS.items()
                        ]}
                    else:
                        # Normal task tool — fire DummyTool side-effect
                        matching = next((t for t in self._visible_tools()
                                         if t.name == fc.name), None)
                        if matching:
                            matching(turn_id=turn_id, **(fc.args or {}))
                        response = {"result": "ok"}
                    # Send response immediately so the server's
                    # function-call state is satisfied.
                    await self._session.send_tool_response(
                        function_responses=types.FunctionResponse(
                            name=fc.name, response=response, id=fc.id,
                        ))
            # 3c. End of turn
            if message.server_content and \
               message.server_content.turn_complete:
                break

        # 4. If a swap is pending, do it now (after turn_complete,
        #    NOT mid-turn, to avoid stranding function-call state).
        if pending_swap_to is not None:
            # 4a. Wait briefly for a resumable resumption_update if we
            #     don't already have a resumable handle. Bound by timeout.
            #     (See R11 below.)
            # 4b. Tear down current session
            timeline.ts_swap_request = time.time()
            old_pool = self._current_toolset
            await self._connect_cm.__aexit__(None, None, None)
            # 4c. Open new session with new pool. Try resumption first;
            #     on any error (R19), fall back to clean restart and
            #     record the mechanism actually used.
            #     CIRCUIT BREAKER (Gemini round 2): if a prior swap in
            #     this adapter instance already fell back, skip the
            #     resumption attempt entirely. This prevents repeated
            #     ~10s double-latency penalties when the resumption
            #     path is structurally broken (e.g., Google rejected
            #     it). `self._fallback_locked` is reset only when a
            #     new adapter instance is constructed (per-scenario
            #     factory pattern, AC5).
            new_visible = (self._core_tools +
                           self._toolsets[pending_swap_to])
            if self._fallback_locked:
                # Skip resumption attempt
                new_config = self._build_config(
                    new_visible, system_prompt,
                    resumption_handle=None,
                )
                self._connect_cm = self.client.aio.live.connect(
                    model=..., config=new_config)
                self._session = await self._connect_cm.__aenter__()
                mechanism_used = "clean_restart"
            else:
              try:
                new_config = self._build_config(
                    new_visible, system_prompt,
                    resumption_handle=self._resumption_handle,
                )
                self._connect_cm = self.client.aio.live.connect(
                    model=..., config=new_config)
                self._session = await self._connect_cm.__aenter__()
                mechanism_used = "session_resumption"
              except Exception as exc:
                # Resumption rejected by server, network error, etc.
                # Fall back to a clean session without the handle.
                # Trip the circuit breaker so subsequent swaps in
                # THIS adapter instance skip the resumption attempt.
                raw_events.append(RawProviderEvent(
                    turn_id=turn_id, ts=time.time(),
                    kind="resumption_fallback",
                    payload_json=json.dumps({
                        "error": str(exc),
                        "type": type(exc).__name__,
                    }),
                ))
                self._resumption_handle = None
                self._fallback_locked = True   # CIRCUIT BREAKER
                new_config = self._build_config(
                    new_visible, system_prompt,
                    resumption_handle=None,
                )
                self._connect_cm = self.client.aio.live.connect(
                    model=..., config=new_config)
                self._session = await self._connect_cm.__aenter__()
                mechanism_used = "clean_restart"
            # Only mutate _current_toolset after the new session is open.
            # If both attempts raised, we never reach this line and the
            # exception propagates — the caller sees terminal_reason
            # PROVIDER_ERROR and the scenario row records the failure.
            self._current_toolset = pending_swap_to
            timeline.ts_swap_session_opened = time.time()
            self._swap_events.append({
                "from_pool": old_pool,
                "to_pool": pending_swap_to,
                "mechanism": mechanism_used,
            })
            # 4d. AC13: trigger verbal readiness confirmation
            confirmation_prompt = (
                f"You've just switched to the {pending_swap_to} "
                f"toolset. In one short sentence, let the user know "
                f"the new tools are ready."
            )
            await self._session.send_client_content(
                turns={
                    "role": "user",
                    "parts": [{"text": confirmation_prompt}],
                },
                turn_complete=True,
            )
            # 4e. Drain receive() until the confirmation turn completes.
            #     Capture transcript so the scorer can verify the model
            #     actually responded. Bound by quiet timeout (default
            #     5s) — if no confirmation arrives, mark
            #     confirmation_text_received=False and continue (don't
            #     fail the whole turn).
            confirmation_text = ""
            try:
                async with asyncio.timeout(t["quiet"]):
                    async for msg in self._session.receive():
                        if msg.server_content:
                            if msg.text:
                                confirmation_text += msg.text
                            if msg.server_content.turn_complete:
                                break
            except asyncio.TimeoutError:
                pass
            transcripts["ai"] += confirmation_text
            # 4f. ts_swap_ack is the moment the model finished
            #     confirming — i.e. when the new session is actually
            #     ready to converse, not when __aenter__ returned.
            timeline.ts_swap_ack = time.time()
    finally:
        # 5. Always tear down on turn exit
        if self._connect_cm is not None:
            try:
                await self._connect_cm.__aexit__(None, None, None)
            except Exception:
                pass
            self._connect_cm = None
            self._session = None
    return TurnResult(...)
```

**Key design choices:**

- **Per-turn session, not per-scenario session.** The OpenAI swap holds
  the WebSocket open across multiple turns. For Gemini, the
  `session.receive()` iterator terminates at `turn_complete`, so there
  is no benefit to holding the connection across turns. Open per-turn,
  carry the resumption handle on `self`.
- **Swap happens AFTER turn_complete.** Doing it mid-turn risks
  stranding multi-call function-response state (Codex critical issue).
  The swap RTT measurement spans from "switch_toolset tool call
  lands" (`ts_swap_request`) through reconnect AND the verbal
  readiness confirmation (`ts_swap_ack`). The pure reconnect cost
  is captured separately as `ts_swap_session_opened` for analysis
  (AC13b).
- **Resumption handle persists across turns** via
  `self._resumption_handle`, captured from
  `session_resumption_update.new_handle` whenever
  `resumable == True`.
- **The TurnTimeline must override `agent` and `model_kind`**:
  ```python
  timeline = TurnTimeline(
      turn_id=turn_id,
      agent="gemini-live-swap",       # not "gemini-live"
      prompt_id=prompt_id,
      model_kind="voice_swap",        # not the default "voice"
  )
  ```
  Codex flagged that the existing adapter defaults at `gemini_live.py:120`
  would otherwise leak through and break dashboard filtering.

#### `swap_mechanism = "clean_restart"` (fallback path)

Same as above, but `resumption_handle=None` on the new session and a
synthetic-context replay is sent as the **first item** of the next
turn. The replay format:

```python
synthetic_context = (
    f"[System: toolset just changed from {old_pool} to {new_pool}. "
    f"The user's previous statement was: \"{last_user_transcript}\". "
    f"Continue with their original intent under the new toolset.]"
)
await new_session.send_client_content(
    turns=[{
        "role": "user",
        "parts": [{"text": synthetic_context}],
    }],
    turn_complete=False,
)
```

The `last_user_transcript` comes from `prompt_text` (passed in by AC11)
or, if missing, from a captured input transcript via
`conversation.item.input_audio_transcription.completed` events during
the previous turn.

**Latency expectation**: 3–6 seconds per swap (per the probe). Document
in the dashboard read-through (Phase 8).

### Phase 3 — `list_toolsets` / `switch_toolset` semantics on Gemini

The Gemini SDK uses `types.FunctionDeclaration` / `types.Tool` for
function calling. The existing toolsets define `switch_toolset` and
`list_toolsets` as `DummyTool` instances with JSON-Schema-shaped
`parameters` dicts (`src/voice_bench/toolsets.py:7-39`). The Gemini
adapter already converts `DummyTool` → `FunctionDeclaration` via
`schema_from_dict` (`gemini_live.py:60-74`).

The two meta tools must work identically across both adapters:
- `switch_toolset(name)` — Gemini sends `tool_call.function_calls`
  with `fc.name == "switch_toolset"` and `fc.args == {"name": "..."}`.
- `list_toolsets()` — synthesise a `FunctionResponse` payload with
  the names + descriptions from `TOOLSET_DESCRIPTIONS`.

Confirm by tracing `gemini_live.py:186-234`: tool calls are dispatched
in the `if message.tool_call:` branch. Mirror the OpenAI swap dispatch
logic in `gemini_live_swap.py`.

### Phase 4 — Toolset / system prompt

Add `prompts/system/gemini-live-swap.md`. Start by copying
`prompts/system/openai-realtime-swap.md` and tweaking:
- Reference "your current toolset" terminology
- Note that swaps take a few seconds (may need to say "switching tools…")
- Same boolean direction rules (hide→false, show→true) as the OpenAI
  prompt

### Phase 5 — Reuse benchmark assets (zero changes)

`prompts/manifest_swap.json` — used as-is.
`src/voice_bench/scoring_swap.py` — used as-is. The scorer reads
`tool_calls`, `timeline.ts_swap_request`, `timeline.ts_swap_ack`,
`toolset_at_call` from the `TurnResult`. The Gemini swap adapter must
populate these correctly:
- `tool_calls` — the existing Gemini adapter already builds
  `ToolCallEvent` objects (`gemini_live.py:210-218`); the swap variant
  must also set `toolset_at_call=self._current_toolset` BEFORE the
  swap mutates state. **Sequencing matters**: capture the toolset
  name at the moment the tool call lands.
- `timeline.ts_swap_request` / `ts_swap_ack` — populated in the swap
  path of `run_turn`.

### Phase 6 — CLI

`src/voice_bench/cli.py`:
- Add `"gemini-live-swap"` to `VOICE_AGENTS`.
- Add `--agent` flag to the existing `swap` command (default
  `openai-realtime-swap`).
- Add a new `probe-gemini-swap` command that invokes
  `scripts/probe_gemini_session_swap.py`.

`src/voice_bench/adapters/registry.py`:

```python
if agent == "gemini-live-swap":
    from .gemini_live_swap import GeminiLiveSwapAdapter
    from ..toolsets import TOOLSETS, build_core
    return GeminiLiveSwapAdapter(toolsets=TOOLSETS, core_tools=build_core())
```

### Phase 7 — Tests (offline + smoke)

Two layers of tests:

**Layer 1 — offline unit tests (no API key required):**
Add `tests/test_swap_adapter_state.py`. Tests:
1. `test_visible_tools_initial` — after construction with
   `initial_toolset="camera_basics"`, `_visible_tools()` returns
   `core + pool["camera_basics"]`.
2. `test_visible_tools_after_swap` — manually set
   `_current_toolset="lab_imaging"`, confirm `_visible_tools()` now
   returns `core + pool["lab_imaging"]`.
3. `test_invalid_initial_toolset_raises` — constructor with
   `initial_toolset="bogus"` raises `ValueError`.
4. Run these against **both** `OpenAIRealtimeSwapAdapter` and
   `GeminiLiveSwapAdapter` (parametrised) to guarantee the contract
   matches.

These tests run with `uv run pytest tests/`. The repo currently has
no `tests/` directory (verified earlier in the OpenAI story); this
phase creates one with a minimal `conftest.py`.

**Layer 2 — live smoke test (API key required):**
1. `uv run voice-bench probe --agent gemini-live` — existing path
   regression.
2. `uv run voice-bench probe-gemini-swap` — Phase-0 probe must pass.
3. `uv run voice-bench swap --agent gemini-live-swap --manifest
   manifest_swap` — full scenario benchmark.
4. Comparison: same command with `--agent openai-realtime-swap`,
   compare accuracy + RTT to validate side-by-side reporting.

### Phase 8 — Dashboard swap-comparison panel (AC7 — not stretch)

Codex flagged that the original draft made AC7 contradict Phase 8
(AC said "side-by-side comparison", Phase 8 said "stretch goal"). AC7
is now real and non-optional: the user explicitly asked for head-to-head
comparison.

Concrete work in `scripts/build_dashboard.py`:

1. Confirm the swap-skip logic still works (it does — line is
   `if d.get("model_kind") == "voice_swap" or d.get("scenario_id"):
   continue` in the main aggregator). No regression there.
2. Add a new aggregator pass that **only** keeps swap rows
   (`model_kind == "voice_swap"`) and walks each row's
   `swap_events` list (per AC12 contract). For each
   (`agent`, `event.mechanism`) bucket, compute:
   - `swap_event_count` — total events in this bucket
   - `median_swap_mechanism_ms` — pure reconnect cost (the
     **primary cross-provider KPI**, per AC13c)
   - `median_swap_rtt_ms` — total user-visible latency
     (secondary; for Gemini ≈ mechanism + UX delay; for OpenAI ≈
     mechanism — these mean different things on purpose)
   - `median_swap_ux_delay_ms` — confirmation generation cost
     (Gemini only; ≈ 0 for OpenAI which has no verbal cue step)
   And separately, per `agent` (turn-level rather than event-level):
   - `total_turns`, `passed_turns`, `accuracy`
   - `terminal_reason` breakdown
   - `fallback_locked_rate` — % of scenarios where the circuit
     breaker tripped (high values indicate Gemini's resumption path
     is structurally broken and needs investigation)
   - For Gemini rows: `confirmation_text_received` rate (AC13 check
     — did the model actually say "ready" after the swap?
     Derived from non-empty captured confirmation transcript /
     non-zero `confirmation_audio_bytes`)
   - For Gemini rows: `confirmation_truncated_rate` — % of swap
     events where the 8s rambling guard fired
3. Emit a small HTML section with a 2-row table: OpenAI swap,
   Gemini swap. If Gemini's runs used a mix of `session_resumption`
   and `clean_restart` mechanisms, render two Gemini sub-rows
   (one per mechanism) so the user can see the split. The table
   header includes a footnote that `swap_rtt_ms` measures different
   things per mechanism (single WebSocket frame for OpenAI; full
   TCP/TLS reconnect for Gemini).
4. The section is hidden if no swap rows are loaded.

Acceptance check: after running both swap benchmark commands
(`voice-bench swap --agent openai-realtime-swap` and
`voice-bench swap --agent gemini-live-swap`), rebuild the dashboard
and visually confirm the panel shows the comparison rows with the
mechanism footnote rendered.

### Phase 9 — Documentation

- Add a "Dynamic tool-pool swap on Gemini" section to
  `README.md` (or `docs/swap.md` if README too long). Content:
  - Mechanism used (from probe result: reconfigure / resumption /
    clean restart).
  - Observed swap RTT and accuracy.
  - When to prefer OpenAI vs Gemini (latency vs availability
    trade-off).
- Update `docs/stories/2026-05-19-openai-realtime-dynamic-tool-pool-swap.md`
  with a one-line cross-reference to this story.

## Risks & Open Questions

- **R1 — Mid-session reconfigure structurally unsupported**: confirmed
  by SDK inspection (`AsyncSession` has no `update` method;
  `LiveClientMessage.setup` is annotated "SDK users should not send
  this message"). Implementation does not attempt this; probe TEST 1
  was dropped.
- **R2 — Session resumption may ignore new tools array**: the SDK
  type docs for `SessionResumptionConfig` describe disconnect
  recovery, not tool swapping. The probe (TEST A) verifies this
  empirically by asking for a new-pool-only tool after reopen. If
  the model calls an old-pool tool, the implementation falls back to
  `clean_restart` mechanism.
- **R3 — Resumption may be impossible during in-flight function
  calls**: the SDK type docs state explicitly that resumption may
  not be possible while function-call execution is in flight. The
  adapter MUST wait for a `session_resumption_update` with
  `resumable == True` after sending the `switch_toolset` tool
  response, BEFORE attempting to close and reopen. Bound the wait
  with a timeout (default 2s); on timeout, fall back to
  `clean_restart` for this swap event.
- **R4 — Audio buffer drop on session close**: when we close the
  current session to swap, any uncommitted audio is lost. The plan
  defers the swap until `turn_complete`, which eliminates this in
  the benchmark. The iOS app would need to either buffer audio
  client-side or accept a brief blackout — call out in README.
- **R5 — Clean-restart transcript replay drift**: replaying
  `prompt_text` (or `input_audio_transcription`) doesn't perfectly
  preserve audio prosody or the model's prior reasoning. This will
  show up as scoring noise on `clean_restart` runs. Mitigation:
  Phase-0 probe should report whether `resumption` actually preserves
  continuity — if so, we use it by default.
- **R6 — Cross-adapter scoring drift on `toolset_at_call`**: the
  scorer reads `tool_calls[i].toolset_at_call`. The contract pinned
  in AC6 is: `toolset_at_call` is the value of
  `self._current_toolset` **at the moment the tool call landed**,
  BEFORE any swap mutation. AC8 unit tests parametrise across both
  adapters to enforce this.
- **R7 — `swap_runner` refactor risk**: validated 10/11 OpenAI swap
  path must not regress. Mitigation: AC5 regression diff (Phase 1
  step describes the pre/post JSONL comparison).
- **R8 — google-genai SDK churn**: SDK is `2.4.0` today, with
  significant Live API changes between 1.x and 2.x. The probe prints
  the version at runtime so reviewers can re-validate after SDK
  upgrades. `pyproject.toml` should be tightened to a `~=2.4` or
  similar pin before merging this story.
- **R9 — Gemini model availability**: the adapter uses
  `gemini-3.1-flash-live-preview`. Preview models can be deprecated.
  Model name is env-overridable
  (`GEMINI_LIVE_MODEL`, `gemini_live.py:56`); store the model used
  in the benchmark output.
- **R10 — Restart latency may be too slow for production fallback**:
  if probe RTT is 4–6s per swap, the fallback is technically working
  but UX-broken. Decision to ship Gemini as fallback in the iOS app
  is informed by — not bound by — this story's results.
- **R11 — Multi-call-in-one-message strands function-call state**:
  Gemini may include `switch_toolset` and a task tool in the SAME
  `message.tool_call.function_calls` list. Codex flagged that
  closing the session before responding to **all** calls in that
  message strands unresolved function-call state on the server.
  **Mitigation**: respond to every function call in the message first
  (via `send_tool_response`), then defer the swap until after
  `turn_complete`. The model may end up calling the task tool against
  the *current* pool (which we know works since it picked the tool);
  the swap is for the NEXT turn.
- **R12 — `session.receive()` is single-turn-bound**: `async for
  message in session.receive()` terminates at `turn_complete`. Cannot
  be continued across sessions by rebinding `self._session`. The
  adapter pattern is per-turn session, with resumption handle
  carried on `self`. Confirmed by SDK source at
  `.venv/.../google/genai/live.py:433`.
- **R13 — Connect-cm lifecycle**: `client.aio.live.connect(...)`
  returns an async context manager; the yielded `AsyncSession` is
  NOT a context manager. The adapter must retain the connect-cm
  object (not just the session) and explicitly call `__aenter__` /
  `__aexit__` for mid-flight teardown. This is the single
  most-likely source of resource leaks / hangs; the implementation
  must wrap the teardown in a `try/finally` block (see Phase 2
  skeleton).
- **R14 — Tests require constructor escape hatch**: both adapter
  constructors currently raise on missing API keys. AC8 unit tests
  need a `_skip_client_init` kwarg on both adapters (this story
  also adds it to `OpenAIRealtimeSwapAdapter` as a small ergonomic
  change). Document that this kwarg is for tests only and must NOT
  appear in the registry construction path.
- **R15 — Timeout discipline on session.receive()**: Codex flagged
  that the current Gemini adapter relies on the outer total
  timeout; `async for ... session.receive()` blocks indefinitely
  if nothing arrives. The swap adapter wraps receive calls in
  `asyncio.wait_for(event_iter.__anext__(), timeout=t["quiet"])`
  (same pattern as the OpenAI swap adapter), and breaks cleanly on
  timeout.
- **R16 — Raw event coverage**: log
  `session_resumption_update`, `go_away`, `tool_call_cancellation`,
  and setup-complete events in `raw_events`. These are essential
  for diagnosing swap failures and are otherwise invisible.
- **R17 — Multi-FunctionResponse batching**: Gemini's
  `send_tool_response(function_responses=...)` accepts a list. When
  a message contains multiple function calls, send all responses
  in a single call (matching the server's "matching ids" expectation
  per the SDK type docs). The existing adapter sends one at a time;
  for the swap adapter, batch.
- **Open Q1**: Should we add a `--swap-mechanism` CLI flag that
  forces `resumption` or `clean_restart` for A/B testing on the
  benchmark? Default: NO. Use what the probe picks. The env var
  `GEMINI_SWAP_MECHANISM` is enough for manual override during
  development.
- **Open Q2**: Should we pre-warm the next likely toolset (open a
  parallel session in the background once `list_toolsets` is
  called)? Default: NO for this story; mentioned as future work.
- **Open Q3**: Should `gemini-live-swap` and `openai-realtime-swap`
  share a `SwapAdapterProtocol`? Codex nice-to-have. **Adopted as a
  light-touch addition**: Phase 1 adds a `typing.Protocol` to
  `registry.py` with the three methods/attrs the runner uses
  (`_current_toolset`, `_visible_tools`, `run_turn`). No inheritance
  enforced; just documents the contract.
- **Open Q4**: Should `gemini-live-swap` be excluded from
  `VOICE_AGENTS` to prevent `voice-bench run --agent gemini-live-swap`
  from accidentally invoking the single-turn path on the swap
  adapter (which doesn't support it cleanly)? Codex nice-to-have.
  Default: YES — add it to a new `SWAP_AGENTS` list and have
  `voice-bench run` reject swap agents with a clear error message.

- **Open Q5 (Gemini round 2)**: should the verbal-readiness cue be
  a **deterministic audio chime** played client-side at
  `ts_swap_session_opened`, rather than an LLM-generated speech
  response? **Trade-offs**:
  - Audio chime: zero hallucination risk; saves 1–3s of LLM
    generation; deterministic latency; consistent UX across
    providers. But: NOT what the user described
    ("model says 'okay, now I'm ready'"); requires client-side
    audio playback logic that the benchmark doesn't have today.
  - LLM-generated speech (current AC13 default): matches the
    user's described UX exactly; the model can adapt the phrasing
    to context ("ready with the lab tools"); but adds variable
    latency, hallucination risk, and bench-only complexity.
  **Default**: LLM-generated speech, per the user's explicit
  description. Flag for user review — they may prefer the chime
  if the variable latency proves annoying in benchmark testing.
  AC13's 8s rambling guard partially mitigates the LLM-rambling
  risk.

- **R23 — LLM rambling on the confirmation turn**: the verbal-
  readiness prompt asks for "one short sentence" but Gemini might
  ramble (return 30+ words, apologize for the wait, etc.),
  inflating `swap_rtt_ms`. **Mitigation**: AC13 step 7 enforces an
  8s wall-clock cap on the receive drain; the event records
  `confirmation_truncated=True` if hit. The dashboard surfaces
  the truncation rate (Phase 8). If the rate exceeds ~20%, the
  prompt needs tightening (or switch to the audio chime
  alternative, Open Q5).

- **R24 — Repeated double-latency without circuit breaker**:
  Without the circuit breaker (Phase 2 step 4c, `_fallback_locked`
  flag), every swap in a scenario where resumption is structurally
  broken pays the resumption-attempt cost AND the clean_restart
  cost (~10s+ each). The circuit breaker locks the adapter to
  `clean_restart` for the rest of its lifecycle after the first
  fallback. Reset happens only via new adapter construction
  (per-scenario factory pattern, AC5).

- **R25 — JSONL schema evolution**: this story changes the swap
  row shape (adds `swap_events: list[dict]`, `mechanisms_used:
  list[str]`, new timeline fields). Verified consumers:
  - `scripts/build_dashboard.py` — updated in Phase 8.
  - `scripts/build_voice_analysis.py` — filters by filename
    regex (`/agent-Nt-timestamp.jsonl`), which swap files don't
    match. Naturally ignores swap rows. **No update needed.**
  Future consumers must check `model_kind=="voice_swap"` before
  reading `swap_events` / `mechanisms_used`.

- **R18 — Cross-provider metric naming is dangerously ambiguous**
  (Gemini round 2): `swap_rtt_ms` means radically different things
  per mechanism. AC12 mitigates by recording `swap_mechanism` per
  row; AC15 promotes honest interpretation via a docstring test.
  But stakeholders looking at the dashboard could still misread.
  **Documentation onus**: the README and dashboard footnote must
  explicitly call out this difference.
- **R19 — Off-label use of `SessionResumptionConfig`** (Gemini round
  2): Google's docs describe resumption for disconnect recovery, not
  schema mutation. If Google tightens backend validation to reject
  resumption attempts that change the `tools` config, the swap
  adapter breaks instantly. **Mitigation**: (a) the
  `clean_restart` mechanism is always available as a fallback
  within this adapter (no resumption handle, just a fresh session
  with the new pool); the adapter auto-falls-back if a resumption
  attempt errors. (b) Add a CI smoke test that runs the probe
  weekly so a sudden regression is caught quickly. (c) The user has
  explicitly accepted the risk that this mechanism is off-label and
  may need rework if Google's API changes. Document in README so
  operators know that periodic re-validation against the live
  Gemini API is required.
- **R20 — Audio context loss on clean_restart**: replaying a text
  transcript strips prosody and pauses. The model on the new
  session is operating on a "translation" of the audio, not the
  audio itself. **Mitigation**: this affects only the `clean_restart`
  path; `session_resumption` should preserve audio context (probe
  TEST A continuity check verifies). The verbal readiness
  confirmation (AC13) means even on `clean_restart` the user
  hears an explicit audible cue that the swap completed, which
  partially compensates for the context loss.
- **R21 — Per-turn session breaks long-context scenarios** (Gemini
  round 2): If a scenario relies on cross-turn conversation memory,
  Gemini's per-turn session may lose it even within a single
  scenario, while OpenAI's continuous WebSocket retains it. Today's
  `manifest_swap.json` scenarios are short enough that this is
  unlikely to matter (3-turn max). **Mitigation**: AC adds a
  "control" run where Gemini swap is exercised on a scenario whose
  `initial_toolset` already covers all needed tools — this isolates
  the session-granularity cost from the swap mechanism cost. If
  the control run scores significantly differently from OpenAI's
  baseline, the comparison is structurally unfair.
- **R22 — iOS integration blind spot** (Gemini round 2): The
  benchmark defers swaps until `turn_complete` to avoid audio buffer
  drops. In production, users may barge in during the 3–6s
  swap. The user has accepted that the lab UX tolerates a brief
  pause provided the model verbally confirms readiness (AC13);
  the deliberate "lab assistant grabbing different tools"
  metaphor matches this cadence. iOS-specific audio plumbing
  (barge-in handling during the swap, queueing user audio for
  the post-confirmation session) is **deferred to the iOS
  integration story** that will follow this one. Document the
  expected UX cadence (swap → ~5s → "ready" → user speaks) in the
  README so the iOS story has a clear contract.

## Out of Scope

- Vector / embedding-based tool retrieval (would be a third story).
- Multi-agent / hierarchical routing.
- Pre-warmed parallel sessions for sub-second Gemini swaps.
- iOS app integration of the swap adapter (separate story).
- Pre-routing adapter (intent classification before opening the
  voice session). Drafted in round-2 of this story, removed in the
  trim round per user decision. The user's lab workflow explicitly
  requires mid-conversation swaps. If swap reliability later proves
  insufficient, pre-routing can be added back as a separate story.
- Improvements to `scoring_swap.py` — used as-is.
- New toolset definitions or new benchmark scenarios.
- Refactoring the existing single-turn `runner.py` to merge with
  swap_runner (Gemini-round-2 alternative #1 in the OpenAI story).
- Fixing the known `ss005t02` string-normalisation scoring gap
  ("trial 1" vs "trial one") — separate issue, affects both
  adapters equally.

## Reviewer Feedback

### Codex (round 1) — 2026-05-19

Critique focused on implementation gaps, SDK reality, and runner
refactor risks. Critical issues:

1. **Session lifecycle plan was too optimistic.**
   `client.aio.live.connect(...)` is an async context manager; the
   yielded `AsyncSession` is not itself a context manager and has no
   `__aenter__` / `__aexit__`. Only exposes `close`, `receive`,
   `send`, `send_client_content`, `send_realtime_input`,
   `send_tool_response`, `start_stream` in `google-genai==2.4.0`.
   Manual lifecycle must retain the connect-cm object, not just
   `self._session`. (`.venv/.../live.py:903`, `live.py:87`)

2. **Mid-session reconfigure is not supported by the installed SDK
   surface.** `AsyncSession` has no `update`; `send_tool_response`
   docstring says tools are set through `config.tools` at connect.
   `LiveClientMessage.setup` exists but is marked "SDK users should
   not send this message" — TEST 1 would require unsupported raw
   websocket writes. (`live.py:348`, `types.py:19382`)

3. **Proposed resumption swap happens at exactly the riskiest time.**
   SDK type docs say resumption may be impossible while the model is
   executing function calls or generating; resuming from such a state
   can lose data. The plan didn't require waiting for a
   `session_resumption_update.resumable == True` after the tool
   response. (`types.py:18536`)

4. **`session.receive()` cannot be "continued" across a replaced
   session.** It's an async generator bound to one websocket and one
   model turn; stops at `turn_complete`. To restart mid-turn, the
   adapter must break out of the old generator, close the old
   connection/context, create a new context, and start a new
   `new_session.receive()` loop. A simple
   `self._session = new_session` rebind inside the iterator will not
   redirect iteration. (`live.py:433`)

5. **TEST 2 cannot verify "new session reports the new tools array"
   through the public SDK.** Initial server response is only
   `setup_complete`; doesn't echo effective tools. Probe can only
   infer new tools by inducing a new-pool function call.

6. **Conversation continuity is under-specified.**
   `SessionResumptionConfig` only has `handle` and `transparent`;
   type docs describe recovering resumable state, not changing tools
   while preserving semantic conversation history. The probe needs a
   concrete continuity prompt, not "doesn't ask what did you say
   earlier."

7. **Multi-call handling is more constrained.** Gemini's
   `LiveServerToolCall.function_calls` is a list, so multiple calls
   in one message are structurally possible. If a message contains
   `switch_toolset` plus another call, the server expects matching
   responses for both calls on the same session. Closing after the
   first call strands unresolved function calls. (`types.py:18372`)

8. **The `swap_runner` refactor as written would regress OpenAI
   scenario initialization.** Current code constructs
   `OpenAIRealtimeSwapAdapter(... initial_toolset=initial_ts)` per
   scenario at `swap_runner.py:147`. Replacing that with
   `build_adapter(agent_name)` loses `initial_toolset`, so `ss004`
   would incorrectly start in `camera_basics` instead of
   `lab_imaging`. Needs an adapter factory callable, not a string
   passed through `build_adapter`.

9. **Offline AC8 tests would fail without API keys** if they
   instantiate current adapters directly.
   `OpenAIRealtimeSwapAdapter.__init__` raises without
   `OPENAI_API_KEY`; Gemini will mirror that. Either constructors
   need a test-friendly no-client path, or tests must monkeypatch
   fake env keys.

10. **Clean-restart fallback needs `prompt_text`**, but
    `swap_runner` currently does not pass it into `run_turn` — only
    passes `audio_wav_path`, `tools`, `system_prompt`, `turn_id`,
    `prompt_id`. The synthetic context replay cannot be implemented
    without changing `swap_runner.py:99`.

11. **Current Gemini timeout logic is weak for restart work.**
    `async for message in session.receive()` can block until the
    outer total timeout; the "quiet timeout" check only runs after a
    message is received. A swap adapter that waits for resumption
    updates, tool responses, or reopen events needs explicit
    `asyncio.wait_for(event_iter.__anext__(), ...)` phases like the
    OpenAI swap adapter.

12. **Gemini swap rows must set
    `TurnTimeline(... agent="gemini-live-swap", model_kind="voice_swap")`.**
    The current Gemini adapter defaults to `agent="gemini-live"` and
    `model_kind="voice"` at `gemini_live.py:119`; missing this breaks
    row comparability and dashboard filtering.

13. **The system prompt loader is OpenAI-specific.**
    `_SWAP_SYSTEM_PROMPT_PATH` is hard-coded to
    `openai-realtime-swap.md` (`swap_runner.py:19`). AC9 will not be
    used unless `_load_system_prompt` becomes agent-aware.

14. **AC7 was inconsistent with Phase 8.** AC said dashboard shows
    both swap adapters side-by-side; Phase 8 made the swap comparison
    panel optional/stretch. If AC7 is real, that dashboard work is
    not optional.

Nice-to-have:
- Small `SwapAdapterProtocol` for `_current_toolset`,
  `_visible_tools()`, `run_turn(...)`.
- Raw-event logging for `session_resumption_update`, `go_away`,
  `tool_call_cancellation`, setup completion.
- Probe should print the installed SDK version and exact model.
- Consider separating swap agents from normal `VOICE_AGENTS` to
  prevent accidental `voice-bench run` invocations on swap adapters.
- For Gemini tool responses, send a list of `FunctionResponse`
  objects when a message includes multiple function calls.
- Explicit fallback path for failed resumption connect.

### Codex critical issues addressed

- (1) Phase 2 connect-cm lifecycle now explicitly documents
  retaining `self._connect_cm` and using manual `__aenter__` /
  `__aexit__`. R13 added.
- (2) TEST 1 dropped from the probe entirely. Context section
  rewritten to call out the SDK reality.
- (3) Phase 2 swap flow waits for `session_resumption_update.resumable
  == True` before close, with a bounded timeout that falls back to
  `clean_restart`. R3 added.
- (4) Phase 2 design changed to **per-turn session**: open at turn
  start, swap happens after `turn_complete`, tear down at turn end.
  R12 added.
- (5) Probe TEST A pass condition is "model calls a new-pool tool"
  (not "server echoes new tools"). Documented in Phase 0b.
- (6) Probe TEST A adds an explicit continuity check ("Which
  toolset did I just switch from?") and reports its result.
- (7) Phase 2 specifies sequential function-response handling for
  ALL calls in a multi-call message before the swap. Swap deferred
  to `turn_complete`. R11 added.
- (8) Phase 1 redesigned around `build_swap_adapter_factory(agent_name)`
  returning a closure that accepts `initial_toolset` at call time.
  AC5 updated. Backward-compat regression diff mandated.
- (9) `_skip_client_init` kwarg added to both swap adapters as a
  test-only escape hatch. R14 added.
- (10) AC11 added: `swap_runner._run_scenario` must forward
  `prompt_text` to `adapter.run_turn`.
- (11) R15 added; Phase 2 wraps receive in
  `asyncio.wait_for(...)`.
- (12) Phase 2 explicitly sets `agent="gemini-live-swap"` and
  `model_kind="voice_swap"` on `TurnTimeline`.
- (13) AC9 + Phase 1 step 4 make `_load_system_prompt` agent-aware
  with a fallback path.
- (14) AC7 is now real; Phase 8 rewritten to mandate the
  swap-comparison panel.

Nice-to-have items adopted:
- `SwapAdapterProtocol` — Open Q3 (adopted as light-touch
  `typing.Protocol`, no inheritance).
- Raw-event logging for swap-specific events — R16 added.
- Probe prints SDK version + model — AC3 updated.
- Swap-agents segregation — Open Q4 (adopted: new `SWAP_AGENTS`
  list, `voice-bench run` rejects swap agents).
- Multi-FunctionResponse batching — R17 added.
- Explicit resumption-connect fallback — covered by R3
  (timeout → clean_restart).

### Gemini (round 2) — 2026-05-19

Critique (verbatim) focused on architectural blind spots and
production-vs-benchmark gap:

**Architectural concerns:**

- **Benchmark Artifact vs. Production Reality:** Plan acknowledges
  4–6s swap latency might be "UX-broken" but proceeds to build it to
  populate a dashboard. If unusable in the iOS app without severe
  conversational disruption, building it purely to force a
  head-to-head comparison is misallocation. The benchmark will
  measure a mechanism we'd never actually ship.
- **Apples-to-Oranges Session Granularity:** OpenAI maintains a
  continuous, stateful WebSocket across turns. The plan forces
  Gemini into a disjointed, per-turn session lifecycle. Operational
  characteristics (TCP/TLS overhead, context accumulation) differ.
  Benchmark claims "same scenarios for both," but the underlying
  network and state models are radically different — the comparison
  is inherently skewed.
- **Betting on an API Loophole:** Relying on
  `SessionResumptionConfig` to perform schema mutation is dangerous
  off-label use. Resumption handles are designed for network
  disconnect recovery; schema is expected immutable. If Google
  tightens backend validation to reject schema mismatches on
  resumed handles, the entire fallback breaks with no recourse.
- **Loss of Audio-Native Context:** The fallback `clean_restart`
  with transcript replay strips all native audio context (prosody,
  tone, pauses) and replaces with synthetic text prompt. OpenAI
  retains full audio context. Benchmark on the Gemini clean-restart
  path is not comparing two "voice" models; it compares a voice
  model against a text-prompted reset.
- **Client Integration Blind Spot:** Deferring swap until
  `turn_complete` avoids audio buffer drops in a synthetic
  benchmark, but leaks badly on iOS. Users barging in while the
  app tears down and rebuilds WebSockets → dropped/buffered audio,
  massive UX failure.
- **Misleading Metric Aggregation:** Dashboard will chart
  `swap_rtt_ms` for both providers. OpenAI's metric is a lightweight
  `session.update` frame over hot socket; Gemini's is TCP connect +
  TLS handshake + WS upgrade + model allocation. Side-by-side under
  the same metric name gives a mathematically true but
  architecturally false comparison.

**Alternatives worth considering:**

- **Scenario-Level Toolset Selection (Pre-Routing):** Instead of
  forcing Gemini to mimic OpenAI's mid-session swapping (which it
  doesn't natively support), use a simpler architecture. Run a fast
  text intent classifier on the user's initial query BEFORE opening
  the voice session. Lock in the correct tool pool at `connect()`
  time and maintain a stable session. If the user drastically
  changes topics, instruct the model to politely ask for a new
  session. Far safer, avoids off-label API hacks, guarantees zero
  mid-conversation audio blackouts on iOS.
- **A/B Scenario Benchmarking (No Swap):** If the goal is truly to
  evaluate Gemini Live's baseline capability against OpenAI,
  benchmark them on their ability to execute the tool pools WITHOUT
  the mid-session swap. If Gemini cannot swap mid-session
  gracefully, the benchmark should reflect that structural
  limitation explicitly (e.g., "Swap Not Supported"), rather than
  engineering an elaborate, brittle polyfill just to populate a CSV
  row.

### Gemini concerns addressed

- **Benchmark vs Production**: Originally addressed by adding a
  separate `gemini-live-prerouting` adapter. **Reversed in the trim
  round** per user decision — the user explicitly wants
  mid-conversation swaps and accepts the ~5s latency cost when
  the model verbally confirms readiness. Gemini's
  "you're building something you wouldn't ship" objection
  doesn't apply: the user IS willing to ship this UX for the lab
  workflow. AC13 (verbal-ready confirmation) addresses the
  UX-feel-of-pause concern — the user isn't sitting in silence,
  the model talks to them.
- **Apples-to-Oranges Granularity**: R21 added. The benchmark
  documents the per-turn-session caveat. If cross-turn memory
  becomes important for future scenarios, add a control run.
- **API Loophole**: R19 addressed within the swap adapter via
  `clean_restart` auto-fallback. The user has explicitly accepted
  that this mechanism is off-label and may need rework if Google's
  API changes; weekly probe in CI gives early warning.
- **Audio Context Loss**: R20 stands. `clean_restart` is the
  second-tier path within the swap adapter; resumption is
  preferred. The verbal-ready confirmation (AC13) means even on
  clean_restart, the user gets an explicit audible cue that the
  swap completed.
- **Client Integration Blind Spot**: R22 still applies — barge-in
  during a swap is unsolved at the iOS level. **Deferred to the
  iOS integration story**. The user's lab UX (deliberate
  context-switch, willing to wait) makes this less acute than for
  consumer voice products.
- **Misleading Metrics**: AC12 + AC14 + Phase 8 footnote require
  per-row `swap_mechanism` labelling and a dashboard footnote
  calling out what `swap_rtt_ms` actually measures per mechanism.

### Gemini alternatives — adoption decisions

- (Pre-routing) — **considered, then rejected** in the trim round.
  The user's stated workflow requires mid-conversation swaps; locking
  toolsets at session-start doesn't fit. Documented in Out of Scope
  with a re-revisit path if swap reliability proves insufficient.
- (A/B no-swap) — **not adopted**. We trust the user's product
  judgment that swap is the right pattern. R21 still suggests a
  control run if cross-turn memory effects show up empirically.

### Codex (round 2 — trim-round re-review) — 2026-05-19

Focused critique of the trim-round changes (verbal readiness
confirmation, removal of pre-routing, latency-semantics shift):

1. **AC13 confirmation transcript capture would be empty in AUDIO
   mode.** `response_modalities=["AUDIO"]` causes the model to emit
   audio bytes via `msg.data`, not text via `msg.text`. The SDK has
   `server_content.output_transcription` but
   `GeminiLiveAdapter._build_config()` only enables
   `input_audio_transcription`. AC13 must require
   `output_audio_transcription: AudioTranscriptionConfig()` in the
   swap adapter's config override.
2. **`ts_swap_ack` semantics contradicted Phase 2 narrative.** AC13
   now sets `ts_swap_ack` at confirmation `turn_complete`, but
   Phase 2 still claimed "swap RTT only counts the close/reopen
   round trip". Fix the contradiction; add a separate
   `ts_swap_session_opened` for pure mechanism cost.
3. **R19 resumption-fallback branch was not in the code skeleton.**
   The story claimed auto-fallback but Phase 2 didn't show it.
   Required: try/except around the resumption-connect, falling
   back to `clean_restart` on error, recording which mechanism was
   used per swap event.
4. **Stale pre-routing references in current sections** (not just
   revision history): "What we ARE measuring" still mentioned
   pre-routing as a measured outcome; R20 said "pre-routing avoids
   this entirely"; R22 said iOS should use pre-routing — directly
   conflicting with the trim decision.
5. **Phase 8 grouping was internally inconsistent.** It said group
   by `agent` × `swap_mechanism` and also compute
   `mechanism_breakdown` per agent×mechanism (tautological at that
   grouping level), and the row contract was ambiguous (scalar
   `swap_mechanism` vs list `mechanisms_used`).

Nice-to-have:
- AC14 docstring-only test was leftover from the 3-way comparison
  framing. Either drop or make it a real assertion that the
  dashboard footnote text exists.
- Timeline comments on `ts_swap_request` / `ts_swap_ack` still
  reference OpenAI-specific `session.update` / `session.updated`.

### Codex round 2 critical issues addressed

- (1) AC13 expanded with 6 sub-steps:
  `output_audio_transcription` config addition, capture from
  `output_transcription.text` with `msg.text` fallback, record
  `confirmation_audio_bytes` as audio-success signal, explicit
  `ts_swap_ack` semantics.
- (2) AC13b added with `ts_swap_session_opened` + derived
  `swap_mechanism_ms`. Phase 2 narrative line corrected.
- (3) Phase 2 step 4c rewritten with try/except resumption →
  clean_restart fallback. Records `mechanism_used` per swap event.
  `_current_toolset` mutates only after successful new-session
  open. `_swap_events.append({...})` records the event for
  AC12 / Phase 8 consumption.
- (4) Three stale references rewritten: "What we ARE measuring"
  now talks about user-visible swap latency; R20 mitigation
  reframed to mention the verbal-ready cue; R22 acknowledges the
  iOS pause is accepted by the user with explicit verbal cue.
- (5) AC12 contract clarified: each swap event has a scalar
  `mechanism`; row-level `mechanisms_used` is the deduplicated
  list. Phase 8 aggregator reads `swap_events` (per-event) not
  `mechanisms_used` (per-row).

Nice-to-have:
- AC14 promoted from docstring-only assertion to real test
  asserting dashboard footnote text exists.
- `TurnTimeline` field comments will be made provider-neutral
  in the implementation (small ergonomic fix, not in the story).

### Gemini (round 2 — trim-round re-review) — 2026-05-19

Critique (verbatim):

**Architectural concerns:**

- **Verbal Readiness Failure Modes (AC13):** Forcing the LLM to
  generate a confirmation introduces non-deterministic latency and
  hallucination risks. The model might refuse the prompt, ramble
  extensively, or ask a clarifying question. The 5s `quiet` timeout
  bounds silence but does not truncate a rambling response, which
  would artificially inflate `swap_rtt_ms` and block the user from
  speaking.
- **In-Turn Fallback Latency Penalty (Phase 2 Step 4c):** Executing
  the `clean_restart` fallback immediately after a
  `session_resumption` failure stacks the latency of both
  operations sequentially (potentially 10+ seconds). There is no
  explicit circuit-breaker to permanently switch
  `self._swap_mechanism` to `clean_restart` for the remainder of
  the session, risking repeated double-latency penalties if the
  session later acquires a new handle that also fails.
- **Metric Conflation (AC13b):** Tracking both `swap_rtt_ms` and
  `swap_mechanism_ms` provides engineering clarity but creates a
  fundamentally skewed primary KPI. OpenAI's `swap_rtt_ms`
  represents pure mechanism cost, while Gemini's `swap_rtt_ms`
  includes variable LLM text generation and TTS time. A footnote
  (AC14) does not fix the fact that the dashboard's top-line
  numbers compare apples to oranges.
- **Downstream Schema Breakage (AC12/Phase 8):** Shifting from
  row-level properties to the `swap_events` list correctly models
  multi-swap turns, but implies schema changes for other JSONL
  consumers. The workspace contains
  `scripts/build_voice_analysis.py`, which likely expects flat
  row-level swap fields and may crash or misreport if it isn't
  updated alongside the dashboard.

**Alternatives worth considering:**

- **Client-Side Audio Cue (Replaces AC13):** Instead of prompting
  the model to speak, the client app or adapter could emit a
  deterministic audio chime ("ping") immediately at
  `ts_swap_session_opened`. This eliminates hallucination risk,
  saves 1-3 seconds of LLM generation latency, provides a crisper
  lab UX, and allows the benchmark to measure true API mechanism
  cost rather than LLM verbosity.
- **Mechanism Circuit Breaker:** Add a `self._fallback_triggered`
  boolean to the adapter. If `session_resumption` fails once, lock
  the mechanism to `clean_restart` for all future swaps in that
  adapter lifecycle. This prevents recurring 10-second penalty
  spikes on repeated swaps.
- **Standardize on Mechanism Latency:** To maintain a coherent
  cross-provider benchmark, the primary dashboard KPI should chart
  `swap_mechanism_ms` for both providers (which is roughly
  equivalent to OpenAI's `session.update`). The verbal
  confirmation delay should be tracked as a separate UX metric
  (`swap_ux_delay_ms`) rather than overloading the definition of
  `swap_rtt_ms`.

### Gemini round 2 concerns addressed

- (Verbal Readiness Failure Modes) — AC13 step 7 (8s wall-clock
  cap, `confirmation_truncated` flag) and step 8 (refusal/no-output
  handling) added. R23 added. Phase 8 dashboard surfaces
  `confirmation_truncated_rate` so the issue is visible without
  manual log digging.
- (In-Turn Fallback Latency Penalty) — Circuit breaker adopted in
  Phase 2 step 4c via `self._fallback_locked` flag. Resets only
  on new adapter construction (per-scenario factory, AC5). R24
  added. Phase 8 surfaces `fallback_locked_rate`.
- (Metric Conflation) — AC13c adopted: `swap_mechanism_ms` is the
  primary cross-provider KPI; `swap_rtt_ms` becomes a secondary
  Gemini-specific UX metric; `swap_ux_delay_ms` separately
  captures confirmation cost. Dashboard primary headline number
  is `swap_mechanism_ms`, with `swap_rtt_ms` and
  `swap_ux_delay_ms` shown alongside.
- (Downstream Schema Breakage) — R25 added with verified consumer
  inventory: `build_dashboard.py` updated in Phase 8;
  `build_voice_analysis.py` filters by filename regex and
  naturally ignores swap rows (no update needed). Future
  consumers documented to check `model_kind=="voice_swap"` before
  reading new fields.

### Gemini round 2 alternatives — adoption decisions

- (Client-Side Audio Cue) — **considered, surfaced as Open Q5 for
  user decision**. Default in the story is the LLM-generated
  confirmation (matches the user's described UX). The chime
  alternative is documented with trade-offs so the user can flip
  if benchmark data shows the verbal cue is too unreliable or
  too slow.
- (Mechanism Circuit Breaker) — **adopted** as
  `self._fallback_locked` in Phase 2 step 4c.
- (Standardize on Mechanism Latency) — **adopted** via AC13c.
  The dashboard primary KPI is now `swap_mechanism_ms`;
  `swap_rtt_ms` is secondary.

## Revision History

- 2026-05-19 — Initial draft
- 2026-05-19 — Round 1 revision after Codex critique. Major changes:
  TEST 1 (mid-session reconfigure) dropped — SDK confirmed
  unsupported. Phase 2 redesigned around per-turn sessions with
  manual connect-cm lifecycle. Phase 1 swap_runner refactor now
  uses adapter-factory callables to preserve `initial_toolset`.
  AC5 / AC7 / AC11 added; AC8 testing approach rewritten with
  `_skip_client_init` escape hatch. R3, R11–R17 added.
- 2026-05-19 — Round 2 revision after Gemini architectural critique.
  Major changes: added `gemini-live-prerouting` as parallel
  production-honest path (AC13, AC14, Phase 7.5). Added explicit
  `swap_mechanism` labelling per row (AC12) and dashboard footnote
  to prevent misleading cross-provider metric comparison. Added
  R18–R22 covering metric ambiguity, off-label API use, audio
  context loss, session-granularity skew, and iOS integration
  blind spot. Goal section rewritten to acknowledge dual delivery
  (swap + pre-routing) and frame the swap adapter as research data
  rather than production-ready code.
- 2026-05-19 — Trim round per user decision. The pre-routing
  adapter was removed: user explicitly wants mid-conversation
  swaps for their lab workflow (chemistry tools → camera tools)
  and is willing to wait ~5s if the model verbally confirms
  readiness. Changes: Goal section narrowed to swap-only; AC13
  rewritten to require a verbal "ready" confirmation after each
  swap (replacing the pre-routing adapter ACs); AC14 renumbered
  (was AC15); Phase 7.5 deleted; Phase 8 dashboard panel reduced
  from 3 rows to 2; R19 mitigation rewritten to use `clean_restart`
  auto-fallback within the swap adapter instead of pre-routing as
  insurance; Out of Scope updated to document pre-routing as
  considered-and-rejected. Phase 2 swap dispatch updated with
  the verbal-confirmation flow (step 4d–4f).

- 2026-05-19 — Codex round 2 (trim-round re-review) fixes. AC13
  expanded to require `output_audio_transcription` config and
  capture from `server_content.output_transcription.text` (audio-mode
  transcript fix). AC13b added (`ts_swap_session_opened` for pure
  mechanism cost, separate from user-visible latency). Phase 2 step
  4c rewritten with try/except resumption→clean_restart fallback
  branch, per-event `mechanism_used` recording. AC12 contract
  rewritten to disambiguate scalar `mechanism` (per event) vs list
  `mechanisms_used` (per row). Three stale pre-routing references
  rewritten in current sections (not just revision history). AC14
  promoted from no-op assertion to real test asserting dashboard
  footnote text. Phase 8 aggregator pass clarified to read
  `swap_events` rather than the deduplicated row-level list.

- 2026-05-19 — Gemini round 2 (trim-round re-review) fixes. AC13
  step 7 (8s wall-clock cap on confirmation drain with
  `confirmation_truncated` flag) and step 8 (no-output handling)
  added to bound the LLM-rambling failure mode. AC13c added: the
  PRIMARY cross-provider KPI is now `swap_mechanism_ms` (pure
  reconnect cost), not `swap_rtt_ms` (Gemini-only UX metric that
  includes confirmation generation). AC13b extended with
  `swap_ux_delay_ms` derived property. Phase 2 step 4c circuit
  breaker (`self._fallback_locked`) added: if a resumption attempt
  fails once, subsequent swaps in the same adapter instance skip
  the retry, preventing repeated ~10s double-latency penalties.
  R23 (rambling), R24 (double-latency), R25 (JSONL schema
  evolution verification) added. Open Q5 (client-side audio chime
  as alternative to LLM-generated confirmation) raised for user
  decision; story keeps LLM-speech as the default per the user's
  described UX. Phase 8 aggregator updated to surface
  `fallback_locked_rate` and `confirmation_truncated_rate`.

**Status:** **Reviewed — awaiting approval**.
