# Story: Two-Layer Pipeline Benchmark

**ID:** 2026-05-19-two-layer-pipeline-benchmark
**Status:** Reviewed — awaiting approval

## Prerequisites — HARD blockers

This story depends on Stories 1, 2, and 3 having shipped. Specifically:
- **Story 1**: 6 text adapters with `model_kind="text"`,
  `ttf_request_to_call_ms` populated, the adapter registry pattern.
- **Story 2**: dashboard renders `tool_mode`-tagged rows and has a
  comparison view (Phase 2B). If 2B is not done, pipeline data is
  recorded; the dashboard view is a follow-up.
- **Story 3**: `DummyTool.category`, `META_TOOLS`,
  `manifest_routing.json`, `score_routing_turn`, `CATEGORY_TO_META_TOOL`.

**Also a prerequisite of this story specifically**: enable and verify
input transcription on both voice adapters. Codex flagged that
`gemini_live.py:104` and `openai_realtime.py:70` do not currently
configure transcription, only listen for transcripts if they happen
to arrive. This story must:
1. Add `input_transcription` to `GeminiLiveAdapter._build_config`.
2. Add transcription config to `OpenAIRealtimeAdapter._session_config`
   (the `audio.input.transcription = {model: "whisper-1"}` field).
3. Verify with a probe that `transcripts["user"]` is non-empty after a
   real audio turn. Required before any pipeline run.
**Created:** 2026-05-19

## Goal

End-to-end benchmark of the proposed multi-agent voice architecture:

```
Voice model (3 meta-tools)         Text model (5-15 tools in bucket)
    └── picks chemistry_tools ──→  picks set_zoom(level=3.0) ──→ done
```

Measure both halves at once: how often does the final, real tool get
called correctly, and what's the combined per-turn latency. This is
the story that answers "is the two-layer architecture actually
better than a single voice agent with 30 tools?"

## Goal — concretely

For each (voice_model, text_model) pair the user wants to test:
1. **End-to-end accuracy**: `(correct_bucket × correct_subtool_with_args)`
2. **Per-hop latency**: voice hop (ms) + text hop (ms) + total (ms)
3. **Two baselines** to compare against:
   - **Single-layer voice (30 tools)**: existing voice-bench data.
     Two-layer should beat this or it's not worth the latency.
   - **ASR-then-text-only**: ASR (Whisper / Deepgram) on the audio,
     transcript piped to a single text model with all 30 tools loaded.
     **Gemini correctly flagged** this missing baseline — it may
     beat two-layer voice on both axes (no audio→tool latency
     penalty, no 3-bucket single-point-of-failure). If it does, the
     production answer isn't two-layer voice — it's pipelined ASR+LLM.

## Context

Stories 1-3 produce the inputs this story needs:
- **Story 1**: text adapters (Sonnet, Opus, GPT-5, GPT-4o, Gemini Pro,
  Gemini Flash) with `model_kind="text"` and `ttf_request_to_call_ms`.
- **Story 2**: dashboard splits voice vs text latency and can render
  comparison views.
- **Story 3**: meta-tools (`chemistry_tools`, `app_control`,
  `assistant`), routing scorer, routing manifest, and the verdict on
  whether the 3-bucket partition is workable.

What this story adds on top:
- A new "pipeline" run mode that orchestrates two adapter calls per
  prompt.
- A new pipeline scorer that combines routing + sub-tool scores.
- ASR-transcript handling so the text adapter sees what the voice
  model actually heard, not the manifest's clean text (Gemini flagged
  this in Story 1 — Story 4 closes the gap).

### Why ASR transcripts matter here

In production, the text sub-router receives a transcript of what the
voice model heard, not the original utterance. Story 1's text
benchmark uses clean manifest text and over-estimates accuracy. This
story restores the production reality by passing the voice adapter's
`transcripts["user"]` field as the text adapter's `prompt_text`.

If the voice model doesn't return a usable input transcript (e.g.
Gemini Live doesn't always emit `input_transcription`), the fallback
is the clean manifest text with a row-level `transcript_source` flag
so we can quantify the gap.

## Acceptance Criteria

