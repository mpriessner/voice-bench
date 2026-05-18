from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class DummyTool:
    name: str
    description: str
    parameters: dict  # JSON Schema object
    tier: int
    _call_log: list = field(default_factory=list, repr=False)

    def __call__(self, turn_id: str, **kwargs: Any) -> dict:
        self._call_log.append({"turn_id": turn_id, "args": kwargs, "ts": time.time()})
        return {"result": "ok", "tool": self.name}

    def to_gemini_declaration(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


# ── Tier 1: pure boolean toggles ──────────────────────────────────────────────

TIER_1_TOOLS: list[DummyTool] = [
    DummyTool(
        name="toggle_flash",
        description="Turn the camera flash on or off.",
        parameters={
            "type": "object",
            "properties": {
                "on": {
                    "type": "boolean",
                    "description": "True to turn flash on, false to turn off.",
                }
            },
            "required": ["on"],
        },
        tier=1,
    ),
    DummyTool(
        name="toggle_grid_overlay",
        description="Show or hide the composition grid overlay on the camera viewfinder.",
        parameters={
            "type": "object",
            "properties": {
                "on": {
                    "type": "boolean",
                    "description": "True to show the grid, false to hide it.",
                }
            },
            "required": ["on"],
        },
        tier=1,
    ),
    DummyTool(
        name="toggle_macro_mode",
        description="Enable or disable macro (close-up) focus mode.",
        parameters={
            "type": "object",
            "properties": {
                "on": {
                    "type": "boolean",
                    "description": "True to enable macro mode, false to disable.",
                }
            },
            "required": ["on"],
        },
        tier=1,
    ),
    DummyTool(
        name="toggle_stabilization",
        description="Enable or disable optical image stabilization.",
        parameters={
            "type": "object",
            "properties": {
                "on": {
                    "type": "boolean",
                    "description": "True to enable stabilization, false to disable.",
                }
            },
            "required": ["on"],
        },
        tier=1,
    ),
    DummyTool(
        name="toggle_voice_captions",
        description="Enable or disable voice captions displayed on screen.",
        parameters={
            "type": "object",
            "properties": {
                "on": {
                    "type": "boolean",
                    "description": "True to enable captions, false to disable.",
                }
            },
            "required": ["on"],
        },
        tier=1,
    ),
]

# ── Tier 2: single enum arg ────────────────────────────────────────────────────

TIER_2_TOOLS: list[DummyTool] = [
    DummyTool(
        name="switch_camera",
        description="Switch to a different camera lens.",
        parameters={
            "type": "object",
            "properties": {
                "lens": {
                    "type": "string",
                    "enum": ["front", "back", "macro"],
                    "description": "Which camera lens to switch to.",
                }
            },
            "required": ["lens"],
        },
        tier=2,
    ),
    DummyTool(
        name="set_resolution",
        description="Set the capture resolution.",
        parameters={
            "type": "object",
            "properties": {
                "resolution": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "max"],
                    "description": "Desired capture resolution.",
                }
            },
            "required": ["resolution"],
        },
        tier=2,
    ),
    DummyTool(
        name="set_white_balance",
        description="Set white balance preset.",
        parameters={
            "type": "object",
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["auto", "daylight", "cloudy", "fluorescent", "incandescent"],
                    "description": "White balance preset name.",
                }
            },
            "required": ["preset"],
        },
        tier=2,
    ),
    DummyTool(
        name="set_timer",
        description="Set the capture timer delay.",
        parameters={
            "type": "object",
            "properties": {
                "delay": {
                    "type": "string",
                    "enum": ["off", "3s", "5s", "10s"],
                    "description": "Timer delay value.",
                }
            },
            "required": ["delay"],
        },
        tier=2,
    ),
    DummyTool(
        name="set_aspect_ratio",
        description="Set the image aspect ratio.",
        parameters={
            "type": "object",
            "properties": {
                "ratio": {
                    "type": "string",
                    "enum": ["1:1", "4:3", "16:9", "full"],
                    "description": "Aspect ratio to use.",
                }
            },
            "required": ["ratio"],
        },
        tier=2,
    ),
]

# ── Tier 3: numeric + enum ─────────────────────────────────────────────────────

TIER_3_TOOLS: list[DummyTool] = [
    DummyTool(
        name="set_exposure",
        description="Adjust exposure compensation.",
        parameters={
            "type": "object",
            "properties": {
                "ev": {
                    "type": "number",
                    "description": "Exposure value in EV stops, range -2.0 to +2.0.",
                }
            },
            "required": ["ev"],
        },
        tier=3,
    ),
    DummyTool(
        name="set_zoom",
        description="Set optical or digital zoom level.",
        parameters={
            "type": "object",
            "properties": {
                "level": {
                    "type": "number",
                    "description": "Zoom multiplier, range 1.0 to 10.0.",
                }
            },
            "required": ["level"],
        },
        tier=3,
    ),
    DummyTool(
        name="set_focus_distance",
        description="Lock focus at a specific distance.",
        parameters={
            "type": "object",
            "properties": {
                "distance_m": {
                    "type": "number",
                    "description": "Focus distance in metres, 0.1 to 10.0.",
                }
            },
            "required": ["distance_m"],
        },
        tier=3,
    ),
    DummyTool(
        name="set_iso",
        description="Set ISO sensitivity.",
        parameters={
            "type": "object",
            "properties": {
                "iso": {
                    "type": "integer",
                    "description": "ISO value: 50, 100, 200, 400, 800, 1600, 3200, or 6400.",
                }
            },
            "required": ["iso"],
        },
        tier=3,
    ),
    DummyTool(
        name="set_shutter_speed",
        description="Set shutter speed for manual exposure.",
        parameters={
            "type": "object",
            "properties": {
                "speed": {
                    "type": "string",
                    "description": "Shutter speed as a fraction or seconds, e.g. '1/100' or '2s'.",
                }
            },
            "required": ["speed"],
        },
        tier=3,
    ),
]

