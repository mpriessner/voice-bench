"""CLI entry point: voice-bench probe | run"""

import json
import asyncio
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

VOICE_AGENTS = ["gemini-live", "gemini-live-v2", "gemini-live-swap", "openai-realtime", "openai-realtime-v2", "openai-realtime-swap"]
TEXT_AGENTS = ["claude-opus", "claude-sonnet", "gpt-text", "gpt-4o", "gpt-5", "gemini-pro", "gemini-flash", "gemini-3-flash"]
VALID_AGENTS = VOICE_AGENTS + TEXT_AGENTS
VALID_TOOL_COUNTS = [1, 2, 3, 5, 10, 15, 20, 30, 40, 50]


@click.group()
def cli() -> None:
    """voice-bench: automated voice-agent benchmarking harness."""


@cli.command()
@click.option("--agent", default="gemini-live", show_default=True,
              type=click.Choice(VALID_AGENTS), help="Agent to probe.")
def probe(agent: str) -> None:
    """Connect to a voice agent, confirm setup-complete, then disconnect."""
    from .adapters.registry import build_adapter
    click.echo(f"Probing {agent}...")
    try:
        adapter = build_adapter(agent)
    except NotImplementedError as e:
        click.echo(str(e), err=True)
        sys.exit(1)
    result = asyncio.run(adapter.probe())

    click.echo(json.dumps(result, indent=2))
    if result.get("status") != "ok":
        sys.exit(1)


@cli.command()
@click.option("--agent", default="gemini-live", show_default=True,
              type=click.Choice(VALID_AGENTS), help="Voice agent to benchmark.")
@click.option("--tools", "tool_count", default=5, show_default=True,
              type=click.Choice([str(n) for n in VALID_TOOL_COUNTS]),
              help="Number of dummy tools to load (1/2/3/5/10/15/20/30).")
@click.option("--mode", default="smoke", show_default=True,
              type=click.Choice(["smoke", "full", "v1", "v2", "v3", "routing", "diverse"]),
              help="smoke=tagged, full=all, v1/v2/v3=difficulty, routing=meta-tool routing, diverse=diverse-tool benchmark.")
@click.option("--routing-mode", default="auto", show_default=True,
              type=click.Choice(["auto", "forced"]),
              help="Routing sub-mode: auto=tool_choice=auto, forced=tool_choice=required.")
@click.option("--voice", default="say", show_default=True,
              help="TTS voice subfolder under prompts/audio/.")
@click.option("--manifest", "manifest_name", default="manifest", show_default=True,
              help="Manifest filename without .json (e.g. manifest_v2).")
@click.option("--run-id", default=None, help="Override the auto-generated run ID.")
@click.option("--strict-routing/--no-strict-routing", default=True, show_default=True,
              help="Force tool_choice=required (on by default). Use --no-strict-routing for diverse/negative-prompt modes.")
def run(agent: str, tool_count: str, mode: str, routing_mode: str, voice: str,
        manifest_name: str, run_id: str | None, strict_routing: bool) -> None:
    """Run the benchmark for one agent + tool count."""
    from pathlib import Path
    from .runner import run_benchmark
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    manifest_path = prompts_dir / f"{manifest_name}.json"
    bm = "diverse" if mode == "diverse" else "needle"
    summary = run_benchmark(
        agent=agent,
        tool_count=int(tool_count),
        mode=mode,
        voice=voice,
        run_id=run_id,
        manifest_path=manifest_path,
        routing_mode=routing_mode if mode == "routing" else None,
        strict_routing=strict_routing,
        benchmark_mode=bm,
    )
    click.echo("\nSummary:")
    click.echo(json.dumps(summary, indent=2))


@cli.command("pipeline")
@click.option("--voice-agent", required=True, type=click.Choice(VOICE_AGENTS),
              help="Native voice adapter for layer 1 routing.")
@click.option("--text-agent", required=True, type=click.Choice(TEXT_AGENTS),
              help="Text adapter for layer 2 sub-tool selection.")
@click.option("--mode", default="smoke", show_default=True,
              type=click.Choice(["smoke", "full", "v1", "v2", "v3"]),
              help="smoke=tagged, full=all, v1/v2/v3=difficulty filter.")
@click.option("--voice", default="say", show_default=True,
              help="TTS voice subfolder under prompts/audio/.")
@click.option("--manifest", "manifest_name", default="manifest", show_default=True,
              help="Manifest filename without .json.")
@click.option("--run-id", default=None, help="Override the auto-generated run ID.")
def pipeline_cmd(voice_agent: str, text_agent: str, mode: str, voice: str,
                 manifest_name: str, run_id: str | None) -> None:
    """Run a two-layer pipeline benchmark: voice routes → text picks sub-tool."""
    from pathlib import Path
    from .runner import run_pipeline_benchmark
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    manifest_path = prompts_dir / f"{manifest_name}.json"
    summary = run_pipeline_benchmark(
        voice_agent=voice_agent,
        text_agent=text_agent,
        mode=mode,
        voice=voice,
        run_id=run_id,
        manifest_path=manifest_path,
    )
    click.echo("\nSummary:")
    click.echo(json.dumps(summary, indent=2))


