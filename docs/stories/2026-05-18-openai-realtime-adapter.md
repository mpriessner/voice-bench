# Story: OpenAI Realtime adapter

**ID:** 2026-05-18-openai-realtime-adapter
**Status:** Implemented
**Created:** 2026-05-18

## Goal

Add an `OpenAIRealtimeAdapter` to voice-bench so we can benchmark OpenAI's native
voice model (`gpt-realtime` / `gpt-4o-realtime-preview`) on the same prompts as
the existing Gemini Live adapter, producing comparable accuracy and latency
metrics.

## Context

voice-bench currently has one working adapter — `GeminiLiveAdapter` at
`src/voice_bench/adapters/gemini_live.py` — which produces 100% accuracy on a
50-prompt manifest at 5–30 tool counts, ~700 ms `ttf_tool` median. With only one
data point the harness has no comparison axis; getting a second native voice
adapter is the highest-leverage next step before either expanding prompts or
building visualization.

The adapter must satisfy the `NativeVoiceAdapter` protocol in
`src/voice_bench/adapters/base.py`: `probe()` + `run_turn(audio_wav_path,
tools, system_prompt, turn_id, prompt_id, timeouts)` returning a `TurnResult`.
`runner.py` and `scoring.py` are provider-agnostic and do not need changes.

Hard-won lesson from Gemini: pre-recorded WAVs do **not** work via
streaming-input + server-side VAD — VAD silently discards non-mic audio and the
model never responds. The fix was `send_client_content(turn_complete=True)`.
The OpenAI Realtime API is event-driven rather than method-based, but the same
principle applies: we must disable server VAD and explicitly commit + request a
response, otherwise the model waits indefinitely.

### Wire format (verified-via-probe-script facts)

> Several of these were initially drafted from memory and corrected after Codex
> caught them — the API surface has shifted as Realtime exited beta. The Step 0
> probe script is the authoritative source; if it disagrees with this section,
> the probe wins and this section gets updated.

- **WebSocket URL**: `wss://api.openai.com/v1/realtime?model=<model_id>`
- **Auth headers**: `Authorization: Bearer <OPENAI_API_KEY>` (the `OpenAI-Beta:
  realtime=v1` header is no longer required — Realtime is GA)
- **Audio format**: 24 kHz mono PCM16 (different from Gemini's 16 kHz — needs a
  new helper)
- **Model IDs**:
  - `gpt-realtime` — current GA alias (resolves to dated snapshot)
  - `gpt-4o-realtime-preview-2024-12-17` — older fallback if `gpt-realtime`
    is not enabled on the account
  - Default to `gpt-realtime` with env override `OPENAI_REALTIME_MODEL`
- **SDK**: `openai>=1.50` exposes `client.realtime.connect(model=...)` as an
  async context manager. **Note:** this is the **non-beta** namespace; older
  examples on the internet show `client.beta.realtime` which is deprecated. The
  Step 0 probe must verify the namespace currently shipped in the installed
  `openai` package; fall back to `client.beta.realtime` if `client.realtime` is
  not present.
- **Tool format**: OpenAI's function tools accept JSON Schema directly — no
  `Schema` object construction is needed (unlike Gemini). The format is
  `{"type": "function", "name": ..., "description": ..., "parameters": <JSON Schema>}`.
  This is **simpler** than Gemini's adapter; `DummyTool.parameters` can be passed
  through unchanged.

### `session.update` payload shape (current GA form)

The Realtime API moved field names when it exited beta. Use the **current**
shape:

```json
{
  "type": "session.update",
  "session": {
    "type": "realtime",
    "instructions": "<system prompt>",
    "tools": [<tool dicts>],
    "output_modalities": ["audio"],
    "input_audio_format": "pcm16",
    "output_audio_format": "pcm16",
    "turn_detection": null
  }
}
```

Key differences from older docs: `output_modalities` (not `modalities`), and
everything is wrapped under `session: {type: "realtime", ...}`. **Verify this
shape with Step 0 probe before committing.** If the SDK provides
`connection.session.update(session={...})`, prefer that over raw event
dictionaries.

### Event flow for one benchmark turn

1. Open WS → receive `session.created` event → record `ts_setup_complete`
2. Send `session.update` (shape above)
3. Receive `session.updated` confirmation (ack)
4. Send audio in chunks: `input_audio_buffer.append` events with base64-encoded
   PCM16. Chunking is required because each event has a size limit (~15 MB), but
   pacing is not — we can send chunks back-to-back since VAD is off.
5. Send `input_audio_buffer.commit` → record `ts_input_audio_end`
6. Send `response.create` — this is what makes the model actually respond;
   without it, the server just buffers.
