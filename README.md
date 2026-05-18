# voice-bench

Automated benchmarking harness for voice AI agents. Measures **tool-calling correctness** and **time-to-first-sentence latency** by streaming pre-rendered audio prompts to voice agents and scoring their tool calls.

## Quick start

```bash
# 1. Install (requires Python 3.11+ and uv)
cd voice-bench
uv sync

# 2. Set up API keys (copy and edit)
cp .env.example .env    # then fill in GEMINI_API_KEY

# 3. Generate audio fixtures (one-time, requires macOS + ffmpeg)
voice-bench gen-audio

# 4. Probe connectivity
voice-bench probe --agent gemini-live

# 5. Run smoke benchmark (5 prompts, 5 tools)
voice-bench run --agent gemini-live --tools 5 --mode smoke
```

Results are written to `results/<run-id>.jsonl` and `results/<run-id>.csv`.

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `GEMINI_API_KEY` | Yes (for gemini-live) | — | Google AI API key |
| `GEMINI_LIVE_MODEL` | No | `gemini-3.1-flash-live-preview` | Live API model ID |
| `GEMINI_VOICE` | No | `Kore` | Voice name |

## Commands

```
voice-bench probe    --agent gemini-live
voice-bench run      --agent gemini-live --tools 5|10|15|20|30 --mode smoke|full
voice-bench gen-audio                    # regenerate WAV fixtures
```

## Project structure

```
src/voice_bench/
  cli.py             # Click entry point
  models.py          # TurnTimeline, Score, ToolCallEvent, TurnResult
  tools.py           # 25 dummy tools in 5 tiers
  scoring.py         # score_turn, fuzzy arg matcher
  runner.py          # BenchmarkRunner
  adapters/
    base.py          # NativeVoiceAdapter protocol
    gemini_live.py   # Gemini Live adapter (first slice)

prompts/
  manifest.json      # 50 prompts (30 train / 10 val / 10 holdout)
  system/
    gemini-live.md   # system prompt (optimizer edits this)
  audio/say/         # pre-rendered WAVs (gitignored)

results/             # run outputs (gitignored)
```

## Scoring

Each turn is scored on:
- **(a) Tool name match** — was the right tool called?
- **(b) Arg score** — fuzzy match 0–1 (handles "on"/"true", case, ±5% numerics)
- **(c) ttf_tool_ms** — ms from end of audio to first tool call
- **(d) ttfs_ms** — ms from end of audio to first spoken audio (TTFS)

A turn **passes** when `tool_name_match=True AND arg_score ≥ 0.8`.

## Prompt splits

- **Train (p001–p030)**: visible to the optimizer loop
- **Val (v001–v010)**: keep/discard gate during optimization
- **Holdout (h001–h010)**: final reporting only — never seen by optimizer
