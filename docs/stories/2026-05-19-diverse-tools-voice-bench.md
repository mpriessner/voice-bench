# Story: Diverse-Tool Voice-Bench Experiment

**Status:** v3 — revised after both Codex (implementation) and Gemini (architectural) reviews. Awaiting user approval.
**Date:** 2026-05-19
**Author:** Agent_Tools session
**Why now:** The current 50-tool benchmark loads 50 tool schemas but only tests 5 distinct expected tools (all tier-1 toggles), which is a *needle-in-haystack* test, not a *use-all-N-tools* test. The 98% scores misrepresent what voice agents can really do at scale. We need a fairer benchmark where N tools loaded → N distinct expected tools tested.

## v3 changes (after Gemini architectural review)

Gemini correctly diagnosed that the v2 plan was over-engineered. Four major simplifications adopted:

1. **`tier_min` filtering is redundant.** The existing `run_benchmark` already filters prompts by `expected_tool in loaded_tool_names` at `runner.py:103`. If we author one prompt per tool and load N tools via `load_tools(N)`, the runner naturally emits N matching prompts. Negatives (with `expected_tool=null`) bypass that filter and run at every tier. **No new filter logic needed.** Codex's finding #4 was technically valid but the *solution* is to use what's already there, not add `tier_min`.

