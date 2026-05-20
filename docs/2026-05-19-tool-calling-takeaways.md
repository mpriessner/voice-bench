# Tool-Calling Model Benchmark — Takeaways (2026-05-19)

This document summarises what we learned from running two complementary benchmarks across Claude, Gemini, and OpenAI model families.

## The two benchmarks (and what they each measure)

### `agent-tool-lab` — per-tool correctness
- 203 natural-language prompts across 16 chemistry / lab tools (text API only).
- Asks: *"Did the model pick the right tool AND produce the right answer string?"*
- Where the bar is set: realistic SciSymbioLens voice-agent calculations.

### `voice-bench` — scaling sweep
- 50 camera-control prompts × multiple tool-count buckets (5 / 10 / 15 / 20 / 30 / 40 / 50).
- Asks: *"Does accuracy hold up as the tool list grows, including under live audio (Live API / Realtime API)?"*
- Where the bar is set: simpler tool routing — "turn on flash", "show grid overlay".
- Static dashboard: `results/dashboard.html` (HTML + JS, no server needed).

## Exact model identities (the labels in the heatmap)

| Heatmap label | **Actual model ID** | Family |
|---|---|---|
| `claude-opus` | `claude-opus-4-7` | Claude — text API |
| `claude-sonnet` | `claude-sonnet-4-6` | Claude — text API |
| `gemini-3-flash` | `gemini-3-flash-preview` | Gemini — text API (added 2026-05-19) |
| `gemini-flash` | `gemini-3.1-flash-lite` | Gemini — text API |
| `gemini-live` | `gemini-3.1-flash-live-preview` | Gemini — Live (audio) API |
| `gemini-pro` | `gemini-3.1-pro-preview` | Gemini — text API |
| `gpt-4o` | `gpt-4o` | OpenAI — text API |
| `gpt-5` | `gpt-5` | OpenAI — text API |
| `gpt-text` | `gpt-4o` *(duplicate alias — same model as gpt-4o)* | OpenAI — text API |
| `openai-realtime` | `gpt-realtime` | OpenAI — Realtime (audio) API |

## Headline findings

### 1. For per-tool correctness on real chemistry prompts (agent-tool-lab, 203 cases)

| Model | Pass% | Cost (one full run) |
|---|---:|---:|
| **Claude Sonnet 4.6** | **97.0%** | $10.80 |
| Gemini 3.1 Pro Preview | 95.1% | $4.95 |
| Gemini 3 Flash Preview | 89.7% | $1.28 |
| Gemini 3.1 Flash-Lite | 89.7% | $0.62 |

- **Sonnet 4.6 wins on accuracy** (+2pp over Pro) but **2.2× the cost**.
- **Gemini 3.1 Pro is the best value point** — only 2pp below Sonnet, less than half the cost.
- **Both Flash tiers tie at 89.7%** in the chemistry domain. Flash-Lite is the better-value Flash (half the cost of 3 Flash for identical accuracy).
- The hardest tool for every model is `spectrophotometry_calculator` (Sonnet/Pro/3 Flash all plateau at 83%, Flash-Lite collapses to 58%). That tool's routing description needs tightening — independent of model choice.

### 2. For tool-count scaling on simpler prompts (voice-bench heatmap)

These are the actual cells from the heatmap, per agent and tool count. Bold = standout. *PROVIDER_ERROR rows are filtered out — they were API outages, not model failures.*

| Agent | 5t | 10t | 15t | 20t | 30t | 40t | 50t |
|---|---:|---:|---:|---:|---:|---:|---:|
| `claude-sonnet` | 100 | 100 | 100 | 100 | 100 | 100 | **94** |
| `claude-opus` | 100 | 100 | 100 | **93** | **72** | — | — |
| `gemini-3-flash` (text) | **100** | **100** | **100** | **100** | **100** | **100** | **100** |
| `gemini-flash` (text) | 100 | 100 | 100 | 100 | 99 | 98 | 99 |
| `gemini-pro` (text) | 100 | 100 | 100 | 100 | 100 | 98 | 99 |
| **`gemini-live-v2`** (audio, gemini-3.1-flash-live-preview) | **100** | **100** | **98** | **98** | **100** | — | — |
| `gemini-live` (audio, legacy / unfixed) | 76 | 65 | 70 | 70 | **51** | — | — |
| `gpt-5` (text) | 100 | 100 | 100 | 100 | 99 | 98 | 99 |
| `gpt-4o` / `gpt-text` (text) | 100 | 100 | 100 | **93** | **87** | — | — |
| **`openai-realtime-v2`** (audio, gpt-realtime-2) | **100** | **98** | **98** | **98** | **100** | **98** | **98** |
| `openai-realtime` (audio, legacy gpt-realtime) | 68 | 55 | 67 | 67 | **54** | — | — |

