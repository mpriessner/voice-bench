"""
Generate prompts/manifest_v2.json — harder benchmark covering all 30 tools.

90 prompts: 3 variants per tool × 30 tools
  v1 (p001–p030): clean natural, one per tool — used for smoke run
  v2 (p031–p060): hedged/filler ("um", "uh"), slightly longer
  v3 (h001–h030): indirect/implicit, requires inference — holdout

Run:
    uv run python scripts/gen_manifest_v2.py
"""

import json
from pathlib import Path

# ── Prompt definitions ────────────────────────────────────────────────────────
# Each entry: (tool, args, v1_text, v2_text, v3_text)
# args must match the DummyTool parameter names exactly.

PROMPTS = [
    # ── Tier 1: boolean toggles ──────────────────────────────────────────────
    (
        "toggle_flash", {"on": True},
        "Turn on the flash",
        "Um, could you turn the flash on for me",
        "It's way too dark to see the sample clearly",
    ),
    (
        "toggle_flash", {"on": False},
        "Turn off the flash",
        "The flash is creating too much glare, switch it off",
        "All my highlights are blowing out from the light",
    ),
    (
        "toggle_grid_overlay", {"on": True},
        "Show the composition grid",
        "I want grid lines on the screen, can you enable those",
        "I keep getting the slide off-center, help me align the shot",
    ),
    (
        "toggle_grid_overlay", {"on": False},
        "Hide the grid overlay",
        "The grid is blocking my view, remove it please",
        "I'm done composing, the lines are in the way now",
    ),
    (
        "toggle_macro_mode", {"on": True},
        "Enable macro mode",
        "I need to photograph something very small, switch to macro",
        "The colonies are less than a millimeter wide, I can't get them in focus",
    ),
    (
        "toggle_macro_mode", {"on": False},
        "Disable macro mode",
        "I'm done with close-ups, switch out of macro",
        "Back to normal distance photography now",
    ),
    (
        "toggle_stabilization", {"on": True},
        "Enable image stabilization",
        "My hands are unsteady today, turn on the stabilizer",
        "Every shot is coming out blurry from hand movement",
    ),
    (
        "toggle_stabilization", {"on": False},
        "Turn off image stabilization",
        "The stabilization is fighting me when I pan, disable it",
        "I'm mounting this on a tripod, I don't want any digital correction",
    ),
    (
        "toggle_voice_captions", {"on": True},
        "Enable voice captions",
        "I want my spoken annotations to appear on screen, turn on captions",
        "I need to see what I'm saying while I'm documenting",
    ),
    (
        "toggle_voice_captions", {"on": False},
        "Turn off voice captions",
        "The caption text is covering the image, get rid of it",
        "The subtitles are blocking the sample I need to see",
    ),

    # ── Tier 2: single enum ──────────────────────────────────────────────────
    (
        "switch_camera", {"lens": "back"},
        "Switch to the back camera",
        "Can you switch over to the rear-facing camera",
        "I need the main lens for this shot",
    ),
    (
        "switch_camera", {"lens": "front"},
        "Switch to the front camera",
        "Um, let me use the front-facing camera for this",
        "I need to face the camera toward me now",
    ),
    (
        "set_resolution", {"resolution": "high"},
        "Set resolution to high",
        "I need high resolution images for the paper, set it to high",
        "These images need to be publication quality",
    ),
    (
        "set_resolution", {"resolution": "low"},
        "Set resolution to low",
        "Just a quick preview shot, drop the resolution down",
        "I only need a rough check, don't waste storage",
    ),
    (
        "set_white_balance", {"preset": "daylight"},
        "Set white balance to daylight",
        "We're working under natural light today, set white balance to daylight",
        "The colors look a bit warm, we're in outdoor lighting here",
    ),
    (
        "set_white_balance", {"preset": "fluorescent"},
        "Set white balance to fluorescent",
        "The lab has fluorescent overhead lights, adjust the white balance",
        "Everything looks greenish because of these tubes above me",
    ),
    (
        "set_timer", {"delay": "5s"},
        "Set the capture timer to five seconds",
        "Give me five seconds to get my hands away before it shoots",
        "I need a moment to step back after pressing the button",
    ),
    (
        "set_timer", {"delay": "10s"},
        "Set the timer to ten seconds",
        "Ten second delay please, I need time to get in position",
        "I have to move the sample into place before it fires",
    ),
    (
        "set_aspect_ratio", {"ratio": "16:9"},
        "Set aspect ratio to sixteen by nine",
        "I want widescreen format, sixteen by nine",
        "The display I'm presenting on is widescreen",
    ),
    (
        "set_aspect_ratio", {"ratio": "1:1"},
        "Set aspect ratio to one by one",
        "I need square photos for the poster layout, one to one ratio",
        "Everything needs to be square format for the report figures",
    ),

    # ── Tier 3: numeric ──────────────────────────────────────────────────────
    (
        "set_exposure", {"ev": -1.0},
        "Set exposure to minus one",
        "The image is too bright, bring the exposure down by one stop",
        "I'm losing all the detail in the bright areas, it's completely washed out",
    ),
    (
        "set_exposure", {"ev": 0.5},
        "Increase exposure by half a stop",
        "It's a bit underexposed, um, bump it up half a stop",
        "The image is too dark, I can't see the fine structures",
    ),
    (
        "set_zoom", {"level": 3.0},
        "Zoom to three times",
        "Can you zoom in to three times magnification",
        "I need to get a bit closer without physically moving the camera",
    ),
    (
        "set_zoom", {"level": 5.0},
        "Set zoom to five times",
        "Five times magnification please, I need to see more detail",
        "That structure is too small to see at this distance, get closer",
    ),
    (
        "set_focus_distance", {"distance_m": 0.5},
        "Lock focus at fifty centimeters",
        "Lock focus at about half a meter from the lens",
        "The subject is really close, around fifty centimeters away",
    ),
    (
        "set_focus_distance", {"distance_m": 2.0},
        "Lock focus at two meters",
        "Uh, set focus distance to about two meters",
        "I'm standing roughly two meters back from the specimen tray",
    ),
    (
        "set_iso", {"iso": 400},
        "Set ISO to four hundred",
        "ISO four hundred should work for this lighting",
        "Normal indoor brightness, I need moderate sensitivity",
    ),
    (
        "set_iso", {"iso": 1600},
        "Set ISO to sixteen hundred",
        "Very low light in here, I need sixteen hundred ISO",
        "The room is almost dark, I need maximum sensitivity to capture anything",
    ),
    (
        "set_shutter_speed", {"speed": "1/100"},
        "Set shutter speed to one hundredth",
        "One hundredth of a second shutter speed please",
        "Standard handheld speed to avoid motion blur",
    ),
    (
        "set_shutter_speed", {"speed": "2s"},
        "Set shutter speed to two seconds",
        "I need a two second long exposure for this fluorescence shot",
        "Low intensity fluorescence, the sensor needs a lot of time to collect light",
    ),

    # ── Tier 4: multi-arg ───────────────────────────────────────────────────
    (
        "start_recording", {"label": "experiment_1"},
        "Start recording, label it experiment one",
        "Start a recording session and call it experiment one",
        "I need to capture this process on video, this is experiment one",
    ),
    (
        "start_recording", {"label": "trial_2", "max_duration_s": 60, "codec": "h265"},
        "Start recording trial two, sixty second limit, H two sixty five codec",
        "Record trial two, cap it at sixty seconds, use H265 for compression",
        "Trial two is starting now, keep it under a minute, H265 please",
    ),
    (
        "start_documentation", {"label": "sample_analysis"},
        "Begin documentation session, call it sample analysis",
        "Start a documentation session for sample analysis",
        "I want to start logging this sample systematically",
    ),
    (
        "start_documentation", {"label": "protein_gel", "project_id": "PROJ-A"},
        "Begin documentation for protein gel, project A",
        "Um, start documenting the protein gel run, this is project A",
        "We're running the gel now, I need to start the project A documentation",
    ),
    (
        "set_capture_burst", {"enabled": True, "count": 5},
        "Enable burst mode, five frames",
        "Turn on burst capture, I want five shots per trigger",
        "I need multiple exposures to catch the exact moment",
    ),
    (
        "set_capture_burst", {"enabled": False},
        "Disable burst mode",
        "Turn off burst, I want single shots only",
        "I don't need rapid fire anymore, back to single frame",
    ),
    (
        "annotate_frame", {"text": "time point zero", "position": "bottom"},
        "Annotate bottom of frame: time point zero",
        "Add a text note at the bottom of the frame, time point zero",
        "Mark this as the start of the experiment at the bottom",
    ),
    (
        "annotate_frame", {"text": "contamination suspected", "position": "center"},
        "Add center annotation: contamination suspected",
        "Put a warning in the middle of the frame, contamination suspected",
        "Something looks off about this sample, flag it in the center of the image",
    ),
    (
        "take_photo", {"label": "reference"},
        "Take a photo, label it reference",
        "Take a reference photo and label it reference",
        "Capture this as the baseline image for comparison",
    ),
    (
        "take_photo", {"flash_override": "off"},
        "Take a photo with flash off",
        "Take a photo, but override the flash to off for this one",
        "The flash would disturb the specimen, just capture without it",
    ),

    # ── Tier 5: nested / compound ───────────────────────────────────────────
    (
        "configure_capture", {"camera": "back", "resolution": "high", "flash": "off"},
        "Back camera, high resolution, flash off",
        "Set up the back camera at high resolution with no flash",
        "Configure for detailed work: main camera, full quality, no artificial light",
    ),
    (
        "configure_capture", {"camera": "macro", "exposure_ev": -0.5, "resolution": "max"},
        "Macro camera, max resolution, exposure minus half",
        "Use the macro lens, maximum resolution, drop exposure half a stop",
        "Close-up specimen work: close-up lens, highest quality, slightly underexposed",
    ),
    (
        "configure_session", {"project_id": "XYZ-001"},
        "Configure session for project XYZ zero zero one",
        "Set up a session, the project ID is XYZ zero zero one",
        "I'm starting a new project today, ID is XYZ zero zero one",
    ),
    (
        "configure_session", {"project_id": "BIO-42", "experimenter": "Martin", "auto_documentation": True},
        "Session for project BIO forty two, experimenter Martin, auto documentation on",
        "Configure session: project BIO forty two, Martin is the experimenter, enable auto documentation",
        "Set everything up for my experiment: BIO forty two, I'm Martin, document automatically",
    ),
    (
        "apply_preset", {"preset_name": "macro"},
        "Apply the macro preset",
        "Can you apply the macro preset for me",
        "I want the close-up photography settings loaded",
    ),
    (
        "apply_preset", {"preset_name": "landscape"},
        "Apply the landscape preset",
        "Let's use the landscape preset for this outdoor shot",
        "We're doing field documentation today, use the right preset",
    ),
    (
        "export_session", {"format": "pdf"},
        "Export session as PDF",
        "Export everything from this session as a PDF",
        "I need to include this data in a report, get me a PDF",
    ),
    (
        "export_session", {"format": "csv", "include_raw": True},
        "Export session as CSV with raw data",
        "Export as CSV and include the raw sensor readings",
        "I need all the numerical data for my analysis, CSV with everything",
    ),
    (
        "sync_to_eln", {"experiment_id": "EXP-001"},
        "Sync to lab notebook, experiment EXP zero zero one",
        "Push this session to the electronic lab notebook, experiment EXP zero zero one",
        "Upload everything to the notebook for EXP zero zero one",
    ),
    (
        "sync_to_eln", {"experiment_id": "EXP-042", "notes": "second replicate"},
        "Sync experiment EXP zero forty two to lab notebook, note: second replicate",
        "Sync to notebook for experiment EXP zero forty two, add a note that this is the second replicate",
        "Push replicate two data to EXP zero forty two in the notebook",
    ),

    # ── Tier 6: advanced ────────────────────────────────────────────────────
    (
        "toggle_hdr", {"on": True},
        "Enable HDR mode",
        "Turn on HDR, I have a high contrast scene",
        "The bright background is killing the foreground detail, I need full dynamic range",
    ),
    (
        "toggle_hdr", {"on": False},
        "Disable HDR mode",
        "Turn off HDR, it's making the colors look unnatural",
        "The HDR processing is too aggressive for scientific imaging",
    ),
    (
        "set_color_profile", {"profile": "display_p3"},
        "Set color profile to Display P3",
        "Switch the color profile to Display P3 for wider gamut",
        "I need accurate colors for this fluorescence imaging, P3 please",
    ),
    (
        "set_color_profile", {"profile": "raw"},
        "Set color profile to raw",
        "I need unprocessed color data, switch to raw profile",
        "No color processing at all, I'll calibrate in post",
    ),
    (
        "set_review_mode", {"duration": "3s"},
        "Show captured photos for three seconds",
        "After each shot, show me the photo for three seconds",
        "I want a quick glance at each image before moving on",
    ),
    (
        "set_review_mode", {"duration": "off"},
        "Disable photo review",
        "Turn off the review screen, I don't want to see each photo after capture",
        "Stop showing me the preview after every shot, it slows me down",
    ),
    (
        "toggle_location_tags", {"on": False},
        "Disable GPS location tags",
        "Turn off the GPS location embedding in the photos",
        "No location data in this dataset, we need to protect site confidentiality",
    ),
    (
        "toggle_location_tags", {"on": True},
        "Enable GPS location tags",
        "Turn on location tagging for these field samples",
        "I need to know exactly where each specimen was photographed",
    ),
    (
        "set_video_fps", {"fps": "60"},
        "Set video to sixty frames per second",
        "Switch video frame rate to sixty FPS",
        "I need smooth slow motion playback, set the frame rate high",
    ),
    (
        "set_video_fps", {"fps": "30"},
        "Set video to thirty frames per second",
        "Thirty frames per second for the video recording",
        "Standard frame rate for documentation video",
    ),
]


