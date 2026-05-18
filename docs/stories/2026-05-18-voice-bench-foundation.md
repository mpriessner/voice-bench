# Story: voice-bench — Foundation & First-Slice End-to-End Loop

**ID:** 2026-05-18-voice-bench-foundation
**Status:** Reviewed — awaiting approval
**Created:** 2026-05-18

## Goal

Bootstrap **voice-bench**, a standalone, web-based, fully-automated benchmarking harness that measures voice-AI agents on two dimensions — **tool-calling correctness** and **time-to-first-sentence (TTFS) latency** — and contains a closed-loop "self-improvement" mode where Claude (or any agent) can iterate on system prompts to drive correctness up. The first slice proves the end-to-end loop with ONE agent + ONE dummy tool before scaling to the full matrix.

## Context

### Why this project exists
Martin builds SciSymbioLens (iOS + Android) — a science-lab field app where the operator's hands are often busy. He wants to drive the app's UI (switch camera, change exposure, start documentation) by voice. He has **7 native voice providers already integrated** in the Android app but no way to tell which performs best — which one calls the right tool, with the right arguments, fast enough to feel real-time.

Rather than instrument the live Android app for benchmarking (slow iteration, hard to automate), we build a **separate web harness** that mimics the SciSymbioLens control surface as a Vite/React app. Voice drives the widgets via tool calls; the harness scrapes events and scores them. This isolates the question "which agent is best at tool-calling" from the noisier question "is the Android app working."

### Current state of the voice landscape (discovered 2026-05-18)
Inventory of voice providers in `~/windsurf_repos/SciSymbioLens-Android/`:

| Provider | Model(s) | Transport | Manager |
|---|---|---|---|
| Gemini Live | `2.5-flash-native-audio` | Google SDK (WS+HTTP) | `VoiceAgentManager` |
| OpenAI Realtime | `gpt-realtime-2` (fallback `gpt-realtime`) | WS `wss://api.openai.com/v1/realtime` | `GenericVoiceConversationManager` |
| xAI Grok | `grok-voice-think-fast-1.0` | WS `wss://api.x.ai/v1/realtime` | Generic |
| ElevenLabs Conv-AI | agent-based | WS `wss://api.elevenlabs.io/v1/convai/conversation` | Generic |
| Hume EVI | EVI core | WS `wss://api.hume.ai/v0/evi/chat` | Generic |
| Deepgram Aura | aura agent | WS `wss://agent.deepgram.com/agent` | Generic |
| Nova 2 Sonic | `amazon.nova-2-sonic-v1:0` | AWS bidirectional stream | Generic |

