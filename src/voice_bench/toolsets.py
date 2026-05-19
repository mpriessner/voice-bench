"""Toolset definitions for the dynamic pool-swap benchmark."""

from .tools import DummyTool, ALL_TOOLS

# ── Swap primitive tools ──────────────────────────────────────────────────────

SWITCH_TOOLSET_TOOL = DummyTool(
    name="switch_toolset",
    description=(
        "Switch the active pool of specialized tools to a different category. "
        "Call this when the user's request requires tools not currently available. "
        "Use list_toolsets first if unsure which toolset to use."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name of the toolset to activate (e.g. 'camera_basics', 'lab_imaging').",
            }
        },
        "required": ["name"],
    },
    tier=0, category="app",
)

LIST_TOOLSETS_TOOL = DummyTool(
    name="list_toolsets",
    description=(
        "List available toolset names and their descriptions. "
        "Call this when unsure which toolset contains the tool you need."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    tier=0, category="app",
)

SWAP_PRIMITIVES: list[DummyTool] = [SWITCH_TOOLSET_TOOL, LIST_TOOLSETS_TOOL]

# ── Core tool names (always loaded, in addition to swap primitives) ────────────

CORE_TOOL_NAMES: list[str] = [
    "take_photo",
    "toggle_flash",
    "switch_camera",
    "start_documentation",
]

# ── Toolset pool definitions ──────────────────────────────────────────────────

_CAMERA_BASICS_NAMES: list[str] = [
    "toggle_grid_overlay", "toggle_macro_mode", "toggle_stabilization",
    "toggle_voice_captions", "set_resolution", "set_white_balance",
    "set_timer", "set_aspect_ratio", "configure_capture", "apply_preset",
]

_CAMERA_ADVANCED_NAMES: list[str] = [
    "set_exposure", "set_zoom", "set_iso", "set_shutter_speed",
    "toggle_hdr", "set_color_profile", "set_review_mode", "toggle_location_tags",
    "set_video_fps", "set_capture_burst", "set_capture_format", "toggle_watermark",
    "set_noise_reduction", "set_sharpening", "toggle_portrait_mode", "set_bokeh_strength",
    "set_live_histogram",
]

_LAB_IMAGING_NAMES: list[str] = [
    "set_focus_distance", "annotate_frame",
    "toggle_ruler_overlay", "toggle_scale_bar", "set_microscope_objective",
    "toggle_temperature_display", "set_temperature_unit",
    "toggle_time_lapse", "set_time_lapse_interval",
    "set_scan_interval", "toggle_auto_capture", "set_calibration_target",
    "toggle_focus_peaking",
]

_LAB_DATA_NAMES: list[str] = [
    "start_recording", "configure_session", "export_session",
    "sync_to_eln", "set_sample_label", "set_experiment_notes",
    "set_capture_burst",
]

_ALL_BY_NAME: dict[str, DummyTool] = {t.name: t for t in ALL_TOOLS}


def _resolve(names: list[str]) -> list[DummyTool]:
    missing = [n for n in names if n not in _ALL_BY_NAME]
    if missing:
        raise ValueError(f"Unknown tool names in toolset: {missing}")
    return [_ALL_BY_NAME[n] for n in names]


def _validate_pool_sizes() -> None:
    for name, pool in TOOLSETS.items():
        if len(pool) > 17:
            raise ValueError(
                f"Toolset {name!r} has {len(pool)} tools; max is 17. "
                "Trim the pool to stay under the positional-confusion cliff."
            )


TOOLSETS: dict[str, list[DummyTool]] = {
    "camera_basics": _resolve(_CAMERA_BASICS_NAMES),
    "camera_advanced": _resolve(_CAMERA_ADVANCED_NAMES),
    "lab_imaging": _resolve(_LAB_IMAGING_NAMES),
    "lab_data": _resolve(_LAB_DATA_NAMES),
}

TOOLSET_DESCRIPTIONS: dict[str, str] = {
    "camera_basics": (
        "Basic camera controls: grid overlay, macro, stabilization, white balance, "
        "timer, aspect ratio, resolution, configure capture."
    ),
    "camera_advanced": (
        "Advanced camera controls: exposure, zoom, ISO, shutter speed, HDR, "
        "color profile, FPS, portrait mode, bokeh, histogram, noise reduction, sharpening."
    ),
    "lab_imaging": (
        "Lab imaging tools: focus distance, frame annotation, ruler/scale overlays, "
        "microscope objective, temperature display, time-lapse, auto-capture, calibration."
    ),
    "lab_data": (
        "Lab data management: video recording, session configuration, export, "
        "ELN sync, sample labels, experiment notes."
    ),
}

_validate_pool_sizes()


def build_core() -> list[DummyTool]:
    """Return the always-loaded core tool list (universals + swap primitives)."""
    universals = [_ALL_BY_NAME[n] for n in CORE_TOOL_NAMES if n in _ALL_BY_NAME]
    return universals + SWAP_PRIMITIVES


def build_pool(name: str) -> list[DummyTool]:
    """Return the tools for a named pool. Raises KeyError if unknown."""
    if name not in TOOLSETS:
        raise KeyError(f"Unknown toolset: {name!r}. Valid: {sorted(TOOLSETS)}")
    return list(TOOLSETS[name])


def build_visible_tools(pool_name: str) -> list[DummyTool]:
    """Return core + named pool (what the model sees at any given moment)."""
    return build_core() + build_pool(pool_name)