- [ ] **New CLI mode**: `voice-bench run --mode pipeline --voice-agent <name>
      --text-agent <name>` runs the two-layer pipeline. Both agents are
      required.
- [ ] **Pair sweep**: smoke run for at least 4 pairs — chosen to span
      the latency/accuracy spectrum based on Story 1's findings:
      - `(gemini-live, claude-sonnet)` — cheap+fast both layers
      - `(gemini-live, claude-opus)` — strong text router
      - `(openai-realtime, gpt-5)` — same-provider pair
      - `(openai-realtime, gemini-pro)` — cross-provider strong/strong
      Final pair list is a per-run choice; the harness must support
      any combination.
- [ ] **Pipeline scorer** in `scoring.py`:
      `score_pipeline_turn(voice_result, text_result, expected_tool,
      expected_args, expected_category) → PipelineScore`. Score fields:
      `bucket_match` (bool), `subtool_match` (bool), `arg_score` (0-1),
      `end_to_end_pass` (bool — both bucket AND subtool AND
      arg_score≥0.8), `voice_hop_ms`, `text_hop_ms`, `total_ms`.
- [ ] **Transcript handling**: if voice adapter emits a non-empty
      `transcripts["user"]`, use it as `prompt_text` for the text
      adapter. Otherwise fall back to `prompt["text"]` from the
      manifest and set `transcript_source="manifest_fallback"` on the
      row.
- [ ] **Two comparison baselines**:
      (a) **Single-layer voice (30 tools)**: re-uses existing
          voice-bench data joined on
          `(voice_agent, prompt_id, manifest, tool_count=30)`.
      (b) **ASR-then-text**: new mini-adapter `AsrPlusTextAdapter`
          (Step 8 below) that runs Whisper-1 over the same audio,
          then passes the transcript to the chosen text model with
          all 30 tools loaded. Records latency for ASR hop + text hop.
      Dashboard shows three columns per prompt: single-layer voice,
      two-layer voice+text, ASR+text. **This baseline matters because
      Gemini flagged that the two-layer voice architecture may be
      worse on both axes (slower + less accurate) than just doing
      ASR+text.**
- [ ] **Latency budget check**: every row records voice hop, text hop,
      total. The dashboard flags rows where `pipeline_wall_ms > 2000`
      (the "feels-fast-in-voice-UX" ceiling that Gemini correctly
      flagged as the central question; 2000 ms not 3000 ms because
      voice UX research consistently puts the awkwardness threshold
      at 2s, not 3s).
- [ ] **Smoke + sweep**: smoke run of the 4 pairs at 10 sub-tools/bucket
      completes in <30 min. Full sweep (each pair × 5/10/15 sub-tools)
      runs in <2 hours.
- [ ] All existing tests pass; new tests for the pipeline scorer.

## Implementation Plan

### Step 0 — Enable transcription on voice adapters

Before any pipeline code, modify the two voice adapters:

**`gemini_live.py`** — add to `_build_config`:
```python
return {
    "response_modalities": ["AUDIO"],
    "system_instruction": system_prompt,
    "tools": gemini_tools,
    "input_audio_transcription": {},  # enables transcript event stream
    "output_audio_transcription": {},  # optional but useful for debug
}
```

**`openai_realtime.py`** — add to `_session_config`:
```python
"audio": {
    "input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "turn_detection": None,
        "transcription": {"model": "whisper-1"},  # NEW
    },
    ...
}
```

Run a one-shot probe: voice adapter on a single audio prompt with
transcription enabled; assert `result.transcripts["user"]` is
non-empty before declaring this step done. Both providers are known
to support this; the bug is just that the adapters never asked for
transcripts.

### Step 1 — Pipeline runner mode

Extend `cli.py:57-59` mode choices with `"pipeline"`. Add two new
flags: `--voice-agent` and `--text-agent`. When `--mode pipeline` is
set, both are required and `--agent` is ignored. Add validation in
`cli.py` for the (voice, text) pairing.

In `runner.py`, add a new function `run_pipeline_benchmark` parallel
to `run_benchmark`. Reuse infrastructure but **do NOT** reuse the
single-adapter `requires_audio` / `_pick_audio()` shortcut at
`runner.py:97-109` — pipeline mode always loads audio for the voice
hop (provided the voice adapter requires audio) and never for the text
hop.