Plus a separate **ElevenLabs Scribe** STT overlay (used in the Android app to replace Gemini Live's native transcript with a more accurate one).

Existing in-app tool surface (`ToolCallHandler.kt`): `ask_clawdbot`, `query_knowledge_base`, `session_status`, `agent_nexus_eln`, `get_page_context`, `set_caption`. These can inform but do **not** constrain the benchmark's tool surface — voice-bench uses its own dummy tools flavored like SciSymbioLens settings.

### Decisions already made with the user
- **Mac-only harness, no Android involvement** for v1. Android port is a later question.
- **Test target = a React+Vite website** that mimics SciSymbioLens controls.
- **Tools are dummy stubs** that record `(name, args, ts)` to an event log — no real side effects.
- **Scoring**: (a) tool name match — boolean; (b) arg fuzzy match — 0..1; (c) TTFS latency — ms. No spoken-response grading in v1.
- **Optimization knob**: system prompt + tool descriptions per agent. Test set fixed. Temperature fixed. Latency is measured, not optimized.
- **Progressive tool counts**: same prompts run with 5, 10, 15, 20, 30 tools loaded to measure correctness decay.
- **Prompts pre-rendered**: ~50 prompts × 2–3 TTS voices = ~150 .wav files checked in.
- **Two modes**: smoke (~5 prompts × 1 voice) for iteration; full (all prompts × all voices × all tiers) for validation.
- **No hard API cost cap**, but log spend per run.
- **Single-turn only in v1**; multi-turn deferred.

## Acceptance Criteria

### V1 (foundation + thin end-to-end slice)
- [ ] `voice-bench/` project bootstrapped: Python harness + React+Vite mock-UI + docs structure.
- [ ] One end-to-end automated test passes: pre-rendered prompt audio file → Gemini Live → harness waits for `setupComplete` → dummy tool `toggle_flash(on=true)` fires → harness sends synthetic `toolResponse` back → harness records timestamps and records event → scoring shows (a=true, b=1.0, ttfs_ms=<n>, ttf_tool_ms=<n>).
- [ ] CLI present: `voice-bench run --agent <name> --tools <count> --mode smoke|full`.
- [ ] Results emitted as both JSONL (one line per turn, including raw provider events for debugging) and a summary CSV (one row per agent×tier).
- [ ] Per-turn deadlines enforced: connect, setup, first-tool, first-audio, quiet-after-last-event, teardown — each turn either resolves or fails with a typed reason; benchmark never hangs.
- [ ] **Provider capability probe** runs before benchmark: each provider connects, receives setup-complete, and disconnects cleanly. Probe-fail short-circuits the run for that provider with a clear log.
- [ ] No secrets committed. `.env.example` documents required keys; `.gitignore` excludes real `.env`.
- [ ] README explains: how to install, run smoke, interpret results.

### V1.1 (matrix scaled out — separate follow-up stories)
- [ ] All 7 native providers run.
- [ ] At least 3 pipelined LLM+TTS combinations run.
- [ ] All 5 tool-count tiers (5/10/15/20/30) run.
- [ ] HTML report shows accuracy-vs-latency scatter with Pareto frontier highlighted.
- [ ] `voice-bench optimize` runs the closed-loop prompt revision flow autonomously.

## Implementation Plan

### Epic 1 — Project scaffolding & dummy tool registry
**Story 1.1** — Init the repo: `pyproject.toml` (uv-managed), `src/voice_bench/`, `mock_ui/` (React+Vite), `docs/`, `prompts/` (audio fixtures), `results/`. Add `.gitignore`, `.env.example`, README.
**Story 1.2** — Define dummy tool registry: dataclass `DummyTool(name, description, schema, tier)`. Seed with **30 tools** organized in tiers of 5:
- **tier-1 (5 tools): pure boolean toggles** — `toggle_flash`, `toggle_grid_overlay`, `toggle_macro_mode`, `toggle_stabilization`, `toggle_voice_captions`. Single boolean arg. Easiest possible function-call shape.
- **tier-2 (5): single enum** — `switch_camera(front|back|macro)`, `set_resolution(low|med|high|max)`, etc.
- **tier-3 (5): numeric + enum** — `set_exposure(float -2..2)`, `set_zoom(float 1..10)`, `set_focus_distance(float)`, plus combos.
- **tier-4 (5): multi-arg** — `start_recording(label, max_duration_s, codec)`, `start_documentation(label, project_id, tags[])`.
- **tier-5 (5): nested object** — `configure_capture({camera, exposure, flash, resolution, label})`, etc.
Each tool's `__call__` just records `(name, args, ts_called)` to an event log. The first-slice acceptance target (`toggle_flash`) is a tier-1 tool so it is included even when `--tools 5`.
**Story 1.3** — Tool-tier loader: `load_tools(count: int) -> list[DummyTool]` returns the first N tools deterministically. This is how progressive tool counts get fed to agents.

### Epic 2 — Mock UI (the "SciSymbioLens-like" website)
**Story 2.1** — React+Vite app with widgets: camera selector (front/back/macro), exposure slider, flash toggle, zoom slider, resolution dropdown, record button, documentation start button, label input. Each widget reads its state from a Zustand store.
**Story 2.2** — Tool-event WebSocket bridge: harness exposes a WS endpoint; UI subscribes; every `tool_called` event updates the corresponding widget so the human can *see* tool firing. (Helpful for sanity-checking; not required for scoring.)
**Story 2.3** — A "test run" panel showing live progress: which agent, which prompt, last tool call, last score. Read-only for v1.

### Epic 3 — Tool-call event sink (V1-critical, NOT scale-out)
**Story 3.1** — Define `TurnTimeline` with split timestamps so latency can be decomposed:
```
TurnTimeline(
  turn_id, agent, prompt_id,
  ts_connect_start, ts_setup_complete,
  ts_input_audio_start, ts_input_audio_end,
  ts_first_event_received,      # any provider event after audio end
  ts_first_tool_call_emitted,   # may precede first audio
  ts_tool_response_sent,        # harness's synthetic ack
  ts_first_output_audio,        # canonical "TTFS"
  ts_turn_complete,
)
ToolCallEvent(turn_id, tool_name, args, call_id, ts_called)  # one per call
RawProviderEvent(turn_id, ts, kind, payload_json)             # full audit trail
```
All three persisted to JSONL per run.

**Story 3.2 (V1)** — Harness exposes the dummy tools to agents via each agent's native function-calling format. Build a **tool-spec adapter** per agent SDK. **Important correction from Codex review:** six of seven SciSymbioLens-Android voice providers currently send tools = `[]` (Nova Sonic explicitly, OpenAI Realtime not at all in `session.update`). Only Gemini Live's path has working tool plumbing. So this epic must implement tool support *from scratch* per provider — it is NOT a port. Each adapter translates the canonical `DummyTool` into the provider-specific function schema, configures it during the connect handshake, parses provider-native tool-call events, and routes them to the harness.

**Story 3.3 (V1)** — Synthetic tool-response handler: when an agent calls a tool, the harness's tool-handler emits a `ToolCallEvent`, records the call_id, executes the dummy tool, AND returns a synthetic success result to the agent in the provider's required tool-response format so the conversation can continue. Dedupe by `call_id` (Gemini emits duplicates; learned from `VoiceAgentManager.kt:858`).

### Epic 4 — Native voice agent adapters (one provider per story)

**Story 4.0 (V1 prerequisite)** — Provider capability probe. For each provider, a minimal `probe()` that:
1. Connects with default config and the agent's tool list.
2. Waits for `setupComplete` (or provider-equivalent — Gemini `setupComplete`, OpenAI `session.created`, etc.).
3. Disconnects cleanly.
4. Records: model ID actually used, configured sample rate, observed event vocabulary, whether tool config was acknowledged.
This catches stale model IDs (Codex flagged that the Android repo uses `gemini-3.1-flash-live-preview`, not `2.5-flash-native-audio`) and missing tool support BEFORE we spend a benchmark run on them.

Each adapter implements `NativeVoiceAdapter` with the same interface:
```python
class NativeVoiceAdapter(Protocol):
    async def run_turn(self, audio_wav_path: Path, tools: list[DummyTool]) -> TurnResult
```
Where `TurnResult = (timeline: TurnTimeline, tool_calls: list[ToolCallEvent], raw_events: list[RawProviderEvent], transcripts: dict, terminal_reason: TerminalReason)`.

**Per-turn deadlines** (every adapter must honor, all configurable, defaults below):
- `connect_timeout`: 10s
- `setup_timeout`: 5s (from connect to `setupComplete`)
- `first_tool_timeout`: 15s (from end-of-input-audio)
- `first_audio_timeout`: 20s (from end-of-input-audio)
- `quiet_timeout`: 3s (no events received → assume turn complete)
- `teardown_timeout`: 5s

`TerminalReason` enum: `TURN_COMPLETE | TIMEOUT_<phase> | PROVIDER_ERROR | NO_TOOL_CALLED | NO_AUDIO_RECEIVED | DISCONNECTED`.

**Story 4.1** — `GeminiLiveAdapter` (first slice — V1). Setup ordering enforced per Android reference (`VoiceAgentManager.kt:488-561`): load tools → build system prompt → connect → wait for `setupComplete` → only then stream audio. Use `gemini-3.1-flash-live-preview` (per current Android code), verify via probe.
**Story 4.2** — `OpenAIRealtimeAdapter`
**Story 4.3** — `XaiGrokAdapter`
**Story 4.4** — `ElevenLabsConvAIAdapter`
**Story 4.5** — `HumeEviAdapter`
**Story 4.6** — `DeepgramAuraAdapter`
**Story 4.7** — `NovaSonicAdapter`

### Epic 5 — Pipelined adapters (LLM + TTS, with STT preprocessing)
**Story 5.1** — `PipelineAdapter` core: takes (STT engine, LLM, TTS engine) as a config; runs STT on input WAV, sends transcript to LLM with tools, streams response, optionally sends to TTS. TTFS = STT-end → LLM first token.
**Story 5.2** — STT options: Whisper (local + cloud), Deepgram, Gemini STT, ElevenLabs Scribe.
**Story 5.3** — LLM brains: Claude Opus 4.7, GPT-5 / 4o, Gemini 2.5 Pro, Gemini 2.5 Flash.
**Story 5.4** — TTS leg (optional for scoring tool-calls, useful for end-to-end latency): ElevenLabs, OpenAI TTS (gpt-4o-mini-tts), Gemini TTS, macOS `say`, Kokoro (local).

### Epic 6 — Prompt generation & audio fixtures
**Story 6.1** — Write 50 canonical prompt texts mapped to expected tool calls. Schema: `Prompt(text, expected_tool, expected_args_pattern, tier_min, split)`. Cover the 5 tool tiers. **Train/val/holdout split (50 = 30/10/10)** to prevent the optimization loop from overfitting:
- **Train (30 prompts)** — visible to `voice-bench optimize`, used for prompt-revision suggestions.
- **Validation (10)** — used as the early-stopping signal during optimization; loop stops when val accuracy plateaus.
- **Holdout (10)** — never seen by the optimizer; only used by `voice-bench run --mode full` for final reporting.
The split membership is encoded in the manifest and frozen; reshuffling requires a manifest-version bump.
**Story 6.2** — Pre-render audio. **Canonical fixture format**: mono PCM16, 16 kHz, normalized to -20 dBFS RMS, trailing 500 ms of silence (lets providers without explicit end-of-utterance markers detect turn boundary via their internal VAD). Stored as `prompts/audio/<voice>/<prompt_id>.wav` at canonical 16 kHz. Per-provider **resampling** happens at adapter level (OpenAI Realtime expects 24 kHz, generic providers vary — see `GenericVoiceConversationManager.kt:223`). Default voices: ElevenLabs (default voice) + OpenAI TTS (alloy) + macOS `say` (system default, as cheap reference).
**Story 6.3** — Manifest (`prompts/manifest.json`) listing every prompt with: text, expected tool, expected args pattern, tier_min, audio paths per voice, audio metadata (codec, sample_rate, duration_ms, rms_dbfs, tts_source). Also include a **negative-prompt subset** (~10 prompts where NO tool call is expected, e.g. "What's the weather like?") to catch over-eager tool calling.
**Story 6.4** — Manifest also captures **catalog versions**: prompt-set version, tool-catalog version, system-prompt version per agent. Results JSONL embeds these so reruns are diffable.

### Epic 7 — Scoring engine
**Story 7.1** — `score_turn(turn: TurnResult, expected: Prompt) -> Score(...)`. Rich score schema accommodating edge cases:
```
Score(
  tool_name_match: bool,         # was the *first* tool call the expected one?
  arg_score: float,              # fuzzy match 0..1 for that call
  ttfs_ms: int | None,           # first output audio (None if no audio emitted)
  ttf_tool_ms: int | None,       # first tool call (the meaningful "agent acted" signal)
  extra_calls: int,              # other tool calls (correct or not) after the first
  duplicate_calls: int,          # same (name, args) called twice
  malformed_calls: int,          # tool call with invalid args / unparseable JSON
  wrong_tool_first: bool,        # wrong tool fired before the right one
  no_call_made: bool,            # zero tool calls — total miss
  negative_prompt_violation: bool, # tool was called when none expected
)
```
**Convention**: for the headline accuracy %, a turn counts as a pass iff `tool_name_match=True AND arg_score >= 0.8 AND not malformed_calls`. Other fields surface in detailed reports.

**Story 7.2** — Hybrid arg matcher with two tiers:
1. **Primary path (deterministic, cheap)**: YAML-based fuzzy match — case differences, synonyms (`"front camera" ≈ "front" ≈ "selfie"`), numeric tolerance (`0.8 ≈ 0.79`, within 5% relative), boolean coercion (`"on" ≈ true`). Synonyms in `scoring/synonyms.yaml`. Returns `(matched: bool, score: float, confident: bool)`.
2. **Fallback path (LLM-as-judge)**: when YAML match is `confident=false` (e.g. agent emitted a paraphrase not in synonyms), call a cheap judge (Haiku or GPT-4o-mini) with the expected pattern + actual args + tool description, asking "does this satisfy the intent?". Cached by `(tool_name, expected_args_json, actual_args_json)` to keep cost down. Judge results logged for human review.
**Rationale**: YAML alone is brittle as the tool catalog grows (Gemini architectural critique #2); pure LLM-judge is expensive and non-deterministic. The hybrid keeps cheap cases cheap and only spends on hard cases.
**Story 7.3** — Aggregation: per agent × tool-tier, output accuracy %, median + p95 ttfs_ms, median + p95 ttf_tool_ms, args-mean-score, extra/duplicate/malformed rates, negative-prompt-violation rate. Emit `results/<run-id>.csv` (summary) and `results/<run-id>.jsonl` (per-turn detail).

### Epic 8 — Runner & CLI
**Story 8.1** — `voice-bench run` command with flags: `--agent`, `--tools 5|10|15|20|30|all`, `--mode smoke|full`, `--voices`, `--out`.
**Story 8.2** — Parallel agent execution (asyncio gather) within a single run, with rate-limit handling per provider.
**Story 8.3** — Cost logger: per-provider token/cost accounting, summed at the end of each run.

### Epic 9 — Self-improvement loop (v1.1)

**Design borrowed from [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch).** That project autonomously optimizes a single file (`train.py`) against a single metric (`val_bpb`) over a fixed budget per experiment, using git as the audit trail. The patterns transfer cleanly to optimizing voice-bench system prompts against tool-call accuracy — see "Reviewer Feedback / Reference: karpathy/autoresearch (round 3)" below for the full mapping.

**Story 9.1 — `voice-bench optimize --agent <name>`**: closed-loop optimizer with these hardened rules:

- **Single file the optimizer modifies**: `prompts/system/<agent>.md`. Tool descriptions, scoring rules, audio fixtures, and harness code are read-only to the optimizer. Keeps diffs reviewable, like autoresearch's "one file, one diff" rule.
- **Fixed budget per round**: each round runs the full 30-prompt **train** set × N fixed voices (default 1 voice for speed during optimize; full voice matrix only in `voice-bench run --mode full`). Makes rounds directly comparable.
- **Git as audit trail**: each optimization run lives on its own branch `voice-bench-opt/<agent>/<run-id>` (analogous to autoresearch's `autoresearch/<tag>`). Every prompt revision is a git commit on that branch. `keep` → branch advances; `discard` → `git reset --hard HEAD~1`. The user gets full prompt version history for free, and `git log` becomes a readable research diary.
- **Composite score with conciseness penalty**: `score = val_accuracy - alpha * (prompt_chars / 1000)`, default `alpha=0.005`. Rewards shorter prompts: a 1000-char addition costs 0.005 accuracy points. Tunable via `--alpha 0.0` (disable) up to `--alpha 0.05` (aggressive). Use `score` for keep/discard but log `val_accuracy`, `prompt_chars`, and `ttf_tool_p50_ms` separately so trade-offs are visible.
- **Train/val/holdout walls strictly enforced**:
  - Optimizer LLM sees only **train** results and train-failure transcripts. Never sees val or holdout prompt texts.
  - **Validation** (10 prompts) is the keep/discard gate. Score evaluated here.
  - **Holdout** (10 prompts) is reserved — only consumed by `voice-bench run --mode full` for final reporting.
- **Results log** (analogous to autoresearch's `results.tsv`) at `optimizations/<agent>.tsv`:
  ```
  commit	round	val_accuracy	val_score	prompt_chars	ttf_tool_p50_ms	status	hypothesis
  ```
  Status ∈ `{keep, discard, crash}`. Logged even when discarded so patterns of "what doesn't work" are preserved.
- **Plateau detection**: stop after `--patience N` rounds (default 10) without `val_score` improving by `≥ 0.01`.
- **Configurable iteration cap**: `--max-rounds N` (default 50) and `--max-wallclock H` (default 8h). Whichever fires first.
- **Crash handling**: a revision is `crash` if the agent fails to connect, malformed-call rate >50%, or no-call rate >70%. Revert, log hypothesis, continue.
- **NEVER STOP directive in autonomous mode** (`--mode autonomous`): no pausing for human input. Run until cap, plateau, or interrupt. (Verbatim philosophy lifted from autoresearch's `program.md` — the human is asleep or away.)
- **Simplicity criterion embedded in optimizer's prompt** (paraphrased from autoresearch): *"All else being equal, simpler is better. A small accuracy gain that doubles prompt length is not worth it. Conversely, removing instructions and getting equal or better accuracy is a great outcome — that's a simplification win."* Nudges the optimizer to actively try *deleting* instructions, not just adding them.

**Story 9.2 — Optimizer's own prompt** lives at `prompts/optimizer.md`. Editable by the human (analogous to autoresearch's `program.md` being the meta-knob). Defines: how the optimizer reads failure transcripts, what kinds of revisions to propose, the simplicity criterion, the never-stop directive. Versioning this file is itself a research dimension; v2 can A/B compare optimizer prompts.

**Story 9.3 — Failure clustering for revision proposals**: before each round, group last-round failures by tool tier × failure mode (`wrong_tool_first`, `malformed_calls`, `no_call_made`, `negative_prompt_violation`). Optimizer receives the top-3 clusters with anonymized sample transcripts (NOT raw prompt texts — to slow overfitting). Optimizer proposes ONE focused revision per round.

**Story 9.4 — Holdout drift detector**: every `--holdout-every N` rounds (default 10), run holdout silently and log its score. If train_accuracy keeps climbing but holdout stays flat or drops, emit a warning — the run is overfitting. Holdout is logged but does NOT feed back into keep/discard (otherwise it becomes train).

**Story 9.5 — Commit-message format** for the research diary:
```
opt(<agent>): r<round> <hypothesis-summary>

val_accuracy: <before> → <after>
prompt_chars:  <before> → <after>
val_score:     <before> → <after>
status: keep|discard|crash
```

### Epic 10 — Reporting & visualization (v1.1)
**Story 10.1** — HTML report from a run: accuracy × latency scatter, per-provider drill-down, failure samples.
**Story 10.2** — Diff report between two runs (e.g. before vs after optimization).

### Recommended FIRST SLICE
**Stories included:** 1.1, 1.2 (just tier-1 tools), 2.1 (minimal — UI need not be wired yet), **3.1 + 3.2 (Gemini-only) + 3.3** (tool plumbing is the architecture proof), **4.0 (probe) + 4.1**, 6.1 (5 prompts only), 6.2 (one voice — `say`), 7.1, 8.1.

**Minimum end-to-end loop**:
```
voice-bench probe --agent gemini-live      # validates connect + setupComplete
voice-bench run --agent gemini-live --tools 5 --mode smoke
```
Produces a JSONL where for ≥1 prompt, the dummy `toggle_flash(on=true)` tool was correctly called, the harness sent a synthetic tool-response, the timeline shows split timestamps, and the scoring CSV reports pass=true with `ttf_tool_ms` measured. This validates the architecture (audio injection → provider handshake → tool plumbing → event sink → scoring → timeouts) before committing to the 7-provider × 5-tier × 3-voice matrix.

**Why this slice and no other**: the riskiest assumption is that we can build *seven* provider-specific tool adapters. Proving the architecture works end-to-end for *one* (Gemini, the only one we know has working tool plumbing in production code) eliminates the highest-uncertainty path before scaling out.

## Risks & Open Questions

### Risks
1. **Tool-spec format divergence**: each provider's function-calling schema is subtly different (OpenAI vs Gemini vs AWS Nova Sonic vs ElevenLabs conversational agents). Building 7 adapters may take longer than expected. *Mitigation*: build the adapter-protocol carefully in Epic 3 before writing all 7.
2. **TTFS measurement consistency across providers**: "first audio chunk" means different things across SDKs (some emit partial tokens before audio; some don't expose token-level events). *Mitigation*: define TTFS strictly as "first byte of audio response received by the harness," and document deviations per adapter.
3. **WebSocket vs SDK heterogeneity**: AWS Nova Sonic uses AWS SDK bidirectional streams; others are raw WS. Test scaffolding must support both.
4. **ElevenLabs Conversational AI is agent-based**: tools may need to be configured on the ElevenLabs side, not just passed in the WS handshake. May require platform-side setup that the harness can't fully automate.
5. **Audio-injection authenticity**: streaming a pre-rendered WAV through a WebSocket bypasses any client-side voice activity detection. Some providers (Hume EVI, Deepgram) rely on VAD to detect end-of-turn — may need to manually send "end of utterance" markers.
6. **Cost surprise**: even without a cap, running 7 providers × 5 tiers × 3 voices × 50 prompts is potentially ~5,250 turns. At ~$0.005–0.05 per turn, full runs could reach $25–$250. Log spend, warn before full runs.
7. **Stale model IDs / API drift**: the brief listed Gemini Live as `2.5-flash-native-audio`, but the current SciSymbioLens-Android code uses `gemini-3.1-flash-live-preview` (per `GeminiLiveWebSocket.kt:45`). Every provider model ID must be verified via `voice-bench probe` before being treated as canonical. The probe is part of V1 acceptance.
8. **Tool support is unimplemented in 6 of 7 native providers** (Codex finding): only Gemini Live has working tool plumbing in the Android repo. OpenAI's `session.update` sends no tools today; Nova Sonic explicitly sends `tools: []`. Epic 3.2 must build per-provider tool support *from scratch*, not port it. Adjust schedule expectations accordingly — each provider adapter is a multi-day story, not a "wire it up" task.

### Open questions (decide during build, before relevant story)
1. **Harness language**: Python (best LLM SDK ecosystem, weakest browser automation) or Node (best for the React side and Playwright). *Recommendation: Python harness + React mock UI as separate processes communicating via WebSocket. No browser automation needed — agent tool calls go directly to harness HTTP endpoint, UI is just an observer.*
2. **Where do tool calls actually go?**: Direct from agent → harness (function-callback in SDK) is simplest. The UI is *only* an observer. The "website mimics SciSymbioLens" is mostly for human sanity-checking, not for scoring. *Recommendation: confirm with Martin that UI is observer-only, not required for tool-call path.*
3. **Pipelined STT default**: Whisper local (free, slow) vs Gemini STT (fast, paid) vs Deepgram (very fast). *Recommendation: Whisper local for v1 to keep cost down; benchmark others separately.*
4. **Where does ElevenLabs agent config live?**: ElevenLabs Conv-AI requires creating an "agent" in their dashboard with tools defined there. Automating this might require their API. *Open — investigate during Story 4.4.*
5. **TTS-voice selection for prompt generation**: should the prompt-generation TTS voice match the agent's expected user voice (probably not relevant), or should we test with deliberately challenging voices (accents, whispering)? *Recommendation: 3 voices in v1 — one clear (ElevenLabs default), one accented (ElevenLabs British), one cheap (`say`), to surface robustness differences.*

## Out of Scope (explicitly)

- Real device testing — no Android, no iOS in this project.
- Real side effects — every tool is a dummy stub.
- Spoken-response quality scoring — too subjective for v1.
- Multi-turn dialogues — single-turn only.
- Production telemetry / monitoring — this is a benchmarking lab, not production.
- The mock UI does **not** need to be visually polished — it's a control panel for sanity-checking, not a product.

## Known Limitations & v2 Roadmap

Gemini's architectural review flagged five systemic blind spots in the v1 design. Each is acknowledged as a *deliberate v1 trade-off*; the path to address them is here so they aren't forgotten.

1. **Clean-room validity gap** (Gemini concern #1). Building 7 Python adapters proves *agent capability*, not *Android-specific performance*. SDK threading, `AudioRecord` buffering, and mobile memory pressure aren't measured. → **v2: Validation pass.** Take v1's top 2–3 agents back to the SciSymbioLens-Android codebase, re-test with the same prompts via instrumentation, and check the v1 ranking holds. Diverging results = a finding (and a story).
2. **Synthetic environment** (Gemini concern #2). Pristine TTS-rendered audio over fast wifi ≠ field reality (4G/5G jitter, lab background noise). v1 measures agents at their best. → **v2: Robustness stories.** Network throttling (`tc qdisc`), additive noise injection, and competing-audio mixing on the audio fixtures.
3. **Optimizer overfitting** (Gemini concern #3) — mitigated in v1 via the 30/10/10 split (Story 6.1). No additional v2 work needed unless the holdout reveals significant train-set overfitting in practice.
4. **Single-turn only** (Gemini concern #4). Tier-3 tools may work on turn 1 but degrade as context bloats. → **v2: Multi-turn dialogue suite.** ~20 conversations of 3–6 turns each. Score continuity (does the agent remember state?) and degradation (does TTFS rise turn-by-turn?).
5. **Synchronous lifecycle** (Gemini concern #5). v1 sends WAV → waits for tool → ends turn. Real voice has barge-in, interruption, overlapping commands. → **v2: Async behavior suite.** Inject a "stop / cancel that" prompt mid-response. Score whether the agent honors barge-in.

These are explicitly out-of-scope for v1 but are not "deferred ideas" — they are the v2 backlog.

## Considered Alternatives (rejected for v1)

1. **On-device benchmark inside SciSymbioLens-Android** (Gemini alternative #1). Pros: reuses production code; measures true mobile latency. Cons: slow iteration loop (Gradle build per change), hard to automate (UI Automator is flaky on voice timing), Android-only (doesn't help iOS, doesn't let us add new providers without an APK release), and the user explicitly chose Mac-first for iteration speed. *Verdict: rejected for v1; revisit as v2 validation harness.*
2. **Intercepting proxy keeping Android in the loop** (Gemini alternative #2). Pros: tests real Android client code. Cons: AWS Nova Sonic uses the AWS SDK (not raw WS) — no traffic to intercept. Requires a patched Android build pointed at the proxy. Fragile to provider SDK upgrades. *Verdict: rejected; covers fewer providers than the clean-room approach.*
3. **Pure LLM-as-judge scoring** (Gemini alternative #3) — *partially adopted* as the fallback path in Story 7.2. Pure LLM-only judging is non-deterministic and expensive at scale; YAML primary + LLM fallback balances cost and robustness.
4. **Field-recorded audio fixtures** (Gemini alternative #4). Pros: tests against real domain jargon and acoustic noise. Cons: requires Martin to do data collection trips before the harness exists; we want to ship v1 first. *Verdict: v2 work — once we have winning agents, augment the corpus with real lab recordings.*

## Reviewer Feedback

### Codex (round 1) — Implementation lens
Verbatim:

> **Findings**
>
> 1. **First slice omits the actual Gemini tool plumbing.** The recommended slice includes Story 3.1 but not 3.2 or 3.3, yet the acceptance test requires Gemini to receive `set_exposure`, emit a function call, and get a tool response. Android's working path depends on `toolDefinitions` being set before connect, parsing `toolCall.functionCalls`, and sending `toolResponse` back: `GeminiLiveWebSocket.kt:155, :418, :454`. So Story 3.2/3.3 are V1 blockers, not later architecture work.
>
> 2. **The plan assumes the Android provider inventory already proves tool support across seven native providers. It does not.** `VoiceProviderService` only exposes `connect`, `sendAudio`, `disconnect`, and events; there is no provider-neutral tool-call event or tool-response API: `VoiceProvider.kt:15`. OpenAI's `session.update` currently sends audio config, transcription, instructions, and reasoning, but no tools: `OpenAIRealtimeProvider.kt:145`. Nova explicitly sends an empty `tools` array: `NovaSonicProvider.kt:393`. Treating "all 7 native providers run" as scale-out is a gap; most adapters need new tool-call implementation, not just porting.
>
> 3. **Gemini setup ordering is underspecified.** Android configures tools before `connect`, builds the system prompt after tool availability is known, then connects: `VoiceAgentManager.kt:488, :551, :561`. The plan does not say the harness must wait for `setupComplete` before streaming the WAV; Android has an explicit setup signal path: `GeminiLiveWebSocket.kt:377`. Without this, the first audio fixture can race session setup and produce flaky "no tool call" results.
>
> 4. **Audio fixture handling is too shallow for a file-driven harness.** Android Gemini sends 16 kHz PCM16 with `mimeType:"audio/pcm;rate=16000"`: `GeminiLiveWebSocket.kt:194`. Generic providers resample because OpenAI expects 24 kHz input while others use 16 kHz: `GenericVoiceConversationManager.kt:223`. The plan says "pre-rendered .wav" but does not require canonical mono PCM16, sample-rate conversion per provider, chunk size, pacing, RMS normalization, silence trimming, or trailing silence/end-of-utterance handling.
>
> 5. **Turn completion and timeouts are missing from `run_turn`.** Android has production defenses for missed or reordered end-of-turn signals: Gemini has `turnComplete`, `interrupted`, `generationComplete`, and a force-end path; generic providers soft-close stale turns when no completion arrives: `GeminiLiveWebSocket.kt:350`, `GenericVoiceConversationManager.kt:168`. The plan needs explicit per-turn deadlines: connect timeout, setup timeout, first-tool timeout, first-audio timeout, no-more-events quiet timeout, and teardown timeout.
>
> 6. **TTFS is not well-defined for tool-call tasks.** Story 3.1 puts `ts_first_audio` on `ToolCallEvent`, and risk 2 defines TTFS as first response audio, but the target prompt may produce a tool call before any audio. If Gemini calls `set_exposure` then waits for `toolResponse` before speaking, TTFS measures post-tool speech latency, not "agent understood and acted" latency. The plan should separately record `input_audio_start`, `input_audio_end`, `ts_tool_called`, `ts_tool_response_sent`, and `ts_first_output_audio`.
>
> 7. **The scoring plan does not define behavior for extra, duplicate, or concurrent tool calls.** Android had to dedupe Gemini calls by call ID: `VoiceAgentManager.kt:858`. For benchmarking, "correct tool fired" is ambiguous if the model first calls a wrong tool, calls the right tool twice, calls two tools in one message, omits required args then retries, or emits a malformed nested object.
>
> 8. **The first-slice dummy tool count conflicts with the acceptance example.** The plan says tier 1 has toggles and the mock UI includes an exposure slider, but the acceptance test expects `set_exposure(0.8)` under `--tools 5`. If exposure is numeric, it likely belongs in tier 3 by the plan's own tier definitions. Either `set_exposure` must be in the first five tools or the acceptance command cannot pass.
>
> 9. **Model names and protocol assumptions need verification.** The story lists Gemini Live as `2.5-flash-native-audio`, but Android's working code says the previous `gemini-2.5-flash` is not a Live API model and uses `gemini-3.1-flash-live-preview`: `GeminiLiveWebSocket.kt:45`. The plan should include a provider capability probe or at least a single "connect + setupComplete" test before building scoring around stale model IDs.
>
> **Critical issues:**
> - Add Gemini tool-spec adapter and synthetic tool-response handling to V1 first slice; Story 3.2/3.3 are required for the acceptance test.
> - Specify setup ordering: load tools, build prompt, connect, wait for setup complete, then stream audio.
> - Define audio normalization/resampling/chunking/pacing/end-of-utterance rules per provider.
> - Add run-turn timeouts and terminal conditions for missing `turnComplete`, missing tool call, no audio, provider error, and teardown.
> - Split TTFS from tool-call latency; record distinct timestamps for input start/end, tool call, tool response, and first output audio.
> - Do not assume the six non-Gemini Android providers already support tool calling; their current interface and several implementations do not.
> - Resolve the `set_exposure(0.8)` vs tier-1/`--tools 5` mismatch.
> - Define scoring for extra, duplicate, malformed, multiple, or retried tool calls.
>
> **Nice-to-have:**
> - Add raw provider event JSONL alongside normalized result JSONL for debugging flaky runs.
> - Add a provider capability smoke test before benchmark runs.
> - Track audio fixture metadata in the manifest: codec, sample rate, duration, RMS/loudness, TTS source.
> - Include deterministic run IDs, seed/config snapshots, prompt version, tool catalog version, and provider model version in results.
> - Add a minimal "no tool expected" negative prompt set to catch over-eager tool calling.
> - Keep UI observer-only, but make the WS bridge subscribe to the same event sink used for scoring so visual state cannot diverge from benchmark records.

### What changed in response
- **All 8 critical issues addressed** in the plan body (not just risks):
  - Stories 3.2 + 3.3 moved into V1 first slice; first slice now includes 4.0 (probe).
  - Acceptance criteria explicitly require `setupComplete` wait + per-turn deadlines.
  - First-slice example changed from `set_exposure(0.8)` (numeric, tier-3) to `toggle_flash(on=true)` (boolean, tier-1).
  - Tier-1 definition now lists 5 concrete boolean toggles including `toggle_flash`.
  - `TurnTimeline` defined with 8 split timestamps replacing the single `ttfs_ms`.
  - Per-turn timeouts spec'd with `TerminalReason` enum.
  - `Score` schema expanded to capture extra/duplicate/malformed/wrong-first/no-call/negative-violation.
  - New Story 4.0 = provider capability probe (`voice-bench probe`).
  - New Risk #7 (stale model IDs) and Risk #8 (tool support not actually implemented in 6 of 7 Android providers).
  - Model ID updated from `2.5-flash-native-audio` → `gemini-3.1-flash-live-preview` (per current Android code).
- **All 6 nice-to-haves adopted**: raw event JSONL (Story 3.1), capability probe (Story 4.0), audio metadata in manifest (Story 6.3), catalog versions in manifest (Story 6.4), negative prompts (Story 6.3), UI bridge subscribes to scoring event sink (Story 2.2 already covered this).

### Gemini (round 2) — Architectural lens
Verbatim:

> **Architectural concerns:**
>
> 1. **Divergent Implementation vs. Production Reality:** Epic 4 proposes rebuilding seven provider-specific adapters from scratch in Python. This creates a "cleanroom" harness that is completely divorced from the Android production code. If an agent performs well in the Python harness, it only proves that the *Python integration* is fast and correct. It entirely ignores the Android-specific threading, audio buffering (`AudioRecord` latency), memory constraints, and SDK nuances present in `VoiceAgentManager` and `GenericVoiceConversationManager`. The benchmark risks measuring the quality of the new Python adapters rather than predicting actual mobile performance.
>
> 2. **Synthetic Environment Blind Spot:** The architecture assumes testing with pristine, pre-rendered TTS WAV files over a Mac's fast broadband connection. This bypasses the defining constraints of the target SciSymbioLens environment: mobile network transport jitter (4G/5G), packet loss, and real-world acoustic conditions (lab equipment noise, wind, overlapping speech). TTFS and tool accuracy measured in this ideal environment will not accurately reflect field performance.
>
> 3. **Overfitting in the Self-Improvement Loop:** Epic 9 introduces an autonomous closed-loop optimization cycle. Optimizing system prompts against a fixed catalog of 50 synthetic prompts without a strict train/validation/test split will inevitably cause the LLM to overfit. The resulting prompts will become hyper-optimized for the specific phrasing and TTS quirks of the test set, failing to generalize to diverse, real-world user utterances.
>
> 4. **State Accumulation and Context Bloat:** The plan explicitly scopes out multi-turn dialogues (Single-turn only). However, real voice interaction in a field app is persistent. An agent that flawlessly executes a tier-3 tool on turn 1 might hallucinate, misinterpret arguments, or degrade in TTFS when its context window is loaded with 15 previous tool events and user queries. A strictly single-turn benchmark cannot validate systemic reliability over a full lab session.
>
> 5. **Synchronous Turn Lifecycle:** The `TurnTimeline` and adapter architecture enforce a rigid, synchronous lifecycle (connect → setup → audio → tool call → response). Real-world voice agents must handle asynchronous events: users interrupting the agent, issuing overlapping commands, or speaking while a tool is still executing. The harness architecture completely shields the agents from these concurrency edge cases.
>
> **Alternatives worth considering:**
>
> 1. **On-Device Test Harness (Simpler/More Valid Approach):** Instead of rewriting seven complex integration layers in Python, build an automated "benchmark mode" directly into the Android app (using UI Automator or Espresso). The device can inject pre-recorded audio directly into the microphone buffer and emit tool-call events to a lightweight local server. This reuses 100% of the production code and measures the true end-to-end mobile latency.
>
> 2. **Proxy/Interceptor Architecture:** If a separate web harness is strictly required, keep the Android app in the loop. Route the Android app's WebSocket traffic through the Python harness acting as an intercepting proxy. The proxy injects the synthetic audio payloads and sniffs the resulting tool-call packets for scoring, avoiding the need to write Python adapters while maintaining production validity.
>
> 3. **LLM-as-a-Judge for Tool Scoring:** Story 7.2 proposes building a custom fuzzy arg matcher with YAML synonym dictionaries. This is brittle and difficult to maintain as the tool tier scales to 30 complex tools. Instead, utilize a fast, cheap LLM (like Haiku or GPT-4o-mini) to evaluate whether the emitted tool arguments semantically satisfy the intended goal.
>
> 4. **Field-Recorded Audio Fixtures:** Replace or augment the synthetic TTS (`say`, ElevenLabs) fixtures with actual voice recordings captured from the SciSymbioLens app in real lab environments. This will test the true resilience of the STT engines against domain-specific jargon and acoustic interference.

### What changed in response
- **All 5 architectural concerns acknowledged in a new "Known Limitations & v2 Roadmap" section** with concrete v2 stories planned for each (validation pass, robustness suite, multi-turn suite, async/barge-in suite). v1 is positioned as a "clean room" benchmark of *agent capability*, deliberately distinct from *Android-specific performance*.
- **Concern #3 (overfitting) addressed in plan**: Story 6.1 now mandates a 30/10/10 train/validation/holdout split; `voice-bench optimize` stops on validation plateau, and holdout is only used for final reporting.
- **All 4 alternatives logged in new "Considered Alternatives" section** with explicit rationale for rejection or partial adoption.
- **Alternative #3 (LLM-as-judge) partially adopted**: Story 7.2 rewritten as a hybrid — YAML primary with LLM-judge fallback for ambiguous cases, cached by `(tool, expected, actual)` to keep cost down.

### Reference: karpathy/autoresearch (round 3)
After the two formal reviews, the user pointed to <https://github.com/karpathy/autoresearch> as a reference for the self-improvement design. The repo is an overnight autonomous-research harness: an LLM modifies a single training file (`train.py`), runs a fixed-budget experiment (5-min training), checks if `val_bpb` improved, keeps or discards via git, logs to a `results.tsv`, and never pauses for the human. Patterns transferred to Epic 9:

| autoresearch pattern | voice-bench application |
|---|---|
| Agent modifies *one* file (`train.py`) | Optimizer modifies *one* file (`prompts/system/<agent>.md`) — tool defs, harness, scoring all read-only |
| Fixed 5-min training budget per experiment | Fixed 30-prompt train-set evaluation per round |
| Single metric (`val_bpb`) for ranking | Composite `val_score = val_accuracy − α·prompt_chars/1000` — accuracy plus a conciseness penalty answering the user's "reward conciseness" question |
| Dedicated branch per session (`autoresearch/<tag>`) | Dedicated branch per optimize run (`voice-bench-opt/<agent>/<run-id>`) — every revision = a commit = free version history |
| `results.tsv` with keep/discard/crash | `optimizations/<agent>.tsv` with the same status column |
| Explicit simplicity criterion in `program.md` | Same criterion embedded in `prompts/optimizer.md` — nudges optimizer to *delete* instructions, not just add |
| `program.md` is the human's meta-knob | `prompts/optimizer.md` is the human's meta-knob (Story 9.2) |
| NEVER STOP directive for autonomous overnight runs | Same directive when `--mode autonomous` |
| ~100 experiments overnight | ~50 rounds default, configurable via `--max-rounds` and `--max-wallclock` (user's "how many iterations" question) |

**Differences from autoresearch** (where voice-bench needs *more*, not less):
1. **Overfitting risk is real here** — autoresearch optimizes against a fixed dataset and accepts overfit to it (the goal is best-on-this-dataset). voice-bench needs *generalization*, so the train/val/holdout walls (Story 6.1, hardened in this round) are non-negotiable and the holdout drift detector (Story 9.4) is added on top.
2. **Conciseness as a first-class metric** — autoresearch has a soft simplicity criterion. voice-bench encodes it numerically (α penalty) so the optimizer can't ignore it under pressure.
3. **Failure clustering** (Story 9.3) — autoresearch lets the agent freely browse the file; voice-bench feeds the optimizer *clustered failure transcripts* rather than raw prompt texts, slowing direct memorization.

## Revision History
- 2026-05-18 — Initial draft (post-recon of SciSymbioLens-Android voice providers).
- 2026-05-18 — Revised after Codex round-1 critique. Substantial changes: tool plumbing (Stories 3.2/3.3) and provider capability probe (Story 4.0) added to V1 first slice; `TurnTimeline` split-timestamps schema replaces single TTFS; per-turn deadlines + `TerminalReason` enum specified; `Score` schema expanded for edge cases; tier-1 narrowed to boolean toggles; first-slice example tool changed to `toggle_flash`; model ID corrected; two new risks added.
- 2026-05-18 — Revised after Gemini round-2 architectural critique. Substantial changes: added "Known Limitations & v2 Roadmap" section with explicit v2 stories for clean-room validity, synthetic environment, multi-turn dialogues, and async/barge-in behavior; added "Considered Alternatives" section documenting on-device harness and proxy/interceptor as rejected with rationale; Story 6.1 now mandates 30/10/10 train/val/holdout split to prevent optimizer overfitting; Story 7.2 rewritten as hybrid YAML + LLM-as-judge scoring; status moved to Reviewed — awaiting approval.
- 2026-05-18 — Revised round 3: Epic 9 rewritten using patterns from `karpathy/autoresearch`. Key changes: optimizer modifies a single file (`prompts/system/<agent>.md`); each run on a dedicated git branch with one commit per revision (full version history for free); composite score `val_accuracy - alpha * prompt_chars/1000` to reward conciseness; explicit train/val/holdout walls (optimizer never sees val or holdout text); `optimizations/<agent>.tsv` log with keep/discard/crash status; configurable iteration cap and patience; holdout drift detector; simplicity criterion embedded in optimizer prompt to nudge it toward *deleting* instructions; NEVER STOP directive for autonomous mode. Optimizer's own prompt (`prompts/optimizer.md`) is itself a human-editable meta-knob mirroring autoresearch's `program.md`.