**Critical finding**: the modern voice models (`openai-realtime-v2` = `gpt-realtime-2`, `gemini-live-v2` = `gemini-3.1-flash-live-preview`) are both 98–100% across the entire range, **completely refuting the earlier "voice models degrade past 15 tools" headline**. That headline was an artifact of (a) the older `gpt-realtime` model with broken adapter config, (b) PROVIDER_ERROR API outages, and (c) v1/v2 runs being merged under one label.

### 3. Patterns that emerge across both benchmarks

**Top performers at scale (text APIs, 30+ tools):**
- Claude Sonnet 4.6 — flat at 100% to 40t, only drops to 94% at 50t
- gpt-5 — flat at ~99% to 50t
- Gemini 3.1 Pro Preview — flat at 100% to 30t, ~99% to 50t
- Gemini 3.1 Flash-Lite — flat at ~99% to 50t (surprisingly resilient)
- **Gemini 3 Flash Preview — perfect 100% across all 7 tool counts** (newly added today)

**Models that degrade as tool count grows:**
- Claude Opus 4.7 — drops to 72% at 30t (counter-intuitive, given size)
- gpt-4o — drops to 87% at 30t
- **Legacy voice models degrade hardest:** `gemini-live` (legacy data) from 76% (5t) to 51% (30t); `openai-realtime` (legacy `gpt-realtime`) from 68% to 54%. ⚠ **But the modern voice models (`gemini-live-v2` and `openai-realtime-v2`) do NOT show this degradation** — they hold 98–100% all the way to 30+ tools. The "voice degrades" story applies only to the older models, not the current generation.

**Sweet spots:**
- Routine voice tool calling (≤15 tools): live APIs are usable but already weaker; text APIs are essentially flawless.
- Larger tool surfaces (30+ tools): only Sonnet, Pro, gpt-5, gemini-flash/3-flash, and Flash-Lite stay reliable.
- For SciSymbioLens-style chemistry: Sonnet 4.6 is the gold standard, 3.1 Pro is the cost-effective near-equal.

## What to use for which workflow

| Workflow | Recommended model | Why |
|---|---|---|
| Production voice agent (text-routed) | **Claude Sonnet 4.6** *or* **Gemini 3.1 Pro Preview** | Both flat at 100% to 30t; Pro is half the cost |
| Production voice agent (Live API) | **`gemini-3.1-flash-live-preview` (currently free) or `gpt-realtime-2`** | Both hold 98–100% to 30 tools. Earlier "voice degrades" finding was a measurement artifact from the older `gpt-realtime` model and broken adapter config. |
| Batch / offline tool calling | Gemini 3.1 Flash-Lite | Same chemistry-domain accuracy as 3 Flash at half the cost |
| Maximum accuracy, cost-no-object | Claude Sonnet 4.6 | +2pp over Pro at 2.2× the cost |
| Maximum scale (50+ tools) | gpt-5, claude-sonnet, gemini-pro, gemini-flash | All ≥94% at 50 tools |

## Open follow-ups

1. **`spectrophotometry_calculator` is the model-agnostic weak point** — only 83% even for Sonnet. Re-write its routing description and re-run; this is the single highest-leverage fix.
2. **`gpt-text` and `gpt-4o` are the same model** under two names in `adapters/registry.py:44`. De-dupe for clarity.
3. **Cost estimator is 3× low** (predicted $1.40 for Sonnet, actual $10.80). Bump `_AVG_INPUT_TOKENS` from 800 → ~12,000 in `agent-tool-lab/agent/cost.py` to reflect the tool-schema overhead.
4. **`gemini-3-flash-preview` is perfect on voice-bench but only 90% on chemistry tools** — the failure mode is domain-specific (chemistry calculations), not scaling. Worth investigating whether the gap closes with tighter system-prompt rules.