def make_prompts() -> list[dict]:
    entries = []
    n = len(PROMPTS)

    for i, (tool, args, v1, v2, v3) in enumerate(PROMPTS):
        tier = (i // 10) + 1  # approximate tier from position

        # v1 — smoke prompt, clean phrasing
        entries.append({
            "id": f"p{i+1:03d}",
            "text": v1,
            "expected_tool": tool,
            "expected_args": args,
            "tier_min": tier,
            "split": "train",
            "difficulty": "v1",
            "smoke": True,
            "negative": False,
        })

    for i, (tool, args, v1, v2, v3) in enumerate(PROMPTS):
        tier = (i // 10) + 1

        # v2 — natural/hedged phrasing
        entries.append({
            "id": f"p{n+i+1:03d}",
            "text": v2,
            "expected_tool": tool,
            "expected_args": args,
            "tier_min": tier,
            "split": "train",
            "difficulty": "v2",
            "smoke": False,
            "negative": False,
        })

    for i, (tool, args, v1, v2, v3) in enumerate(PROMPTS):
        tier = (i // 10) + 1

        # v3 — indirect/implicit, holdout
        entries.append({
            "id": f"h{i+1:03d}",
            "text": v3,
            "expected_tool": tool,
            "expected_args": args,
            "tier_min": tier,
            "split": "holdout",
            "difficulty": "v3",
            "smoke": False,
            "negative": False,
        })

    return entries


# ── Diverse manifest ──────────────────────────────────────────────────────────
# One positive prompt per distinct tool covering all ALL_TOOLS[:20] shapes.
# Six negative prompts that sound tool-related but must not trigger any tool.
# IDs are prefixed "d_" to avoid clashes with the standard manifest.
# Audio lives under prompts/audio/<voice>/diverse/ for the same reason.

DIVERSE_POSITIVES = [
    # tier=1 boolean toggles (tools 1-5)
    ("toggle_flash",         {"on": True},
     "Turn on the flash"),
    ("toggle_grid_overlay",  {"on": True},
     "Show the grid overlay"),
    ("toggle_macro_mode",    {"on": True},
     "Switch to macro focus mode"),
    ("toggle_stabilization", {"on": False},
     "Turn off image stabilization, I'm on a tripod"),
    ("toggle_voice_captions",{"on": True},
     "Enable voice captions"),

    # tier=2 single enum (tools 6-10)
    ("switch_camera",   {"lens": "front"},
     "Switch to the front camera"),
    ("set_resolution",  {"resolution": "high"},
     "Set resolution to high quality"),
    ("set_white_balance", {"preset": "fluorescent"},
     "Set white balance to fluorescent"),
    ("set_timer",       {"delay": "5s"},
     "Set the capture timer to five seconds"),
    ("set_aspect_ratio",{"ratio": "1:1"},
     "Set aspect ratio to square"),

    # tier=3 numeric (tools 11-15)
    ("set_exposure",       {"ev": -1.0},
     "Lower exposure by one stop"),
    ("set_zoom",           {"level": 3.0},
     "Zoom to three times magnification"),
    ("set_focus_distance", {"distance_m": 0.5},
     "Lock focus at fifty centimeters"),
    ("set_iso",            {"iso": 1600},
     "Set ISO to sixteen hundred"),
    ("set_shutter_speed",  {"speed": "1/100"},
     "Set shutter speed to one hundredth of a second"),

    # tier=4 multi-arg (tools 16-20)
    ("start_recording",    {"label": "experiment_1"},
     "Start recording, label it experiment one"),
    ("start_documentation",{"label": "sample_analysis"},
     "Begin a documentation session for sample analysis"),
    ("set_capture_burst",  {"enabled": True, "count": 5},
     "Enable burst mode with five frames"),
    ("annotate_frame",     {"text": "time point zero", "position": "bottom"},
     "Add a note at the bottom: time point zero"),
    ("take_photo",         {"label": "reference"},
     "Take a reference photo"),
]

DIVERSE_NEGATIVES = [
    "What's the weather like outside today",
    "Tell me a bit about the history of microscopy",
    "Can you read me back the last annotation I added",
    "What's the current battery level on this device",
    "How many photos have I taken this session",
    "Describe what you can see in the current frame",
]


def make_diverse_prompts() -> list[dict]:
    entries = []

    for i, (tool, args, text) in enumerate(DIVERSE_POSITIVES):
        entries.append({
            "id": f"d_p{i+1:03d}",
            "text": text,
            "expected_tool": tool,
            "expected_args": args,
            "split": "train",
            "difficulty": "v1",
            "smoke": i < 5,
            "negative": False,
            "audio_subdir": "diverse",
        })

    for i, text in enumerate(DIVERSE_NEGATIVES):
        entries.append({
            "id": f"d_n{i+1:03d}",
            "text": text,
            "expected_tool": None,
            "expected_args": None,
            "split": "train",
            "difficulty": "v1",
            "smoke": False,
            "negative": True,
            "audio_subdir": "diverse",
        })

    return entries


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["standard", "diverse"], default="standard")
    args = parser.parse_args()

    if args.mode == "diverse":
        out = Path(__file__).parent.parent / "prompts" / "manifest_diverse.json"
        prompts = make_diverse_prompts()
        with open(out, "w") as f:
            json.dump(prompts, f, indent=2)
        print(f"Wrote {len(prompts)} prompts to {out}")
        pos = sum(1 for p in prompts if not p["negative"])
        neg = sum(1 for p in prompts if p["negative"])
        tools = {p["expected_tool"] for p in prompts if p["expected_tool"]}
        print(f"  Positive: {pos} ({len(tools)} distinct tools), Negative: {neg}")
    else:
        out = Path(__file__).parent.parent / "prompts" / "manifest_v2.json"
        prompts = make_prompts()
        with open(out, "w") as f:
            json.dump(prompts, f, indent=2)
        print(f"Wrote {len(prompts)} prompts to {out}")

        by_tool = {}
        for p in prompts:
            by_tool.setdefault(p["expected_tool"], []).append(p["difficulty"])
        print(f"\nTools covered: {len(by_tool)}")
        smoke = [p for p in prompts if p.get("smoke")]
        print(f"Smoke prompts: {len(smoke)}")
        print(f"\nDifficulty distribution:")
        for diff in ("v1", "v2", "v3"):
            count = sum(1 for p in prompts if p["difficulty"] == diff)
            print(f"  {diff}: {count}")


if __name__ == "__main__":
    main()