# ── Tier 4: multi-arg ──────────────────────────────────────────────────────────

TIER_4_TOOLS: list[DummyTool] = [
    DummyTool(
        name="start_recording",
        description="Start a video recording session.",
        parameters={
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Experiment label for the recording."},
                "max_duration_s": {"type": "integer", "description": "Maximum duration in seconds."},
                "codec": {"type": "string", "enum": ["h264", "h265", "vp9"], "description": "Video codec."},
            },
            "required": ["label"],
        },
        tier=4,
    ),
    DummyTool(
        name="start_documentation",
        description="Begin a documentation session with a label and optional project.",
        parameters={
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Documentation session label."},
                "project_id": {"type": "string", "description": "Optional project identifier."},
            },
            "required": ["label"],
        },
        tier=4,
    ),
    DummyTool(
        name="set_capture_burst",
        description="Configure burst capture mode.",
        parameters={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "Enable or disable burst mode."},
                "count": {"type": "integer", "description": "Number of frames in a burst (2-20)."},
                "interval_ms": {"type": "integer", "description": "Interval between frames in ms."},
            },
            "required": ["enabled"],
        },
        tier=4,
    ),
    DummyTool(
        name="annotate_frame",
        description="Add a text annotation to the current frame.",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Annotation text."},
                "position": {"type": "string", "enum": ["top", "center", "bottom"], "description": "Vertical position."},
            },
            "required": ["text"],
        },
        tier=4,
    ),
    DummyTool(
        name="take_photo",
        description="Capture a single photo with optional label.",
        parameters={
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Optional label to embed in metadata."},
                "flash_override": {"type": "string", "enum": ["auto", "on", "off"], "description": "Flash override."},
            },
            "required": [],
        },
        tier=4,
    ),
]

# ── Tier 5: nested object ──────────────────────────────────────────────────────

TIER_5_TOOLS: list[DummyTool] = [
    DummyTool(
        name="configure_capture",
        description="Apply a full capture configuration in one call.",
        parameters={
            "type": "object",
            "properties": {
                "camera": {"type": "string", "enum": ["front", "back", "macro"]},
                "exposure_ev": {"type": "number"},
                "flash": {"type": "string", "enum": ["auto", "on", "off"]},
                "resolution": {"type": "string", "enum": ["low", "medium", "high", "max"]},
                "label": {"type": "string"},
            },
            "required": [],
        },
        tier=5,
    ),
    DummyTool(
        name="configure_session",
        description="Configure the entire lab session.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "experimenter": {"type": "string"},
                "auto_documentation": {"type": "boolean"},
                "capture_interval_s": {"type": "integer"},
            },
            "required": ["project_id"],
        },
        tier=5,
    ),
    DummyTool(
        name="apply_preset",
        description="Apply a named capture preset.",
        parameters={
            "type": "object",
            "properties": {
                "preset_name": {"type": "string"},
                "overrides": {
                    "type": "object",
                    "description": "Key-value overrides on top of the preset.",
                },
            },
            "required": ["preset_name"],
        },
        tier=5,
    ),
    DummyTool(
        name="export_session",
        description="Export the current session data.",
        parameters={
            "type": "object",
            "properties": {
                "format": {"type": "string", "enum": ["json", "csv", "pdf"]},
                "include_raw": {"type": "boolean"},
                "destination": {"type": "string"},
            },
            "required": ["format"],
        },
        tier=5,
    ),
    DummyTool(
        name="sync_to_eln",
        description="Sync captured data to the electronic lab notebook.",
        parameters={
            "type": "object",
            "properties": {
                "experiment_id": {"type": "string"},
                "overwrite": {"type": "boolean"},
                "notes": {"type": "string"},
            },
            "required": ["experiment_id"],
        },
        tier=5,
    ),
]

ALL_TOOLS: list[DummyTool] = (
    TIER_1_TOOLS + TIER_2_TOOLS + TIER_3_TOOLS + TIER_4_TOOLS + TIER_5_TOOLS
)


def load_tools(count: int) -> list[DummyTool]:
    """Return first `count` tools in tier order (deterministic)."""
    if count > len(ALL_TOOLS):
        raise ValueError(f"Only {len(ALL_TOOLS)} tools available, requested {count}")
    return ALL_TOOLS[:count]