@cli.command("gen-audio")
@click.option("--voice", default="say", show_default=True, help="Voice name (say = macOS say).")
@click.option("--manifest", default=None, help="Path to manifest file (default: prompts/manifest.json).")
@click.option("--rate", default=160, show_default=True, help="Speaking rate (words per minute).")
def gen_audio(voice: str, manifest: str | None, rate: int) -> None:
    """Pre-render audio fixtures for all prompts in the manifest."""
    import subprocess
    import tempfile
    from pathlib import Path

    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    manifest_path = Path(manifest) if manifest else (prompts_dir / "manifest.json")
    out_dir = prompts_dir / "audio" / voice
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(manifest_path) as f:
        prompts = json.load(f)

    # Vary voice by difficulty to simulate different speakers
    voice_map = {
        "v1": ("Alex", rate),
        "v2": ("Samantha", rate - 10),   # slightly slower, different voice
        "v3": ("Fred", rate - 20),        # slower still, Fred has a rougher cadence
    }

    # Support both flat manifest (list of prompts) and scenario manifest (list of scenarios with turns)
    flat_prompts = []
    for entry in prompts:
        if "turns" in entry:
            # Scenario manifest: expand turns into individual prompts
            for turn in entry["turns"]:
                flat_prompts.append({
                    "id": turn["id"],
                    "text": turn.get("text", ""),
                    "difficulty": turn.get("difficulty", "v2"),
                })
        else:
            flat_prompts.append(entry)

    for prompt in flat_prompts:
        subdir = prompt.get("audio_subdir")
        prompt_out_dir = (out_dir / subdir) if subdir else out_dir
        prompt_out_dir.mkdir(parents=True, exist_ok=True)
        wav_path = prompt_out_dir / f"{prompt['id']}.wav"
        if wav_path.exists():
            click.echo(f"  [skip] {wav_path.name} already exists")
            continue

        text = prompt["text"]
        difficulty = prompt.get("difficulty", "v1")
        say_voice, say_rate = voice_map.get(difficulty, ("Alex", rate))
        click.echo(f"  [{difficulty}] {wav_path.name}: \"{text}\"")

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            tmp_aiff = tmp.name

        try:
            subprocess.run(
                ["say", "-v", say_voice, "-r", str(say_rate), text, "-o", tmp_aiff],
                check=True, capture_output=True,
            )
            # Convert AIFF → 16kHz mono PCM16 WAV (adds 500ms trailing silence for VAD)
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", tmp_aiff,
                    "-ar", "16000",
                    "-ac", "1",
                    "-sample_fmt", "s16",
                    "-af", "apad=pad_dur=0.5",  # 500ms silence
                    str(wav_path),
                ],
                check=True, capture_output=True,
            )
            click.echo(f"    → {wav_path}")
        except subprocess.CalledProcessError as e:
            click.echo(f"    ERROR: {e.stderr.decode()}", err=True)
        finally:
            Path(tmp_aiff).unlink(missing_ok=True)

    click.echo(f"\nDone. Audio files in: {out_dir}")


@cli.command("probe-swap")
def probe_swap_cmd() -> None:
    """Run the Phase 0 session.update probe against OpenAI Realtime."""
    import subprocess
    import sys
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    probe_script = scripts_dir / "probe_session_update.py"
    if not probe_script.exists():
        click.echo(f"Probe script not found: {probe_script}", err=True)
        sys.exit(1)
    result = subprocess.run([sys.executable, str(probe_script)])
    sys.exit(result.returncode)


@cli.command("probe-gemini-swap")
def probe_gemini_swap_cmd() -> None:
    """Run the Phase 0 session-restart probe against Gemini Live."""
    import subprocess
    import sys
    scripts_dir = Path(__file__).parent.parent.parent / "scripts"
    probe_script = scripts_dir / "probe_gemini_session_swap.py"
    if not probe_script.exists():
        click.echo(f"Probe script not found: {probe_script}", err=True)
        sys.exit(1)
    result = subprocess.run([sys.executable, str(probe_script)])
    sys.exit(result.returncode)


SWAP_AGENTS = ["openai-realtime-swap", "gemini-live-swap"]


@cli.command("swap")
@click.option("--agent", default="openai-realtime-swap", show_default=True,
              type=click.Choice(SWAP_AGENTS),
              help="Swap adapter to benchmark.")
@click.option("--voice", default="say", show_default=True,
              help="TTS voice subfolder under prompts/audio/.")
@click.option("--manifest", "manifest_name", default="manifest_swap", show_default=True,
              help="Swap manifest filename without .json.")
@click.option("--run-id", default=None, help="Override the auto-generated run ID.")
def swap_cmd(agent: str, voice: str, manifest_name: str, run_id: str | None) -> None:
    """Run the dynamic tool-pool swap benchmark."""
    from .swap_runner import run_swap_benchmark
    prompts_dir = Path(__file__).parent.parent.parent / "prompts"
    manifest_path = prompts_dir / f"{manifest_name}.json"
    summary = run_swap_benchmark(
        agent=agent,
        voice=voice,
        run_id=run_id,
        manifest_path=manifest_path,
    )
    click.echo("\nSummary:")
    click.echo(json.dumps(summary, indent=2))
