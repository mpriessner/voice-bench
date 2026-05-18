"""CLI entry point: voice-bench probe | run"""

import json
import asyncio
import os
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

load_dotenv()

VALID_AGENTS = ["gemini-live"]
VALID_TOOL_COUNTS = [5, 10, 15, 20, 30]


@click.group()
def cli() -> None:
    """voice-bench: automated voice-agent benchmarking harness."""


@cli.command()
@click.option("--agent", default="gemini-live", show_default=True,
              type=click.Choice(VALID_AGENTS), help="Agent to probe.")
def probe(agent: str) -> None:
    """Connect to a voice agent, confirm setup-complete, then disconnect."""
    click.echo(f"Probing {agent}...")
    if agent == "gemini-live":
        from .adapters.gemini_live import GeminiLiveAdapter
        adapter = GeminiLiveAdapter()
        result = asyncio.run(adapter.probe())
    else:
        click.echo(f"No adapter for {agent}", err=True)
        sys.exit(1)

    click.echo(json.dumps(result, indent=2))
    if result.get("status") != "ok":
        sys.exit(1)


@cli.command()
@click.option("--agent", default="gemini-live", show_default=True,
              type=click.Choice(VALID_AGENTS), help="Voice agent to benchmark.")
@click.option("--tools", "tool_count", default=5, show_default=True,
              type=click.Choice([str(n) for n in VALID_TOOL_COUNTS]),
              help="Number of dummy tools to load (5/10/15/20/30).")
@click.option("--mode", default="smoke", show_default=True,
              type=click.Choice(["smoke", "full"]),
              help="smoke=5 prompts, full=all prompts.")
@click.option("--voice", default="say", show_default=True,
              help="TTS voice subfolder under prompts/audio/.")
@click.option("--run-id", default=None, help="Override the auto-generated run ID.")
def run(agent: str, tool_count: str, mode: str, voice: str, run_id: str | None) -> None:
    """Run the benchmark for one agent + tool count."""
    from .runner import run_benchmark
    summary = run_benchmark(
        agent=agent,
        tool_count=int(tool_count),
        mode=mode,
        voice=voice,
        run_id=run_id,
    )
    click.echo("\nSummary:")
    click.echo(json.dumps(summary, indent=2))


@cli.command("gen-audio")
@click.option("--voice", default="say", show_default=True, help="Voice name (say = macOS say).")
@click.option("--manifest", default=None, help="Path to manifest.json (default: prompts/manifest.json).")
def gen_audio(voice: str, manifest: str | None) -> None:
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

    for prompt in prompts:
        wav_path = out_dir / f"{prompt['id']}.wav"
        if wav_path.exists():
            click.echo(f"  [skip] {wav_path.name} already exists")
            continue

        text = prompt["text"]
        click.echo(f"  Generating {wav_path.name}: \"{text}\"")

        with tempfile.NamedTemporaryFile(suffix=".aiff", delete=False) as tmp:
            tmp_aiff = tmp.name

        try:
            subprocess.run(
                ["say", "-v", "Alex", "-r", "160", text, "-o", tmp_aiff],
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