7. Receive event stream:
   - `response.created`, `response.output_item.added`, `response.content_part.added`
   - **Function call**: `response.function_call_arguments.delta` (streaming JSON
     args fragments) → `response.function_call_arguments.done` (this event
     already contains the complete `arguments` string, the `call_id`, the
     `item_id`, and `name`). **Build the `ToolCallEvent` from `.done`, not from
     `response.output_item.done`.** Deltas are used only to record the first
     `ts_first_tool_call_emitted` timing.
   - **Audio**: `response.audio.delta` (base64 PCM16 chunks) → record
     `ts_first_output_audio` on first delta
   - **Text** (if requested): `response.audio_transcript.delta`
   - **Terminal**: `response.done` — must inspect `response.status` field:
     `completed` → `TURN_COMPLETE`; `failed` or `cancelled` → `PROVIDER_ERROR`;
     `incomplete` → preserve whichever terminal reason the receive loop already
     set (likely a timeout).
8. On `response.function_call_arguments.done`:
   1. Parse `arguments` (JSON string) — **wrap in try/except**. On
      `json.JSONDecodeError`, log to `raw_events` with `kind="malformed_args"`,
      do NOT create a `ToolCallEvent`, do NOT send a tool response (server
      would reject), and increment a local malformed-counter. The harness's
      `score.malformed_calls` field consumes this signal (see Risk #9).
   2. On successful parse: build `ToolCallEvent(name, args, call_id)`, record
      `ts_first_tool_call_emitted` (if not already set).
   3. Send `connection.conversation.item.create(item={"type":
      "function_call_output", "call_id": <call_id>, "output":
      '{"result":"ok"}'})` → record `ts_tool_response_sent`.
   4. **Issue a second `response.create`** to elicit the verbal confirmation.
      This is required for cross-provider TTFS comparability — `ttfs_ms` is
      one of the two foundational benchmark metrics and OpenAI must produce
      it the same way Gemini does (Gemini emits post-tool audio automatically
      via the same `turn_complete` flow; OpenAI requires the explicit
      response). The cost is ~1 extra model round-trip per turn; that cost
      is the price of fair comparison and is non-optional in v1.
9. After the second `response.create`, continue consuming events:
   - `response.audio.delta` → first chunk records `ts_first_output_audio`
   - `response.done` (second one, for the post-tool response) → inspect
     `status` per Step 7. On `completed`, set `ts_turn_complete` →
     `TerminalReason.TURN_COMPLETE`.
10. Close WS

### Mapping events to `TurnTimeline`

| Timeline field                  | OpenAI event                                                  |
|---------------------------------|---------------------------------------------------------------|
| `ts_connect_start`              | Before `client.beta.realtime.connect()`                       |
| `ts_setup_complete`             | `session.created` received                                    |
| `ts_input_audio_start`          | Before first `input_audio_buffer.append`                      |
| `ts_input_audio_end`            | After sending `input_audio_buffer.commit`                     |
| `ts_first_event_received`       | First event after `response.create`                           |
| `ts_first_tool_call_emitted`    | First `response.function_call_arguments.delta` (or `.done` if no delta) |
| `ts_tool_response_sent`         | After sending `conversation.item.create` (function_call_output) |
| `ts_first_output_audio`         | First `response.audio.delta`                                  |
| `ts_turn_complete`              | `response.done`                                               |

## Acceptance Criteria

- [ ] `voice-bench probe --agent openai-realtime` connects, receives
  `session.created`, returns `{"status": "ok"}` with `connect_ms`. (Requires
  adding the `openai-realtime` branch to **both** `cli.py:run()` and
  `cli.py:probe()` — the latter is easy to miss.)
- [ ] `voice-bench run --agent openai-realtime --tools 5 --mode smoke` produces a
  results JSONL with at least 1 `tool_name_match=True` (sanity check — full
  scoreboard not required for this story).
- [ ] For a successful tool-calling turn, **all 9** `TurnTimeline` timestamp
  fields MUST be populated: `ts_connect_start`, `ts_setup_complete`,
  `ts_input_audio_start`, `ts_input_audio_end`, `ts_first_event_received`,
  `ts_first_tool_call_emitted`, `ts_tool_response_sent`,
  `ts_first_output_audio`, `ts_turn_complete`. (Requires the second
  `response.create` after the tool response — see Implementation Plan step 8.4.)
- [ ] On the smoke run, the JSONL shows a non-null `ttfs_ms` for at least one
  PASS row — confirming the second-response audio was actually received and
  timed.
- [ ] `OPENAI_API_KEY` is documented in `README.md`'s env-var table AND the
  Quick Start section's setup step mentions it alongside `GEMINI_API_KEY`.
- [ ] `VALID_AGENTS` in `cli.py` includes `"openai-realtime"`.
- [ ] `runner.py` instantiates `OpenAIRealtimeAdapter` when `agent ==
  "openai-realtime"`.
- [ ] A system prompt file exists at `prompts/system/openai-realtime.md`
  (copy of `gemini-live.md` initially; can be tuned per-provider later).
- [ ] `pyproject.toml` declares the version of `openai` that exposes
  `client.realtime` (likely `>=1.50`; verified by Step 0 probe).
- [ ] On `PROVIDER_ERROR`, the adapter logs the raw event payload so failures
  are debuggable from `results/*.jsonl` (same pattern as Gemini adapter), using
  the SDK's structured serializer (`event.model_dump_json()` or equivalent),
  not `str(event)`.
- [ ] If `response.function_call_arguments.done` contains malformed JSON in
  `arguments`, the adapter does NOT crash — it logs to `raw_events`, skips the
  `ToolCallEvent`, and the turn is reported with `terminal_reason` set to
  whatever the receive loop reached. (No new acceptance test for this — it's
  defensive plumbing.)

## Implementation Plan

### Step 0 — Tiny probe script (de-risk first)

Before touching the package, write a one-file script at
`scripts/probe_openai_realtime.py` that:
1. Connects to OpenAI Realtime. Verify **which SDK namespace ships** —
   try `client.realtime` first, fall back to `client.beta.realtime`.
   Print whichever path worked so the adapter pins the same one.
2. Sends a 1-tool `session.update` using the current GA payload shape
   (`session={"type": "realtime", "output_modalities": [...], "turn_detection":
   null, ...}`). If the API rejects the shape, print the error and adjust.
3. Streams `prompts/audio/say/p001.wav` ("Turn on the flash"), upsampled to
   24 kHz, via `input_audio_buffer.append` + `commit` + `response.create`.
4. Prints **every** event received with its type AND serialized payload (via
   `event.model_dump_json()` or `event.to_dict()` — whichever the SDK exposes).
5. Confirms a `response.function_call_arguments.done` event fires with
   `name="toggle_flash"`, `call_id` set, and parseable JSON in `arguments`.
6. Sends `conversation.item.create(item={"type": "function_call_output",
   "call_id": ..., "output": ...})` to verify the wrapper is accepted.
7. Receives `response.done` and prints its `status` field.

This validates the event flow AND the field shapes before committing to the
full adapter structure. **Do not proceed to Step 1 until this script succeeds**
end-to-end. Findings update the "Wire format" section of this story before
implementation begins.

### Step 1 — Add dependency + 24 kHz audio helper

- Add `openai>=1.50` (or whatever Step 0 confirmed exposes
  `client.realtime.connect`) to `pyproject.toml` dependencies; run `uv sync`.
- Generalize `_load_pcm16_16k` in the codebase — refactor to
  `_load_pcm16(wav_path, target_rate)` in a new module
  `src/voice_bench/audio.py` so both adapters can share it. Move from
  `gemini_live.py`; the Gemini adapter calls it with `target_rate=16000`, the
  new OpenAI adapter with `target_rate=24000`.
- Add a tiny unit test (`tests/test_audio.py`) that loads a sample WAV at both
  16 kHz and 24 kHz and asserts the returned byte-length matches the
  resampling ratio. This guards against breaking Gemini's audio path while
  refactoring.

### Step 2 — Create `OpenAIRealtimeAdapter`

File: `src/voice_bench/adapters/openai_realtime.py`. Structure mirrors
`gemini_live.py`:

```python
DEFAULT_MODEL = "gpt-realtime"
AUDIO_RATE = 24000
AUDIO_CHUNK_BYTES = 24000 * 2 // 10  # ~100ms of 24kHz PCM16

class OpenAIRealtimeAdapter:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError("Set OPENAI_API_KEY environment variable")
        self.model = model or os.environ.get("OPENAI_REALTIME_MODEL", DEFAULT_MODEL)
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=self.api_key)

    def _build_tools(self, tools: list[DummyTool]) -> list[dict]:
        """OpenAI takes JSON Schema directly — no Schema-object conversion."""
        return [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in tools
        ]

    async def probe(self) -> dict:
        ts_start = time.time()
        try:
            async with asyncio.timeout(15.0):
                # Step-0-verified namespace: client.realtime (not client.beta.realtime)
                async with self.client.realtime.connect(model=self.model) as conn:
                    # Wait for session.created
                    async for event in conn:
                        if event.type == "session.created":
                            return {
                                "agent": "openai-realtime",
                                "model": self.model,
                                "connect_ms": int((time.time() - ts_start) * 1000),
                                "status": "ok",
                            }
                        if event.type == "error":
                            return {"agent": "openai-realtime", "model": self.model,
                                    "status": "error", "error": str(event)}
        except asyncio.TimeoutError:
            return {"agent": "openai-realtime", "model": self.model, "status": "timeout"}

    async def run_turn(self, audio_wav_path, tools, system_prompt, turn_id,
                       prompt_id, timeouts=None) -> TurnResult:
        # ... full implementation per event mapping table above
```

The receive loop is a state machine over event types. **The naïve `async for
event in conn:` pattern cannot enforce a quiet timeout** (a known bug in the
Gemini adapter at `gemini_live.py:301` that this story does NOT inherit).
Wrap each next-event in `asyncio.wait_for` so the loop wakes periodically:

```python
event_iter = conn.__aiter__()
while True:
    try:
        event = await asyncio.wait_for(event_iter.__anext__(), timeout=t["quiet"])
    except asyncio.TimeoutError:
        terminal_reason = TerminalReason.TIMEOUT_FIRST_TOOL  # or appropriate
        break
    except StopAsyncIteration:
        break
    # ...dispatch on event.type...
```

Key event-type branches:

- `session.created` → `ts_setup_complete`
- `session.updated` → ack of our config (raw_events only)
- `response.created` → `ts_first_event_received` (if not set)
- `response.function_call_arguments.delta` → `ts_first_tool_call_emitted` (if
  not set). Deltas only used for timing; do not accumulate.
- `response.function_call_arguments.done` → parse `arguments` JSON (guarded);
  on success build `ToolCallEvent` from `.name`, `.call_id`, parsed args; send
  `conversation.item.create(item={"type": "function_call_output", ...})` via
  `connection.conversation.item.create(...)`; record `ts_tool_response_sent`.
  On JSON parse failure: log `raw_events` with `kind="malformed_args"`, skip
  the event, do **not** send a tool response.
- `response.audio.delta` → `ts_first_output_audio` (if not set) — may never
  fire in v1 since we skip the post-tool `response.create`.
- `response.done` → inspect `event.response.status`:
  - `"completed"` → `ts_turn_complete`, `terminal_reason = TURN_COMPLETE`, break
  - `"failed"` or `"cancelled"` → log status_details, `terminal_reason =
    PROVIDER_ERROR`, break
  - `"incomplete"` → keep whichever terminal reason the loop reached (likely a
    timeout), break
- `error` → log full payload to `raw_events` via `event.model_dump_json()`,
  set `PROVIDER_ERROR`, break

Use the same `seen_call_ids` dedup pattern as Gemini. Serialize event payloads
via the SDK's structured form (`event.model_dump_json()` /
`event.model_dump(mode="json")`) so `results/*.jsonl` is debuggable; fall back
to `str(event)` only if those attributes are absent.