**Pipeline turn-state record** (Codex flagged that two adapters need
shared state):

```python
@dataclass
class PipelineTurn:
    pipeline_turn_id: str   # uuid for the whole pair-turn
    voice_turn_id: str      # uuid for the voice hop
    text_turn_id: str       # uuid for the text hop (None if no-route)
    prompt_id: str
    voice_agent: str
    text_agent: str
    manifest_text: str      # from manifest, original
    transcript_text: str    # from voice adapter's transcripts["user"]
    transcript_source: str  # "voice_transcript" | "manifest_fallback"
    called_bucket: Optional[str]
    voice_result: TurnResult
    text_result: Optional[TurnResult]
    pipeline_score: PipelineScore
    ts_pipeline_start: float
    ts_pipeline_end: float
```

Per prompt:

```python
voice_result = await voice_adapter.run_turn(
    audio_wav_path=audio_path,
    tools=META_TOOLS,          # the 3 buckets from Story 3
    system_prompt=routing_system_prompt,
    turn_id=...,
    prompt_id=...,
    timeouts=...,
    prompt_text=prompt["text"],
)
# Determine called bucket
called_bucket = voice_result.tool_calls[0].tool_name if voice_result.tool_calls else None

# Pull transcript or fall back
transcript = voice_result.transcripts.get("user", "").strip() or prompt["text"]

if called_bucket is None:
    # Voice failed to route — pipeline ends here
    text_result = None
    pipeline_score = score_no_route(...)
else:
    bucket_tools = SUBTOOLS_BY_BUCKET[called_bucket]
    text_result = await text_adapter.run_turn(
        audio_wav_path=None,
        tools=bucket_tools,         # sub-tools in the chosen bucket
        system_prompt=subtool_system_prompt,
        ...,
        prompt_text=transcript,
    )
    pipeline_score = score_pipeline_turn(voice_result, text_result,
                                          prompt, called_bucket)
```

The sub-tool subset comes from `tools.py`'s category field (Story 3):
`SUBTOOLS_BY_BUCKET = {bucket: [tools for t in ALL_TOOLS if t.category == bucket]}`.

**JSONL schema** for pipeline rows (Codex flagged the current
dashboard parser would drop these):

```json
{
  "run_id": "...",
  "tool_mode": "pipeline",
  "pipeline_turn_id": "...",
  "prompt": {...},
  "voice_agent": "gemini-live",
  "text_agent": "claude-sonnet",
  "voice_result": {...},
  "text_result": {...} | null,
  "transcript_source": "voice_transcript" | "manifest_fallback",
  "manifest_text": "...",
  "transcript_text": "...",
  "pipeline_score": {...}
}
```

`scripts/build_dashboard.py:_rescore` branches on `tool_mode`:
- `"accuracy"` (default) → existing `score_turn` path.
- `"routing"` → `score_routing_turn` (Story 3).
- `"pipeline"` → `score_pipeline_turn`. Reconstructs both
  `TurnResult`s and re-derives the pipeline score.

**CSV schema** — define fixed pipeline fieldnames at runner-init so
`csv.DictWriter.writerows()` (`runner.py:163`) doesn't crash on
no-route rows that lack text-hop fields. Add a
`PIPELINE_CSV_FIELDS = [...]` constant in `runner.py`; missing fields
write empty strings.

### Step 2 — Pipeline scorer

New `score_pipeline_turn` in `scoring.py`:

```python
@dataclass
class PipelineScore:
    bucket_match: bool
    subtool_match: bool
    arg_score: float
    end_to_end_pass: bool
    # Latency — Codex flagged these must be separate concepts
    voice_decision_ms: Optional[int]   # voice ttf_tool_ms (excludes setup/audio upload)
    text_decision_ms: Optional[int]    # text ttf_request_to_call_ms
    pipeline_wall_ms: Optional[int]    # ts_pipeline_end - ts_pipeline_start, includes setup
    no_route: bool
    wrong_route: bool                  # bucket called but != expected
    transcript_source: str
    # Mirrors of Score's fields for dashboard
    tool_name_match: bool
    no_call_made: bool

def score_pipeline_turn(voice_result, text_result, prompt,
                        called_bucket) -> PipelineScore:
    ...
```

