"""
Generate prompts/manifest_routing.json — routing benchmark prompts.

Each prompt has an expected_category field (chemistry / app / assistant / ambiguous).
Run once to create the manifest; re-run only to regenerate.

Usage:
    uv run python scripts/gen_routing_manifest.py
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
OUT = ROOT / "prompts" / "manifest_routing.json"

PROMPTS = [
    # ── chemistry ─────────────────────────────────────────────────────────────
    {"id": "rt001", "text": "Start recording this reaction with h264.", "expected_category": "chemistry", "smoke": True},
    {"id": "rt002", "text": "Begin a documentation session labelled reaction alpha.", "expected_category": "chemistry", "smoke": True},
    {"id": "rt003", "text": "Annotate the frame with 'temperature spike at t=30'.", "expected_category": "chemistry"},
    {"id": "rt004", "text": "Configure the session for project BioVia with auto-documentation on.", "expected_category": "chemistry"},
    {"id": "rt005", "text": "Export this session as a PDF.", "expected_category": "chemistry"},
    {"id": "rt006", "text": "Sync experiment EXP042 to the lab notebook.", "expected_category": "chemistry", "smoke": True},
    {"id": "rt007", "text": "Set the exposure to plus one stop for better sample visibility.", "expected_category": "chemistry"},
    {"id": "rt008", "text": "Zoom in to 5x on the crystal lattice.", "expected_category": "chemistry"},
    {"id": "rt009", "text": "Set focus distance to 0.5 metres for the microscope slide.", "expected_category": "chemistry"},
    {"id": "rt010", "text": "Set ISO to 800 for the low-light incubator.", "expected_category": "chemistry"},
    {"id": "rt011", "text": "Use a shutter speed of 1/200 to freeze the droplet.", "expected_category": "chemistry"},

    # ── app ───────────────────────────────────────────────────────────────────
    {"id": "rt012", "text": "Turn on the flash.", "expected_category": "app", "smoke": True},
    {"id": "rt013", "text": "Show the composition grid.", "expected_category": "app"},
    {"id": "rt014", "text": "Enable macro mode.", "expected_category": "app"},
    {"id": "rt015", "text": "Turn on image stabilization.", "expected_category": "app"},
    {"id": "rt016", "text": "Enable voice captions.", "expected_category": "app"},
    {"id": "rt017", "text": "Switch to the front camera.", "expected_category": "app", "smoke": True},
    {"id": "rt018", "text": "Set resolution to max.", "expected_category": "app"},
    {"id": "rt019", "text": "Set white balance to fluorescent.", "expected_category": "app"},
    {"id": "rt020", "text": "Set a 10-second timer.", "expected_category": "app"},
    {"id": "rt021", "text": "Switch to 4:3 aspect ratio.", "expected_category": "app"},
    {"id": "rt022", "text": "Enable HDR.", "expected_category": "app"},
    {"id": "rt023", "text": "Enable burst mode with 10 frames.", "expected_category": "app"},
    {"id": "rt024", "text": "Take a photo.", "expected_category": "app", "smoke": True},
    {"id": "rt025", "text": "Apply a full config: back camera, flash off, high resolution.", "expected_category": "app"},
    {"id": "rt026", "text": "Apply the night preset.", "expected_category": "app"},
    {"id": "rt027", "text": "Set color profile to raw.", "expected_category": "app"},
    {"id": "rt028", "text": "Set photo review to 5 seconds.", "expected_category": "app"},
    {"id": "rt029", "text": "Enable GPS location tagging.", "expected_category": "app"},
    {"id": "rt030", "text": "Set video frame rate to 120 fps.", "expected_category": "app"},

    # ── assistant (out-of-category prompts) ───────────────────────────────────
    {"id": "rt031", "text": "What is the melting point of copper?", "expected_category": "assistant"},
    {"id": "rt032", "text": "Can you explain what ISO means in photography?", "expected_category": "assistant"},
    {"id": "rt033", "text": "How do I calibrate a microscope?", "expected_category": "assistant"},

    # ── ambiguous (≥2 valid categories) ───────────────────────────────────────
    # These are intentionally tricky and expected_category is the best answer
    {"id": "rt034", "text": "Set up the camera for a good shot of the crystal.", "expected_category": "app", "ambiguous": True},
    {"id": "rt035", "text": "I need to record and also increase the zoom.", "expected_category": "chemistry", "ambiguous": True},
]

if __name__ == "__main__":
    with open(OUT, "w") as f:
        json.dump(PROMPTS, f, indent=2)
    print(f"Wrote {len(PROMPTS)} routing prompts to {OUT}")

    # Print category counts
    from collections import Counter
    counts = Counter(p["expected_category"] for p in PROMPTS)
    for cat, n in sorted(counts.items()):
        print(f"  {cat}: {n}")