2. **No new `tools_diverse.py` module.** Instead, **upgrade the existing tools in `tools.py`** so the existing 50-tool universe has diverse parameter shapes (mix of boolean / int / float / enum / string / mixed). This preserves the SciSymbioLens-Android domain (the benchmark's actual purpose) and avoids forking the codebase. Tools 1–20 stay roughly as-is; tiers 4–7 get audited for parameter-shape diversity.

3. **No new manifest file.** Author the new prompts in the **existing `gen_manifest_v2.py` pipeline** with a new flag `--mode diverse`. Same audio fixtures, same generator, same structural guarantees. Output goes to `manifest_diverse.json` but the *generation* path is unified.

4. **No new dashboard page.** Extend the existing `dashboard.html` heatmap with a **mode selector** (toggle: `needle` / `diverse`) — same heatmap, different data slice. Side-by-side comparison becomes a click, not a separate browser tab. Adds the `false_positive` failure kind to the stacked-bar legend.

5. **`tool_choice="auto"` becomes the default for both adapters**, with a `--strict-routing` opt-in flag to restore the legacy `required` behaviour. This means the existing benchmark also stops conflating "did the model decide to call a tool" with "did it pick the right one." That's a real upgrade independent of diverse-mode.

6. **Tiers extended to 30 / 40 / 50** to match the existing scaling sweep. The user originally asked for 3/5/10/15/20 — we deliver those plus the higher tiers so a single heatmap shows the whole story. Setting up the higher tiers is essentially free once one-prompt-per-tool is in place.

Cumulative effect: ~50% fewer new files, no parallel codepath, one source of truth, and the existing benchmark gets fixed along the way. The user's actual ask (compare voice agents at 3–20 tools with N prompts → N tools) is delivered as a strict subset.

## 0. Codex review summary (v1 → v2)

Codex identified six blocking issues; all addressed below. Six nice-to-haves; pragmatic ones folded in, others noted in Open Questions. Raw critique appears under **Reviewer Feedback / Codex** at the bottom.

The six blockers and their resolution:

1. **Negative-prompt scoring is structurally broken** — `score_turn` returns `tool_name_match=False` for negatives even with zero calls, so `Score.passed` (which requires `tool_name_match`) can never be True. **Fix:** introduce explicit pass branch in `score_turn` for negatives: `passed = (len(calls) == 0)` and a new `failure_kind="false_positive"` when a negative prompt does call a tool. Add new step to plan.

2. **OpenAI Realtime forces `tool_choice="required"`** in `_session_config` at `openai_realtime.py:72`, making no-call success impossible. **Fix:** add per-run `tool_choice` selection. In diverse mode, set `tool_choice="auto"` for both adapters.

3. **`tools_diverse.py` is never loaded** because `run_benchmark` directly imports `load_tools` from `tools.py`. **Fix:** add a `tool_loader` parameter to `run_benchmark` defaulting to the existing `load_tools`; `run-diverse` passes the diverse loader.

4. **`tier_min` filter doesn't exist in `run_benchmark`** — it filters by `mode` and `expected_tool` presence only. **Fix:** add an explicit `tier_min`/`tier_max` filter on prompts in the runner. Negatives have `tier_min=0` so they're included at every tier.

5. **Manifest count contradiction** — the v1 plan said "50 prompts," "20 positive + 5 negative," and "5 negative per tier" simultaneously. **Fix:** define the actual count clearly. The diverse manifest has **20 positive prompts** (one per tool, tagged with `tier_min` matching when the tool first appears) plus **6 negative prompts** (`tier_min=0`, included at every tier). At tier T, the runner emits T positive + 6 negative = T+6 prompts. Total per-tier prompt counts: 3t→9, 5t→11, 10t→16, 15t→21, 20t→26.

6. **Dashboard failure-kind needs `false_positive`** — currently absent. **Fix:** add `false_positive` to `_failure_kind` in `build_dashboard_diverse.py` and to the stacked-bar legend. Also extend `Score` to carry malformed_calls from the adapters (Codex finding #10 — folded in here).

**Nice-to-haves adopted:**
- Namespace diverse audio under `prompts/audio/<voice>/diverse/<id>.wav` (no collision with main manifest IDs).
- Add `scripts/validate_manifest_diverse.py` — checks unique expected_tool per tier, tier nesting, missing audio, negative ratio.
- Dashboard builder reads structured `agent`/`tier` from inside the JSONL (not from run-id parsing).
- Use existing category vocabulary (`app`, `chemistry`, `assistant`) instead of inventing `camera`.

**Nice-to-haves deferred:**
- Recursive arg scoring (out of scope; current top-level matching is sufficient for the 7 declared param shapes — no nested args are used).
- Schema constraints (`minimum`/`maximum`) propagation through `schema_from_dict` — not needed since the diverse-tool schemas don't depend on them for correctness, only for clarity in descriptions.

## 1. Goal

Build a new benchmark mode (`--mode diverse`) that measures voice agents' ability to **correctly route, fill parameters for, and call N genuinely different tools across N distinct prompts** at tool counts 3, 5, 10, 15, and 20.

Voice agents are the primary target (`openai-realtime-v2` = gpt-realtime-2; `gemini-live-v2` = gemini-3.1-flash-live-preview). Text agents must be supported as a follow-on but are out of scope for the first run.

Add a dedicated dashboard page that visualises this new dimension separately from the existing scaling heatmap.

## 2. What's wrong with the current benchmark

| Property | Current (`manifest.json`, mode=full) | Diverse-tool mode (proposed) |
|---|---|---|
| Tools loaded | 50 | N ∈ {3, 5, 10, 15, 20} |
| Distinct expected tools across prompts | **5** | **N** (one prompt per tool, minimum) |
| Parameter shape diversity | All boolean | Boolean / int / float / enum / string / mixed |
| Categories | Mostly camera (tier 1) | Camera / chemistry / assistant / system, balanced |
| Negative prompts (should-not-call) | 0 | At least 20% of prompts per tier |
| Naming-confusion adversarial pairs | accidental | intentional (e.g. `set_zoom` vs `set_zoom_speed`) |
| What it measures | Needle-in-haystack routing | True multi-tool capability |

## 3. Manifest design

### File layout
`prompts/manifest_diverse.json` — list of prompts compatible with the existing `run_benchmark` schema:
- `tier_min` ∈ {0, 3, 5, 10, 15, 20}. Positive prompts use the tier at which their tool first appears. Negative prompts use `tier_min=0` (included at every tier).
- Each positive prompt has a unique `expected_tool` — no duplicates anywhere in the manifest. Tier T contains all tools with `tier_min ≤ T`.
- Negative prompts have `expected_tool=null` and `negative=true`.

Result: 20 positive + 6 negative = **26 prompts total in the manifest**. At a given tier T the runner includes T positive prompts + all 6 negatives, so per-tier counts are 9/11/16/21/26 prompts. This sample size is intentional — small N at low tiers is acceptable here because the experimental dimension is *N tools loaded*, not *N prompts*.

### Tool tiers (with categories balanced)

| Tier | N tools | Category mix |
|---:|---:|---|
| 3 | 3 | 1 app, 1 assistant, 1 chemistry |
| 5 | 5 | 2 app, 2 assistant, 1 chemistry |
| 10 | 10 | 4 app, 3 assistant, 3 chemistry |
| 15 | 15 | 6 app, 5 assistant, 4 chemistry |
| 20 | 20 | 8 app, 7 assistant, 5 chemistry |

Each tier strictly extends the previous (T5 ⊃ T3, T10 ⊃ T5, etc.) — i.e. nested supersets — so trends across tiers reflect added load only, not changed tool composition.

### Parameter-shape coverage (within the 20 chosen tools)

| Shape | Tools |
|---|---|
| Boolean-only | 4 (e.g. `toggle_flash`) |
| Integer-only | 3 (e.g. `set_zoom_level(level=5)`) |
| Float-only | 2 (e.g. `set_exposure_ev(stops=1.0)`) |
| Enum-only | 3 (e.g. `set_white_balance(mode='cloudy')`) |
| String-only | 2 (e.g. `set_sample_label(name='S04')`) |
| Mixed required | 3 (e.g. `set_timer(duration_s=10, beep=True)`) |
| Optional-arg | 3 (e.g. `start_recording(quality='4k')` where `quality` is optional) |

### Adversarial naming pairs
At least 2 pairs of similar-sounding tools at tier 10+ and 4+ pairs at tier 20:
- `set_zoom` vs `set_zoom_speed`
- `toggle_grid_overlay` vs `toggle_grid_lock`
- `start_recording` vs `start_documentation`
- `set_white_balance` vs `set_color_temperature`

Each tool description must explicitly state the disambiguator (e.g. "Use this when the user wants to *change* the zoom level — not the speed at which zoom changes; for that use `set_zoom_speed`.").

### Negative prompts (no-tool-call)
Per tier, 20% negatives covering:
- Pure-knowledge questions ("what's the capital of France")
- Greetings ("hello, how are you")
- Out-of-scope camera asks ("delete all my photos" — capability not in tool list)
- Ambiguous-but-no-tool ("can you remind me later" without giving a time)

## 4. New CLI surface

```bash
voice-bench run-diverse \
    --agent openai-realtime-v2 \
    --tier 10 \
    --voice say \
    --manifest manifest_diverse
```

- New subcommand `run-diverse` (instead of `--mode diverse` to keep the existing `run` command stable).
- `--tier` ∈ {3, 5, 10, 15, 20}, validates against the manifest's `tier_min` field.
- Falls back through the existing `run_benchmark` runner so we reuse audio pre-render, scoring, timing, and result writing.
- Result files: `<agent>-diverse-<tier>t-<ts>.{jsonl, csv}` (new `-diverse-` infix so the dashboard can detect them).

Audio fixtures generated by re-running `voice-bench gen-audio --manifest manifest_diverse`.

## 5. Scoring (rewritten after Codex review)

The existing `score_turn` in `scoring.py:95` has a structural bug where `tool_name_match` is set to `False` for negative prompts even when zero tools were called, so `Score.passed` (which requires `tool_name_match`) can never be True. **Fix this first**, before adding the diverse-mode pass logic.

After the fix:
- **Positive prompts:** pass = (right tool called) AND (arg_score ≥ 0.8). arg_score uses existing top-level key comparison normalised to [0, 1].
- **Negative prompts:** pass = (zero tool calls made). `negative_prompt_violation=True` if a tool *was* called; this maps to `failure_kind="false_positive"` in the dashboard builder (new kind — added in step 8 of the implementation order).
- Per-tier metrics: overall accuracy, positive-only accuracy, negative-only accuracy (= 1 − false-positive rate), and arg-score median for positives.

**`tool_choice="auto"` for diverse mode** — both `OpenAIRealtimeAdapter` and `GeminiLiveAdapter` currently force tool calls when tools are loaded. Add an `allow_no_tool_call: bool = False` flag to each adapter; `run-diverse` passes `True`. Defaults preserve existing benchmark behaviour.

## 6. Dashboard page

New `results/dashboard_diverse.html` (and `dashboard_diverse.js`) — separate file so the existing heatmap stays untouched.

Two views on the new page:
1. **Diverse-tool heatmap** — agents × {3, 5, 10, 15, 20} cells showing accuracy. Same colour scale as the existing heatmap.
2. **Failure-mode breakdown** — stacked bar per cell showing % wrong_tool / arg_mismatch / no_tool / false_positive (negative prompts that wrongly called something). This is the new insight the current dashboard can't surface.

Optional third view: **per-category accuracy** (camera vs chemistry vs assistant) so it's clear whether a model is weak on one domain.

`scripts/build_dashboard_diverse.py` reads only `*-diverse-*.jsonl` files and writes `data_diverse.js`. `dashboard.html` (existing) is untouched. Linked from the existing dashboard via a header nav link.

## 7. Implementation order (v3)

Sequenced for independent testability. Each step is small, reversible, and unblocks the next.

1. **Fix `score_turn` negative-prompt branch** in `scoring.py:95`. Add unit test: negative prompt with zero calls returns `passed=True`; with one call returns `passed=False, negative_prompt_violation=True`. *Codex finding #1 — independent of diverse-mode.*
2. **Add `--strict-routing` flag to `voice-bench run`**. When passed, adapters use `tool_choice="required"` (legacy); default becomes `auto`. Both adapters (`openai_realtime.py`, `gemini_live.py`) take a per-call `tool_choice` parameter; default value comes from a new `strict_routing: bool` argument on `run_benchmark` (default False = auto). Existing scaling runs remain reproducible by passing `--strict-routing`. *Resolves Codex #2 and Gemini concern #4.*
3. **Audit `tools.py` for parameter-shape diversity.** Specifically the tier 1–4 boolean toggles. Where a non-boolean shape is natural without changing the tool's purpose, rewrite. Target distribution across all 50 tools: ≥6 boolean, ≥4 integer, ≥3 float, ≥4 enum, ≥3 string, ≥3 mixed-required, ≥3 optional-arg. Document the changes in a per-tool diff table in the story. *Resolves Gemini concern #6.*
4. **Extend `scripts/gen_manifest_v2.py` with `--mode diverse`.** Generates `manifest_diverse.json` with: one positive prompt per tool (50 prompts, each tagged with the tool's tier so audio lives under `prompts/audio/<voice>/diverse/<id>.wav`) plus 6 negatives. Prompts use phrasing that exercises the parameter shape (not the tool name — e.g. "set the white balance to cloudy" not "call set_white_balance"). *Resolves Gemini concern #5.*
5. **`scripts/validate_manifest_diverse.py`** — fails on: duplicate `expected_tool`, missing audio fixture, missing tool in the canonical `tools.py` registry, negative ratio < 10%. Run before any sweep.
6. **Generate audio** with `voice-bench gen-audio --manifest manifest_diverse`. Updates `gen_audio` to namespace output under `prompts/audio/<voice>/diverse/`. Updates `_pick_audio` in `runner.py:49` to look in `<voice>/diverse/<id>.wav` when manifest name contains "diverse". *Resolves Codex #14.*
7. **`voice-bench run` accepts `--mode diverse`.** No new subcommand needed — the existing `run` handles it. When `--mode diverse`: load `manifest_diverse.json`; emit a `benchmark_mode: "diverse"` field on every row written; otherwise unchanged. The runner's existing `expected_tool in loaded_tool_names` filter at `runner.py:103` does all the tier-filtering work automatically. *Resolves Gemini concern #3 — no `tier_min` filter needed.*
8. **Run sweep:** `openai-realtime-v2` and `gemini-live-v2` × tool counts {3, 5, 10, 15, 20, 30, 40, 50}. Iterations: 3 per cell for Wilson confidence intervals. Wall time: 8 tiers × 2 agents × 3 iters × ~30s ≈ 24 min. Cost: ~$3 on gpt-realtime-2, free on Gemini.
9. **Extend `scripts/build_dashboard.py` for the new field.** Read `benchmark_mode` from each row (default "needle" for legacy rows). Add `false_positive` to `_failure_kind`. Output two parallel data slices in `data.js` (or one slice with a `mode` field per row). *Resolves Gemini concerns #2 and Codex #6 simultaneously.*
10. **Extend `dashboard.html` / `dashboard.js`** with a **mode toggle** at the top (needle / diverse / both). The accuracy heatmap and failure-mode chart re-render against the selected slice. Adds `false_positive` to the stacked-bar legend. No new HTML file.
11. **Update takeaway doc** `docs/2026-05-19-tool-calling-takeaways.md` with the diverse-mode findings and a side-by-side comparison vs the needle numbers.

Note: text agents are intentionally not in the sweep. The user said voice is the priority; once the pipeline works end-to-end, running `claude-sonnet`, `gpt-5`, `gemini-pro` etc. against diverse mode is a one-line addition (same CLI, different `--agent`).

## 8. Open questions for reviewers

1. **Should we run multiple iterations** (e.g. n=3 per prompt) at this scale, so we can put Wilson confidence intervals on the heatmap cells? Adds 3× cost but solves the "50-prompt single-shot noise" problem.
2. **Is the negative-prompt fraction (20%) right**, or should we go higher (33%) to really stress false-positive rate?
3. **Parameter scoring**: full-arg-match-or-fail vs partial credit (arg_score ∈ [0, 1])? Current scoring already supports partial — should diverse-mode require strict match for negatives but partial for positives?
4. **Should adversarial pairs be a separate test mode** (just the confusable pairs at small N) so we can isolate that effect, or kept inline as designed?
5. **Cost ceiling**: 50 prompts × 5 tiers × 2 voice agents = 500 audio-API calls. ~$5 for gpt-realtime-2, free for Gemini. Acceptable.
6. **Voice-only first vs voice+text in the same run**: The user said voice is primary. If we wire `run-diverse` to accept any agent (voice or text), running text variants later is a one-line change. Worth doing now or later?

## 9. Acceptance criteria

- [ ] `tools_diverse.py` ships with 20 tools covering 7 param shapes, 3 categories, and 4 adversarial pairs.
- [ ] `prompts/manifest_diverse.json` ships with ≥ 25 prompts (20 positive + 5+ negative) with proper `tier_min` tagging.
- [ ] `voice-bench run-diverse --agent openai-realtime-v2 --tier 10` runs end-to-end and writes results.
- [ ] Both voice agents have results at tiers 3/5/10/15/20.
- [ ] `dashboard_diverse.html` renders an accuracy heatmap and failure-mode breakdown.
- [ ] Existing dashboard (`dashboard.html`) is unchanged.
- [ ] `docs/2026-05-19-tool-calling-takeaways.md` updated with diverse-mode findings.

## 10. Risks

- **Audio TTS quality on adversarial pairs**: macOS `say` might pronounce `set_zoom` vs `set_zoom_speed` identically enough that the audio prompt is ambiguous even for a perfect model. May need to ensure prompts are phrased *as commands* not *as tool names*, e.g. "make the zoom faster" (→ `set_zoom_speed`) vs "zoom in twice" (→ `set_zoom`).
- **Tool description length**: long descriptions blow up input tokens. With 20 tools each having a paragraph-long disambiguator, we could be at 4–6k tokens per prompt easily. Budget cost accordingly.
- **Manifest churn**: if we discover a prompt is genuinely ambiguous, we'll need to revise. Lock the manifest after a smoke-test round.

---

## Reviewer Feedback / Codex (round 1, gpt-5.5)

Raw critique from `codex exec -s read-only` against the v1 draft. Numbered findings; the final two labelled lists drove the v2 revision in section 0.

**Findings**

1. Negative prompts will all fail under current scoring. In `score_turn` (`scoring.py:95`), the negative branch sets `tool_name_match=False` even when `len(calls) == 0`; `Score.passed` (`models.py:173`) only passes when `tool_name_match and arg_score >= 0.8`. So "pass = NO tool called" is not true today.

2. OpenAI Realtime is configured to force a tool call whenever tools exist. `OpenAIRealtimeAdapter._session_config` (`openai_realtime.py:72`) sets `"tool_choice": "required" if built_tools else "none"`. That makes the proposed no-tool negative prompts structurally impossible for `openai-realtime-v2` unless diverse mode changes tool choice to `auto` or uses a separate adapter option.

3. `run_benchmark` cannot use `tools_diverse.py` just by passing a new manifest. It imports `load_tools` from `tools.py` (`runner.py:14`) and calls it directly at `runner.py:78`. A new module will be ignored unless the runner gains a tool-set parameter/loader or `run-diverse` bypasses this path.

4. `tier_min` is not implemented as a runner filter. `run_benchmark` (`runner.py:81`) filters only by `mode` and then by whether `expected_tool` is loaded at `runner.py:103`. Negative prompts with no `expected_tool` will be included at every tier; positive prompts will be included based on current `load_tools()` order, not `tier_min`.

5. The plan's prompt-count logic is internally inconsistent. It says "50 prompts," "N distinct expected tools," "20 positive + 5 negative," and "5 negative per tier," but those cannot all hold. If there are only 5 negatives tagged at tier 20, lower tiers get no negatives under a real `tier_min <= tier` filter. If all negatives lack `expected_tool`, current runner includes them at every tier.

6. Failure-mode breakdown cannot work as described with current result fields. `scripts/build_dashboard.py` (line 67) classifies no-call negative successes as `no_tool`, not `pass`, because of the scoring bug. It also has no `false_positive` failure kind; negative violations are only present as `negative_prompt_violation` in score output, not mapped into dashboard categories.

7. Planned run IDs like `<agent>-diverse-<tier>t-...` will confuse existing dashboard parsing if reused. `build_dashboard.py:143` derives agent as everything before the `10t` part, so `openai-realtime-v2-diverse-10t-...` becomes agent `openai-realtime-v2-diverse`. A dedicated builder must avoid copying that assumption.

8. The category vocabulary in the plan does not match the tool model. `DummyTool.category` (`tools.py:12`) allows `"chemistry"`, `"app"`, and `"assistant"`, while the plan says `"camera"`, `"chemistry"`, `"assistant"`, and later expects per-category camera accuracy. Either the plan must map camera to `app`, or the type/model/routing constants need updating.

9. Assistant-category "tools" are not supported by the current standard tool pool. Existing `assistant` is a meta-tool for routing only, and `SUBTOOLS_BY_BUCKET` (`tools.py:827`) has no assistant subtools. If diverse mode includes real assistant tools, the plan needs to account for schemas, expected calls, and whether system prompts still say "lab camera app."

10. Malformed tool arguments are not actually propagated into scoring. `OpenAIRealtimeAdapter.run_turn` (`openai_realtime.py:247`) increments `malformed_count`, but `TurnResult` has no malformed field and `score_turn` (`scoring.py:159`) always sets `malformed_calls=0`. Diverse mode with richer arg shapes needs this fixed or malformed JSON will be undercounted.

11. Argument scoring is shallow and may not match the proposed parameter coverage. `_arg_score` (`scoring.py:60`) checks only expected top-level keys, ignores unexpected extra args, and has no recursive object/array support. Optional args are effectively ignored when omitted from `expected_args`, so "optional-arg coverage" may not measure what the plan claims.

12. The adapter/schema path needs validation for the proposed tool schemas. Gemini declarations go through `schema_from_dict` (`_gemini_common.py:6`), which only carries `type`, `description`, `enum`, `properties`, `required`, and `items`; it drops constraints such as `minimum`/`maximum`. If diverse tools rely on constraints for correctness, providers will not see them consistently.

13. The CLI plan omits a key dependency: `run-diverse` needs to choose both manifest and tool universe. Current `cli.run` (`cli.py:44`) passes `--tools` and `--manifest` only; there is no equivalent hook for "diverse tools." Just adding `--tier` will still run against the old first-N tools.

14. Audio generation output is keyed only by prompt id and voice folder. `_pick_audio` (`runner.py:49`) looks up `prompts/audio/<voice>/<id>.wav`. If `manifest_diverse.json` reuses IDs from `manifest.json`, `gen-audio` will skip existing files at `cli.py:153` and silently run the wrong audio.

15. Existing dashboard builder intentionally scans all `*.jsonl` at `build_dashboard.py:85`. The proposed `build_dashboard_diverse.py` must filter strictly by filename or manifest; otherwise old benchmark rows will contaminate diverse metrics.

**Critical issues:** (all six folded into v2 section 0)

- Fix negative prompt pass semantics in `score_turn`/`Score.passed`.
- Change OpenAI Realtime tool choice for diverse negatives; current `required` makes no-call success impossible.
- Add an explicit diverse tool loader path; `tools_diverse.py` is otherwise unused.
- Implement real `tier_min` filtering and define how negative prompts are included per tier.
- Resolve the manifest-count contradiction before authoring prompts.
- Extend dashboard failure classification to distinguish `false_positive` from ordinary `wrong_tool`.

**Nice-to-have:** (adopted: audio path namespacing, validation script, structured JSON in builder, category vocabulary; deferred: recursive arg scoring, schema constraints — see section 0)

## Revision history

- 2026-05-19 — v1 drafted.
- 2026-05-19 — Codex (gpt-5.5) implementation-lens critique. Six critical issues identified; all addressed in v2. Six nice-to-haves; four adopted, two deferred with rationale.

## Reviewer Feedback / Gemini (round 2, gemini-3.1-pro-preview)

Architectural-lens critique against the v2 draft. Six concerns, four alternatives.

**Architectural concerns:**

1. **Disconnected scaling baseline (capping at 20 tools)** — the primary benchmark tests scaling up to 50 tools across 7 tiers. Stopping diverse at 20 creates a disconnected baseline; cannot compare how parameter-shape diversity impacts the critical 20→50 scale cliff.

2. **Dashboard fragmentation** — a completely separate `dashboard_diverse.html` makes side-by-side comparison of needle-vs-diverse impossible. Should be toggleable layers on the existing heatmap.

3. **Redundant filtering logic (`tier_min`)** — `run_benchmark` already filters via `p["expected_tool"] in loaded_tool_names`. If you load 10 tools, the runner naturally filters the manifest to those 10. Negatives (`expected_tool=null`) are natively included at every tier. The entire `tier_min` machinery is unnecessary.

4. **Adapter configuration divergence (`allow_no_tool_call`)** — if the baseline keeps `tool_choice="required"`, it's an artificially easier task that conflates "decide whether to call" with "decide which to call." The fix should apply globally.

5. **Manifest generation split** — the existing manifest is generated programmatically via `scripts/gen_manifest_v2.py`, providing structural guarantees. Drafting `manifest_diverse.json` manually breaks the data pipeline.

6. **Domain misalignment (SciSymbioLens-Android)** — the benchmark exists for SciSymbioLens-Android. Inventing generic "assistant" tools to force diversity dilutes the benchmark's relevance. Diversity should be achieved within the SciSymbioLens domain.

**Alternatives worth considering:**

1. **"In-place" diversification** — instead of building parallel `tools_diverse.py` and `run-diverse`, rewrite the existing 50 tools to have diverse parameter shapes and add prompts to the existing generator so N tools = N distinct prompts. Upgrades the entire benchmark globally.

2. **Unified dashboard** — extend `scripts/build_dashboard.py` to parse the new failure kinds and plot diverse runs as a toggleable layer.

3. **Universal `tool_choice="auto"`** — change default for all runs. Exposes true routing accuracy on the baseline, ensures negative-prompt correctness universally.

4. **Programmatic diverse manifest** — integrate new prompts into `scripts/gen_manifest_v2.py` with a `benchmark_mode: diverse` tag.

**All six concerns and all four alternatives adopted in v3 (see top of file).**

## Revision history (updated)

- 2026-05-19 — v1 drafted.
- 2026-05-19 — Codex (gpt-5.5) implementation-lens critique. Six critical issues, six nice-to-haves. v2 drafted addressing all critical items and four of six nice-to-haves.
- 2026-05-19 — Gemini (gemini-3.1-pro-preview) architectural-lens critique. Six concerns, four alternatives — all adopted, drove a major simplification. v3 eliminates `tier_min` machinery, `tools_diverse.py`, `manifest_diverse.json`, `run-diverse` subcommand, and separate dashboard page. All work now folds into existing files. v3 ready for user approval.
