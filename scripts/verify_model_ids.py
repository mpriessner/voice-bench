"""Step 0: verify that planned model IDs are actually available from each provider."""

import asyncio
import os
import sys
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_MODELS = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
]

GPT_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.5-preview",
    "gpt-5",
    "o3",
    "o4-mini",
]

GEMINI_MODELS = [
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-flash-preview-05-20",
    "gemini-2.0-flash-001",
    "gemini-1.5-pro-002",
]


def check_anthropic() -> None:
    try:
        import anthropic
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            print("[anthropic] ANTHROPIC_API_KEY not set — skipping")
            return
        client = anthropic.Anthropic(api_key=key)
        print("\n[anthropic] Testing models:")
        for m in ANTHROPIC_MODELS:
            try:
                resp = client.messages.create(
                    model=m, max_tokens=4,
                    messages=[{"role": "user", "content": "hi"}],
                )
                print(f"  ✓  {m}  (stop_reason={resp.stop_reason})")
            except Exception as e:
                print(f"  ✗  {m}  — {e}")
    except ImportError:
        print("[anthropic] SDK not installed")


def check_openai() -> None:
    try:
        import openai
        key = os.environ.get("OPENAI_API_KEY", "")
        if not key:
            print("[openai] OPENAI_API_KEY not set — skipping")
            return
        client = openai.OpenAI(api_key=key)

        available = {m.id for m in client.models.list()}
        print("\n[openai] Testing models:")
        for m in GPT_MODELS:
            if m in available:
                try:
                    resp = client.chat.completions.create(
                        model=m, max_tokens=4,
                        messages=[{"role": "user", "content": "hi"}],
                    )
                    print(f"  ✓  {m}  (finish={resp.choices[0].finish_reason})")
                except Exception as e:
                    print(f"  !  {m}  listed but request failed: {e}")
            else:
                print(f"  ✗  {m}  not in models list")

        # Also show latest-looking models available
        interesting = sorted(
            (m for m in available if any(x in m for x in ["gpt-4", "gpt-5", "o1", "o3", "o4"])),
            reverse=True,
        )
        print(f"\n  Available GPT/O-series IDs ({len(interesting)} shown):")
        for m in interesting[:20]:
            print(f"    {m}")
    except ImportError:
        print("[openai] SDK not installed")


def check_gemini() -> None:
    try:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
        if not key:
            print("[gemini] GEMINI_API_KEY not set — skipping")
            return
        client = genai.Client(api_key=key)
        available = {m.name.split("/")[-1] for m in client.models.list()}

        print("\n[gemini] Testing models:")
        for m in GEMINI_MODELS:
            if m in available:
                try:
                    resp = client.models.generate_content(
                        model=m,
                        contents="hi",
                    )
                    print(f"  ✓  {m}")
                except Exception as e:
                    print(f"  !  {m}  listed but request failed: {e}")
            else:
                print(f"  ✗  {m}  not in models list")

        text_models = sorted(m for m in available if "flash" in m or "pro" in m)
        print(f"\n  Available Gemini text model IDs ({len(text_models)} shown):")
        for m in text_models[:20]:
            print(f"    {m}")
    except ImportError:
        print("[gemini] google-genai SDK not installed")


if __name__ == "__main__":
    check_anthropic()
    check_openai()
    check_gemini()
    print("\nDone.")