Two latency concepts:
- `*_decision_ms`: pure model decision time, comparable to the
  Story-1 per-model numbers (excludes session setup).
- `pipeline_wall_ms`: real wall-clock time from "started routing" to
  "tool fired." This is what production UX feels. The runner records
  `ts_pipeline_start` immediately before `voice_adapter.run_turn()`
  and `ts_pipeline_end` immediately after `text_adapter.run_turn()`.

Two failure modes captured separately:
- `no_route`: voice didn't pick a bucket. Counts as full pipeline failure.
- `wrong_route`: voice picked the wrong bucket; the text adapter ran
  on sub-tools that can't include the expected_tool. Pipeline fails
  but the text hop still runs and its latency is recorded.

**Dashboard baseline-join keys** (Codex flagged `(voice_agent,
prompt_id)` alone is ambiguous):

The single-layer comparison joins pipeline rows against existing
voice-bench accuracy rows on the full key
`(voice_agent, prompt_id, manifest, tool_count=30)`. If multiple
baseline runs exist for the same key, pick the most recent by
`run_ts` (Story 2 adds this field). Document the picked baseline
`run_id` on each pipeline row for auditability.

If no baseline exists for a given voice_agent at 30 tools, the
comparison cell shows "no baseline" rather than silently picking a
different tool count.

### Step 3 — Transcript availability sanity check

Done in Step 0; this step is just the verification that the
end-to-end pipeline IS actually receiving transcripts (not
manifest fallback) for the bulk of prompts. Smoke metric: ≥80% of
voice-hop turns produce a non-empty `transcripts["user"]`. If less,
the routing benchmark is effectively the clean-text benchmark and
we lose the point of this story.

### Step 4 — Pipeline-aware dashboard

Extend `scripts/build_dashboard.py`:
- New `tool_mode="pipeline"` discriminator on each row.
- Pipeline rows have BOTH voice_agent and text_agent fields; dashboard
  joins them into a "pair" badge.
- New "Pipeline vs single-layer" comparison view (in Phase 2B if not
  already in Story 2):
  - Pick a voice model.
  - Table: voice-only accuracy at 30 tools, pipeline accuracy with
    several text models, delta per pair.
  - Latency comparison: voice-only `ttf_tool_ms` vs pipeline `total_ms`.

If Story 2 (Dashboard v2) Phase 2B hasn't shipped when this lands,
the pipeline rows are recorded but the comparison view is a follow-up.

### Step 5 — Smoke run

```bash
voice-bench run --mode pipeline \
    --voice-agent gemini-live --text-agent claude-sonnet \
    --manifest manifest_routing
```

Pick a 10-prompt subset for smoke. Verify:
- Voice hop fires, returns a bucket.
- Text hop fires with the right sub-tool subset and returns a tool call.
- Pipeline score correctly captures bucket + subtool + arg.
- Total latency is recorded.

### Step 6 — Full sweep

The user's interest is "which voice+text combination is best." The full
sweep is the 4 candidate pairs × 5/10/15 sub-tools-per-bucket × the
30-prompt eval manifest:

```bash
for voice in gemini-live openai-realtime; do
  for text in claude-sonnet claude-opus gpt-5 gpt-4o gemini-pro gemini-flash; do
    voice-bench run --mode pipeline \
        --voice-agent $voice --text-agent $text \
        --manifest manifest_routing
  done
done
```

12 pairs × ~30 prompts × ~3s per prompt (1.5s voice + 1.5s text) ≈
20 min. Run serially; if the API providers rate-limit, halve the pairs
and pick the most-promising 6.

### Step 8 — ASR-then-text baseline adapter

New file `src/voice_bench/adapters/asr_plus_text.py`. Composite
adapter:
- Takes the audio file, runs it through `openai.audio.transcriptions.create(model="whisper-1")` to get a transcript.
- Wraps an existing text adapter (passed via constructor).
- Returns a `TurnResult` whose `timeline` has two new timestamps:
  `ts_asr_start`, `ts_asr_end`, and a new derived field
  `asr_decision_ms`. Plus the wrapped adapter's normal timeline
  fields with offsets adjusted so `ttf_request_to_call_ms` measures
  "transcript to tool call."