### Step 3 — Wire into the runner AND probe

Three call sites need updating, not two — easy to miss the `probe` branch:

In `runner.py:67`:
```python
if agent == "gemini-live":
    adapter = GeminiLiveAdapter()
elif agent == "openai-realtime":
    from .adapters.openai_realtime import OpenAIRealtimeAdapter
    adapter = OpenAIRealtimeAdapter()
else:
    raise NotImplementedError(f"Adapter not yet implemented: {agent}")
```

In `cli.py:14`:
```python
VALID_AGENTS = ["gemini-live", "openai-realtime"]
```

In `cli.py:probe()` (around line 29 — the hardcoded `if agent == "gemini-live"`
branch):
```python
if agent == "gemini-live":
    from .adapters.gemini_live import GeminiLiveAdapter
    adapter = GeminiLiveAdapter()
elif agent == "openai-realtime":
    from .adapters.openai_realtime import OpenAIRealtimeAdapter
    adapter = OpenAIRealtimeAdapter()
else:
    click.echo(f"No adapter for {agent}", err=True)
    sys.exit(1)
result = asyncio.run(adapter.probe())
```

Also export the new adapter from `src/voice_bench/adapters/__init__.py` for
consistency with `GeminiLiveAdapter`.

### Step 4 — System prompt file