## Voice-API hourly cost comparison

For real-time bidirectional audio (1 hour of user input + 1 hour of assistant output):

| Model | Audio in | Audio out | **~Per hour** |
|---|---:|---:|---:|
| `gpt-realtime-2` (current) | $32/M tokens | $64/M tokens | **~$5.76** |
| `gpt-realtime` (legacy) | $40/M | $80/M | ~$7.20 |
| Gemini 2.5 Flash Live (paid) | $3/M | $12/M | **~$1.35** |
| Gemini 3.1 Flash Live Preview | **Free (preview)** | **Free (preview)** | **$0** (free tier) |
| `gpt-realtime-2` cached input | $0.40/M | n/a | up to 80× discount on stable system prompts |

- Token-to-time conversion: OpenAI is 10 tok/s user audio, 20 tok/s assistant audio; Gemini is 25 tok/s for both.
- OpenAI is **~4× more expensive** than paid Gemini Flash Live for equivalent audio time.
- The Gemini 3.1 Flash Live preview tier is free but has a 15-min session cap and other restrictions.

## Diverse-tool benchmark — implementation status (2026-05-19)

The needle-in-haystack design flaw was identified: the current 50-tool sweep only exercises 5 distinct tools. A fairer "does the model actually call the right tool from a genuinely diverse set?" experiment has been implemented:

**Implemented (ready to run):**
- Negative-prompt scoring bug fixed (`score_turn` now correctly passes negative prompts that produce no tool call).
- `--no-strict-routing` CLI flag added — sets `tool_choice="auto"` instead of `"required"`, enabling negative-prompt evaluation on OpenAI Realtime.
- `manifest_diverse.json` generated: 20 positive prompts (1 per distinct tool, covering all 7 parameter shapes) + 6 negative prompts. IDs prefixed `d_` to avoid collision with the standard manifest.
- `scripts/validate_manifest_diverse.py` — validates uniqueness, tool coverage, and negative prompt count.
- Audio output namespaced under `prompts/audio/<voice>/diverse/` to prevent cache collisions.
- `benchmark_mode` field added to every JSONL row (`"needle"` or `"diverse"`).
- Dashboard: "Benchmark" filter toggle (needle / diverse) + `false_positive` failure category.

**To run the diverse sweep:**
```bash
# Generate audio (one-time)
voice-bench gen-audio --manifest prompts/manifest_diverse.json

# Run sweep per agent × tool count
voice-bench run --agent openai-realtime-v2 --tools 3 --mode diverse \
    --manifest manifest_diverse --no-strict-routing
voice-bench run --agent gemini-live-v2 --tools 3 --mode diverse \
    --manifest manifest_diverse
# Repeat for --tools 5 10 15 20

# Rebuild dashboard
python scripts/build_dashboard.py && open results/dashboard.html
```

## What changed in this session vs the earlier dashboard

- **`PROVIDER_ERROR` rows are now filtered** from the heatmap (script: `scripts/build_dashboard.py`). They were API outages, not model failures.
- **v1 and v2 are now distinct rows** in the heatmap. Previously both were merged under the v1 name.
- **The adapter no longer sends `reasoning.effort` to legacy models** that don't support it (script: `src/voice_bench/adapters/openai_realtime.py`). This was silently failing every legacy call.
- **`gemini-3-flash` is a new row** representing `gemini-3-flash-preview` (text API) — added 2026-05-19.

## Where the data lives

- **agent-tool-lab Streamlit:** http://localhost:8502 (Reliability Matrix page)
- **voice-bench HTML dashboard:** `file:///Users/mpriessner/windsurf_repos/voice-bench/results/dashboard.html`
- Per-run reports: `agent-tool-lab/bench/reports/<run-id>/SUMMARY.md` and `voice-bench/results/*.{csv,jsonl}`