CLI: `voice-bench run --mode asr_plus_text --text-agent claude-sonnet --tools 30`.
This is a single-layer adapter from the runner's perspective — no
pipeline-runner changes needed.

The output of this baseline runs through the existing accuracy scoring
(not the pipeline scorer) — it's a single-agent architecture, just
with an ASR pre-step. Dashboard treats it as a regular accuracy run
with `agent="asr+<text_agent>"`.

### Step 7 — Document findings

Final findings doc `docs/findings/pipeline-architecture.md`:
- Per-pair accuracy + latency matrix.
- Comparison to single-layer voice-bench at 30 tools.
- The recommendation: production architecture should use pair X if
  cost matters / pair Y if accuracy matters / pair Z if latency
  matters.
- Open question: what's the next layer of optimization (cache the
  bucket call, parallel speculative execution, hybrid routing where
  the voice agent answers from context for trivial prompts).

## Risks & Open Questions

1. **Latency stacking**. Per the architecture discussion, two layers
   means voice hop + text hop. If voice ~700ms (Gemini Live's measured
   ttf_tool) + text ~1500ms (Claude Sonnet average), we're at 2.2s
   before any tool actually fires. Voice UX feels slow above 3s and
   awkward above 2s. **Mitigation**: measure honestly; if the numbers
   exceed 3s for all pairs, the architecture is wrong and Story 4's
   output is "abandon two-layer in favor of dynamic single-layer tool
   loading." That's a valuable finding.

2. **ASR transcript fidelity varies by provider**. Some voice models
   emit transcripts only on request; some emit partial transcripts;
   some are accuracy-poor on technical terms ("ISO" → "I.S.O." →
   transcript artifact). The fallback to manifest text masks
   transcript-quality differences between voice models. **Mitigation**:
   capture both the transcript AND the manifest text on every row; a
   follow-up story can compute "transcript-vs-manifest delta" as a
   voice-model quality metric.

3. **Voice routing is itself stochastic**. The voice model may pick
   `chemistry_tools` 99% of the time and `app_control` 1%; we'd never
   see the rare failure in a 30-prompt sweep. **Mitigation**: each
   prompt is run once per pair — the noise is acceptable for the
   "which architecture wins" question. If we want statistical
   significance, repeat the sweep 3× and average. Documented but not
   gated.

4. **The text sub-router sees only the sub-tools in the chosen
   bucket** — what if the voice routes to the wrong bucket? The
   sub-router will pick the best wrong tool from its (wrong) subset.
   Pipeline score correctly captures this as `bucket_match=False,
   subtool_match=False, arg_score=0.0`. The architecture's
   resilience to wrong routing is a separate question; for the
   benchmark, wrong route = failure.

5. **Cost**. Each pipeline run hits both APIs. The 12-pair sweep with
   ~30 prompts each = 360 voice + 360 text API calls. At Gemini
   Live's $0.0001/turn + Claude Sonnet's $3 / 1M tokens × ~1000
   tokens/turn = $0.003/turn, total cost ≈ $2-5 per sweep. Sustainable.

6. **Cache/speculative-execution optimizations are out of scope**.
   A real production architecture might fire the text adapter
   speculatively against multiple buckets while the voice model
   resolves, then cancel the losers. We measure the naive serial
   architecture here; speculative execution is a follow-up.

7. **Manifest coverage**. `manifest_routing.json` (Story 3) has
   ambiguous and out-of-route prompts. Pipeline scoring must handle
   ambiguous prompts (any of N expected_categories acceptable) and
   negative prompts (no_route is correct). The scorer at Step 2
   needs explicit handling.

## Out of Scope

- Production wiring into SciSymbioLens.
- Parallel speculative execution (fire all 3 buckets, cancel losers).
- Caching frequent bucket routes.
- Streaming the text adapter's output back through the voice model
  for spoken confirmation. The benchmark records "tool fired
  successfully" not "user heard the response."