Copy `prompts/system/gemini-live.md` → `prompts/system/openai-realtime.md`
(identical content for now; provider-specific tuning is a later optimizer task).

### Step 5 — Update README + .env.example

Add to env-var table:

| `OPENAI_API_KEY` | Yes (for openai-realtime) | — | OpenAI API key |
| `OPENAI_REALTIME_MODEL` | No | `gpt-realtime` | Realtime model ID |

Update the `voice-bench probe` and `voice-bench run` examples to show both
agents. Update the Quick Start "Set up API keys" step to mention
`OPENAI_API_KEY` alongside `GEMINI_API_KEY`.

**Caveat on `.env.example`**: the project's damage-control hook
(`.claude/hooks/damage-control/bash-tool-damage-control.py`) blocks ALL `.env*`
file operations, so an AI cannot create or edit the example file. The user
must add `OPENAI_API_KEY=...` to their `.env` and `.env.example` manually;
the README setup step should call this out explicitly.

### Step 6 — Verify

1. `voice-bench probe --agent openai-realtime` → expect `status: ok`
2. `voice-bench run --agent openai-realtime --tools 5 --mode smoke` → expect at
   least 1 PASS
3. Inspect the JSONL: confirm all 8 timeline fields populated, raw events
   include event types from the mapping table.

## Risks & Open Questions

1. **`openai-python` SDK realtime API may not be 1:1 with the WS protocol.**
   The SDK abstracts over the raw events somewhat. If `client.realtime` has
   awkward ergonomics or doesn't expose all events, fall back to raw
   `websockets` library (Gemini's SDK was used; OpenAI's may not be needed at
   all). The probe script in Step 0 confirms which path to take.

2. **Model availability**: `gpt-realtime` may require a paid OpenAI account or
   organization access. If the user's API key lacks access, probe will fail
   with 401/403. Document this in README troubleshooting and have the adapter
   surface the HTTP error verbatim via the `status: error` probe path.

3. **Tool-call args arrive on `.done` complete, not delta-accumulated**.
   `response.function_call_arguments.done.arguments` already contains the full
   JSON string; deltas are diagnostic only. Codex initially flagged this — the
   plan now builds `ToolCallEvent` from `.done`, with try/except around
   `json.loads(arguments)`. Malformed JSON logs to `raw_events` and skips the
   call.

4. **`turn_detection: null` may be the wrong key**. Some versions of the API
   want `{"type": "server_vad", "threshold": null}` or just omitting the field.
   The probe script must verify the disabled-VAD behavior empirically.

5. **`response.create` after `input_audio_buffer.commit` is mandatory** when
   VAD is off. Without it, the server buffers audio forever. This is the
   OpenAI analogue of Gemini's `turn_complete=True`.

6. **`response.done` is not always `completed`.** Inspect `response.status` —
   `failed`/`cancelled` should not be silently scored as `TURN_COMPLETE`. The
   implementation plan now maps each status explicitly (see Step 7 of the
   event flow table).

7. **Latency reference frame**: For Gemini we measured `ttf_tool` from
   `ts_input_audio_end` (when we finished sending to the SDK). For OpenAI,
   `ts_input_audio_end` is when `input_audio_buffer.commit` is sent. These
   should be analogous (both = "I'm done speaking" timestamp from the client
   side), but call out that the WS round-trip is now part of the measurement,
   whereas Gemini's `send_client_content` was a single batched call. We are
   accepting this asymmetry — both providers see "client thinks audio is done"
   as the reference.

8. **Audio resampling correctness**: Source WAVs are 16 kHz (macOS `say`). The
   24 kHz upsample uses `scipy.signal.resample_poly` with ratio 3/2. The unit
   test in Step 1 covers byte-length only; intelligibility is verified
   in-band via the smoke run (if the model picks the right tool, the audio
   was understood).

9. **Inherited bug — quiet-timeout pattern in Gemini adapter**: Codex flagged
   that `gemini_live.py:301` (`while time.time() - last_event_ts > t["quiet"]`)
   never fires inside `async for message in session.receive()` because the
   `async for` blocks on the next message. The new OpenAI adapter uses
   `asyncio.wait_for(iter.__anext__(), timeout=quiet)` per event to fix this.
   The Gemini adapter is **not** changed in this story (out of scope), but
   noted as a follow-up. In practice Gemini turns finish via
   `turn_complete=True` from the server, so the quiet timeout has rarely
   mattered; the OpenAI adapter cannot rely on that.

10. **`Score.malformed_calls` already exists in the data model but scoring.py
    always returns 0** (Codex finding). This story adds the adapter-side
    signal (malformed args → `raw_events` with `kind="malformed_args"`) but
    does NOT modify `scoring.py` — wiring the signal into `Score` is a
    separate change. Acceptable for v1; flagged for future work.

11. **Should we also enable server-VAD as a separate test mode?** Some
    real-world voice apps use server-VAD; if Gemini and OpenAI behave
    differently with VAD enabled, that's worth measuring. **Deferred** to a
    future story — for parity with the Gemini baseline we use disabled-VAD now.

12. **Connection reuse across turns**: The current Gemini adapter opens a
    fresh WS per turn. The new OpenAI adapter does the same. If we ever
    optimize by reusing connections, we'll need `input_audio_buffer.clear`
    before each turn (Codex noted) — but for v1, fresh connections sidestep
    this entirely. Architectural note (Gemini reviewer): fresh-per-turn tests
    cold-start performance only, not steady-state behavior in a long-lived
    lab session, which is closer to how SciSymbioLens actually uses these
    APIs. Considered and accepted for v1; persistent-session benchmarking
    is a deferred separate-story concern that should apply to both adapters
    symmetrically when it lands.