- Cross-bucket spillover handling (sub-router admitting it can't
  serve the request and re-routing).
- A third layer (e.g. sub-sub-routers within `assistant`).
- **Semantic tool retrieval (RAG for tools)** — Gemini suggested
  this as an alternative to static buckets: embed the user
  utterance, retrieve top-K most-relevant tools from a vector
  index, feed only those to a single agent. Worth a separate
  story (Story 5 candidate) if Stories 1-4 conclude the two-layer
  architecture is too slow.
- **Dynamic context-aware tool loading** — Gemini also suggested
  loading tools per-session based on what UI surface the user is
  on. Production-engineering territory; the benchmark can simulate
  it by manipulating the manifest, but the real win is in the app
  layer, not the harness.

## Reviewer Feedback

### Codex (round 1)

**Critical issues raised:**
1. Two adapters need explicit shared state — runner doesn't have it.
2. Voice adapters don't currently enable input transcription, so
   `transcripts["user"]` will be empty.
3. JSONL schema is single-result; dashboard parser drops pipeline
   rows.
4. CSV `DictWriter` will crash if early rows lack text-hop fields.
5. Audio fixture lookup is single-adapter, needs pipeline-specific
   handling.
6. Dashboard baseline join on `(voice_agent, prompt_id)` is
   ambiguous — need full key with manifest, tool count, run selection
   rule.
7. Latency: sum of `ttf_tool_ms` undercounts wall-clock. Record
   `*_decision_ms` AND `pipeline_wall_ms`.
8. Story 3 primitives are prerequisites — make hard blockers.

**Nice-to-have raised:**
- `pipeline_turn_id` with child voice/text turn ids.
- Store both manifest text and transcript text on every row.
- Tests for no-route, wrong-route, negative, ambiguous prompts.
- Fixed pipeline CSV fieldnames.

**Resolution:**
- Added Prerequisites section as HARD blockers (including
  transcription enablement).
- Added Step 0 to enable input transcription on both voice adapters.
- Added `PipelineTurn` shared-state dataclass.
- Defined pipeline JSONL schema explicitly.
- Defined pipeline CSV fieldnames.
- Latency split into decision-time vs wall-clock — both recorded.
- Dashboard join key fully specified with tie-breaking rule.

### Gemini Pro (round 2)

**Architectural concerns raised:**
1. Serial latency floor (~2.2s) is a UX dealbreaker for trivial
   local actions ("toggle flash"). Using native voice merely as a
   slow bucket-sorter defeats its low-latency advantage.
2. Voice-model ASR transcripts are unreliable "exhaust" — we'd be
   measuring provider-specific ASR quirks, not architecture viability.
3. The 3-bucket assumption creates a single point of failure at the
   top of the funnel; downstream sub-router quality can't save it.

**Alternatives suggested:**
- **Fast ASR + Strong Text Model** baseline (Whisper/Deepgram →
  Sonnet with all 30 tools). The missing comparison.
- **Semantic tool retrieval** (embeddings + top-K).
- **Dynamic context-aware tool loading** (load tools per UI screen).

**Resolution:**
- **Adopted in scope**: ASR-then-text baseline (Step 8) — this is
  the critical missing comparison. If it beats two-layer voice on
  both accuracy and latency, the architecture conclusion changes
  entirely.
- **Adopted as conclusion criterion**: the "is two-layer better
  than the alternatives" question becomes the explicit deliverable
  of Step 7. Two-layer is on trial, not assumed.
- **Latency ceiling tightened**: 2000 ms (not 3000 ms) per voice
  UX research; Gemini correctly flagged 3000 ms was too generous.
- **Deferred**: semantic tool retrieval and dynamic tool loading
  acknowledged as Story-5/6 candidates if Stories 1-4 conclude the
  two-layer architecture is too slow.

## Revision History

- 2026-05-19 — Initial draft
- 2026-05-19 — Round 1: Codex feedback (all 8 critical, 4 of 4
  nice-to-haves adopted)
- 2026-05-19 — Round 2: Gemini Pro feedback (3 concerns; ASR+text
  baseline adopted in scope, semantic retrieval and dynamic loading
  deferred to Stories 5-6 candidates)