13. **VAD-disabled is a deliberate trade-off**, not free correctness. The
    Foundation story envisioned using the trailing 500 ms of silence to
    measure each provider's server-side VAD as a feature in its own right
    (often 500–1500 ms of additional latency in production open-mic mode).
    By committing the buffer and calling `response.create` ourselves, we are
    measuring "push-to-talk" latency, not realistic open-mic latency. This
    asymmetry exists in both adapters consistently (Gemini also uses
    `turn_complete=True` for the same reason), so cross-provider comparison
    remains fair — but the numbers do not reflect open-mic UX. A future
    "vad-mode" comparison story should measure the VAD branch when the
    provider supports it. **Empirical reality**: Gemini Live's VAD silently
    drops pre-recorded WAVs entirely (0/5 in earlier testing), so a working
    VAD test mode may need to use synthetic microphone playback (loopback)
    rather than buffer-append. Out of scope here.

14. **Pipelined-adapter contract consistency** (raised by architectural
    review): when Epic 5 lands pipelined LLM+TTS adapters, the "always
    produce a verbal confirmation after tool call" rule established here
    must be applied uniformly. Pipelined adapters should not be able to
    truncate their TTS leg to win on `ttfs_ms` if natives can't. This story
    sets the precedent: a turn is incomplete without the verbal confirmation
    when the model would naturally produce one. The adapter contract docstring
    in `base.py` should be updated to make this explicit (small follow-up
    change after this story merges).

## Out of Scope

- Pipelined LLM+TTS adapters (Claude/GPT + ElevenLabs/OpenAI TTS) — separate story.
- Other native voice providers (xAI Grok, ElevenLabs Conv-AI, Hume EVI, Deepgram
  Aura, Nova Sonic) — separate stories each.
- Dashboard / visualization — deferred until we have ≥2 working adapters.
- Multi-turn dialogues — still single-turn only in v1.
- Server-VAD vs disabled-VAD comparison mode — future work.
- Provider-specific prompt optimization (the `voice-bench optimize` loop is its
  own Epic 9).

## Reviewer Feedback / Codex (round 1)

Raw critique, captured verbatim:

```
Critical issues:
- [Plan lines 45 and 181] use client.beta.realtime.connect(...), but current
  official openai-python docs show client.realtime.connect(model="gpt-realtime")
  instead. openai>=1.40 is also too vague for this surface; the latest docs show
  the non-beta namespace and a current release train. Source: OpenAI Python
  README Realtime example.
- [Plan lines 57-59] assume session.update fields modalities,
  input_audio_format, and output_audio_format. Current SDK examples use
  session={"type": "realtime", "output_modalities": [...]}; current API
  reference also says session.update wraps a session object. This needs a
  concrete verified request shape before implementation. Source: Realtime client
  event reference and OpenAI Python README.
- [Plan lines 76-78] omit the required item wrapper for raw
  conversation.item.create. The item itself is
  {"type":"function_call_output", "call_id": ..., "output": ...}, but the client
  event is {"type":"conversation.item.create", "item": {...}}; the SDK
  equivalent is connection.conversation.item.create(item={...}). Source:
  Realtime guide function-calling example and API reference.
- [Plan lines 69-71 and 207-208] get the function-call data ordering partly
  wrong. response.function_call_arguments.done itself includes arguments,
  call_id, item_id, and name; call_id is not only available at
  response.output_item.done. The adapter should build ToolCallEvent from
  .done.arguments and .done.call_id, using deltas only for timing/diagnostics.
  Source: Realtime server event reference.
- [Plan lines 76-83] likely marks a tool-call response complete too early.
  Official guidance says function-call responses return function-call data
  instead of normal text/audio, and response.done contains the complete call;
  after executing the tool, you provide output so the model can generate
  another response. If v1 skips the second response.create,
  ts_first_output_audio will often stay None, contradicting acceptance line
  107. Source: Realtime guide lines on function-call detection.
- [Plan line 107] says "all 8 TurnTimeline fields", but TurnTimeline has 9
  timestamp fields, and ts_first_output_audio is not guaranteed for tool-only
  turns. The acceptance criterion should not require audio timing unless the
  adapter deliberately requests and waits for the post-tool verbal response.
- [Plan lines 210-211] treats any response.done as TURN_COMPLETE. The API
  reference says response.done is always emitted and clients must inspect
  response.status for completed, cancelled, failed, or incomplete;
  non-completed statuses should map to PROVIDER_ERROR, timeout-like terminal
  reasons, or at least raw diagnostic events.
- [Step 3] misses cli.py's probe() branch. Updating only VALID_AGENTS lets
  Click accept openai-realtime, but cli.py still prints "No adapter for
  openai-realtime" and exits.
- The receive loop plan does not describe a real quiet timeout. async for
  event in conn cannot check quiet time while no events arrive; it needs
  asyncio.wait_for around the next event or a separate timeout mechanism.
  Gemini has the same ineffective pattern at gemini_live.py:301, so copying
  it preserves the bug.
- Malformed function arguments are not handled.
  response.function_call_arguments.done.arguments is a JSON string; parse
  failures should be logged and reflected somehow, but Score.malformed_calls
  currently exists while scoring.py always sets it to 0.

Nice-to-have:
- Export OpenAIRealtimeAdapter from adapters/__init__.py for consistency, even
  though runner.py can import directly.
- Update .env.example and README Quick Start, not just the README env-var
  table; current setup text only mentions GEMINI_API_KEY.
- Add a small unit test around _load_pcm16(wav_path, target_rate) so the
  Gemini 16 kHz path does not regress when gemini_live.py is refactored.
- Log complete Realtime event payloads with SDK-safe serialization (to_dict()
  / model_dump_json() / model_extra) rather than str(event), so
  results/*.jsonl is actually useful for failed protocol cases.
- Consider input_audio_buffer.clear before a turn if the adapter is ever
  reused on one connection; official docs call this out when VAD is disabled.
```

## Revision History

- 2026-05-18 — Initial draft.
- 2026-05-18 — Round 1 revision after Codex critique. Changes:
  - SDK namespace corrected (`client.realtime`, not `client.beta.realtime`)
    with fallback path documented.
  - `session.update` payload shape rewritten with current GA structure
    (`output_modalities`, `session.type="realtime"`).
  - `conversation.item.create` now uses the required `item` wrapper.
  - Tool-call extraction moved to `response.function_call_arguments.done`
    (which already carries `arguments`, `call_id`, `item_id`, `name`).
  - Acceptance criterion #3 relaxed: 8 required timeline fields,
    `ts_first_output_audio` optional (it stays None in v1 because we skip
    the second `response.create` after tool response).
  - Added `response.done.status` switching: completed → TURN_COMPLETE;
    failed/cancelled → PROVIDER_ERROR; incomplete → preserved.
  - Added `cli.py:probe()` branch update to Step 3 (was previously
    missed).
  - Quiet-timeout pattern replaced with `asyncio.wait_for(iter.__anext__())`
    pattern; the inherited Gemini bug is documented as a separate follow-up.
  - Malformed-args JSON parsing now guarded; logs to `raw_events` with
    `kind="malformed_args"`, skips ToolCallEvent.
  - Step 0 probe expanded: verify SDK namespace, payload shape, AND tool-
    response wrapper before adapter implementation begins.
  - Added unit-test step for `_load_pcm16` helper to guard against
    regression in Gemini's 16 kHz path during the refactor.
  - Adapter to be exported from `adapters/__init__.py`.
  - Codex round-1 critique appended verbatim above.
- 2026-05-18 — Round 2 revision after Gemini architectural critique. Changes:
  - **Reversed the "skip second `response.create`" decision.** TTFS is one
    of the two foundational benchmark metrics; allowing OpenAI to opt out
    silently broke cross-provider comparability with Gemini (which produces
    post-tool audio naturally) and would have crippled the Epic 9 optimizer
    loop and Epic 10 visualization. Cost of one extra round-trip per turn
    accepted as the price of fair comparison.
  - Acceptance criterion #3 tightened: all 9 timeline fields required,
    including `ts_first_output_audio`; smoke run must show at least one
    non-null `ttfs_ms`.
  - Added Risk #13: VAD-disabled is a deliberate trade-off, documented
    explicitly with counter-evidence (Gemini VAD drops pre-recorded WAVs);
    "vad-mode" deferred to a separate story.
  - Added Risk #14: pipelined-adapter contract consistency — sets the
    precedent that a turn includes the verbal confirmation when the model
    naturally produces one; recommends a small follow-up to update the
    `NativeVoiceAdapter` docstring after this story merges.
  - Documented persistent-session benchmarking as a deferred symmetric
    concern (Risk #12 augmented).
  - Gemini round-2 critique appended verbatim below.
- 2026-05-18 — Implemented. Two wire-format corrections discovered during probe:
  - `audio.input.format` requires explicit `rate: 24000` (API rejects without it).
  - Audio output events are named `response.output_audio.delta` (not `response.audio.delta`) in openai>=2.x; transcript events similarly `response.output_audio_transcript.delta`. Both handled with `in (...)` checks.

## Reviewer Feedback / Gemini (round 2)

Raw critique, captured verbatim:

```
Architectural concerns:
1. VAD Bypassing Subverts the Foundation Story: The plan explicitly relies on
   disabling server VAD (turn_detection: null) and manually triggering
   responses (input_audio_buffer.commit + response.create), copying Gemini's
   turn_complete=True. However, the Foundation story planned to use "trailing
   500 ms of silence" specifically to test providers' internal VAD. By
   explicitly signaling end-of-speech, the benchmark measures a
   "push-to-talk" latency and completely ignores the server-side VAD delay
   (often 500–1500ms) that the real SciSymbioLens open-mic users will
   inevitably experience.
2. TTFS Cross-Provider Inconsistency: The plan deliberately skips the second
   response.create after sending the synthetic tool response, meaning
   ts_first_output_audio (TTFS) will intentionally remain None for OpenAI.
   But TTFS is one of the two foundational metrics of voice-bench, required
   for the Epic 10 "accuracy-vs-latency scatter" Pareto frontier. Allowing
   OpenAI to unilaterally opt-out of the spoken confirmation breaks
   comparability with Gemini and future pipelined adapters, which may
   generate post-tool audio.
3. Optimizer Loop (Epic 9) Blind Spot: The self-improvement loop optimizes
   system prompts, which heavily influence agent behavior around tool-calling
   vs. speaking (e.g., saying "I'll adjust that" before firing a tool vs.
   firing silently). By structurally preventing the OpenAI adapter from
   requesting or receiving post-tool audio, the harness robs the optimizer of
   the ability to tune or measure these conversational nuances, creating an
   uneven playing field.
4. Pipelined Adapter Contract Mismatch (Epic 5): If native adapters like
   OpenAI use custom shortcuts (like skipping the final verbal response) to
   optimize benchmark speed and cost, the future Pipelined Adapters (STT →
   LLM → TTS) will have no clear standard for what constitutes a "fair"
   turn. Pipelined adapters would be forced to artificially truncate their
   pipelines (e.g., dropping the TTS leg entirely on tool calls) just to
   remain latency-competitive on the scoreboard.
5. Cold-Start Bias (Fresh Connection per Turn): The plan states that opening
   a new WebSocket for every turn "sidesteps input_audio_buffer.clear
   entirely." However, this tests the cold-start performance of the agents
   rather than their behavior in a continuous, long-lived lab session. It
   avoids the hard problem of session state management and context bloat,
   which is how the real Android application actually interacts with these
   services.

Alternatives worth considering:
- Global VAD-Enabled Benchmark Mode: Instead of hardcoding explicit
  end-of-turn signals (commit / turn_complete=True), implement a strict
  open-mic flow that relies on the Foundation story's 500ms trailing
  silence. Measure how long each provider's VAD takes to realize the user
  stopped speaking. If a provider's VAD fails on pre-recorded audio, that is
  a valid benchmark finding, not a bug to be hidden.
- Enforce Post-Tool Audio Globally: Mandate the second response.create in
  OpenAI to force it to generate the spoken confirmation, producing a true
  TTFS metric. If the cost/latency of post-tool audio is a structural
  concern, add a global --disable-verbal-confirmations CLI flag to the
  runner that applies to *all* adapters simultaneously, ensuring an
  apples-to-apples baseline.
- Persistent Session Benchmarking: Rather than tearing down the WebSocket
  per turn, maintain a single connection per run (or per subset of prompts).
  Use input_audio_buffer.clear for OpenAI and equivalent resets for other
  providers. This tests session stability and context degradation, providing
  a far more accurate reflection of real-world mobile app usage than
  isolated single-turn bursts.
```

**Adoption decisions:**

- Concern #1 (VAD bypass) — **noted**, deferred to a future "vad-mode"
  comparison story. Adopting now would break the Gemini baseline retroactively
  since its VAD demonstrably drops pre-recorded audio.
- Concern #2 (TTFS inconsistency) — **adopted** alternative "Enforce
  post-tool audio globally" (mandatory second `response.create` in OpenAI).
  The CLI flag for disabling verbal confirmations is a future-work
  refinement; default behavior is now: turn is incomplete without verbal
  confirmation.
- Concern #3 (optimizer blind spot) — **addressed by adopting #2**.
- Concern #4 (pipelined contract) — **adopted**; follow-up after this story
  updates `NativeVoiceAdapter` docstring in `base.py` to make the rule
  explicit.
- Concern #5 (cold-start bias) — **considered and deferred**. Persistent-
  session benchmarking is a cross-cutting architectural change that should
  affect both adapters symmetrically; it belongs in its own story alongside
  the optimizer Epic 9 work. Noted in Risk #12.
