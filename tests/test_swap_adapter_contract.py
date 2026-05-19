"""
Offline contract tests for the swap adapters.

No API keys needed — these only test state management logic, not network calls.
"""

import pytest


def _make_gemini_adapter(initial_toolset="camera_basics"):
    from voice_bench.adapters.gemini_live_swap import GeminiLiveSwapAdapter
    from voice_bench.toolsets import TOOLSETS, build_core
    return GeminiLiveSwapAdapter(
        toolsets=TOOLSETS,
        core_tools=build_core(),
        initial_toolset=initial_toolset,
        api_key="TEST_KEY_PLACEHOLDER",
    )


def _make_openai_adapter(initial_toolset="camera_basics"):
    from voice_bench.adapters.openai_realtime_swap import OpenAIRealtimeSwapAdapter
    from voice_bench.toolsets import TOOLSETS, build_core
    return OpenAIRealtimeSwapAdapter(
        toolsets=TOOLSETS,
        core_tools=build_core(),
        initial_toolset=initial_toolset,
        api_key="TEST_KEY_PLACEHOLDER",
    )


SWAP_META_TOOLS = {"switch_toolset", "list_toolsets"}
CORE_TOOL_NAMES = {"take_photo", "toggle_flash", "switch_camera", "start_documentation"}
ALL_ALWAYS_LOADED = SWAP_META_TOOLS | CORE_TOOL_NAMES


class TestGeminiLiveSwapAdapterContract:

    def test_visible_tools_always_contains_core(self):
        adapter = _make_gemini_adapter()
        names = {t.name for t in adapter._visible_tools()}
        assert ALL_ALWAYS_LOADED.issubset(names), (
            f"Missing always-loaded tools: {ALL_ALWAYS_LOADED - names}"
        )

    def test_visible_tools_initial_pool_is_camera_basics(self):
        adapter = _make_gemini_adapter("camera_basics")
        names = {t.name for t in adapter._visible_tools()}
        assert "toggle_grid_overlay" in names
        assert "set_resolution" in names
        assert "set_microscope_objective" not in names

    def test_visible_tools_after_manual_pool_change(self):
        adapter = _make_gemini_adapter("camera_basics")
        adapter._current_toolset = "lab_imaging"
        names = {t.name for t in adapter._visible_tools()}
        assert "set_microscope_objective" in names
        assert "toggle_grid_overlay" not in names

    def test_all_toolsets_within_size_limit(self):
        from voice_bench.toolsets import TOOLSETS, build_core
        core = build_core()
        for pool_name, pool in TOOLSETS.items():
            total = len(core) + len(pool)
            assert total <= 23, (
                f"Toolset {pool_name!r} produces {total} visible tools; max is 23"
            )

    def test_invalid_initial_toolset_raises(self):
        from voice_bench.adapters.gemini_live_swap import GeminiLiveSwapAdapter
        from voice_bench.toolsets import TOOLSETS, build_core
        with pytest.raises(ValueError, match="Unknown initial_toolset"):
            GeminiLiveSwapAdapter(
                toolsets=TOOLSETS,
                core_tools=build_core(),
                initial_toolset="nonexistent_pool",
                api_key="TEST_KEY_PLACEHOLDER",
            )

    def test_circuit_breaker_starts_unlocked(self):
        adapter = _make_gemini_adapter()
        assert adapter._fallback_locked is False

    def test_resumption_handle_starts_none(self):
        adapter = _make_gemini_adapter()
        assert adapter._resumption_handle is None

    def test_lab_imaging_initial_toolset(self):
        adapter = _make_gemini_adapter("lab_imaging")
        names = {t.name for t in adapter._visible_tools()}
        assert "set_microscope_objective" in names
        assert "toggle_grid_overlay" not in names


class TestOpenAIRealtimeSwapAdapterContract:

    def test_visible_tools_always_contains_core(self):
        adapter = _make_openai_adapter()
        names = {t.name for t in adapter._visible_tools()}
        assert ALL_ALWAYS_LOADED.issubset(names), (
            f"Missing always-loaded tools: {ALL_ALWAYS_LOADED - names}"
        )

    def test_visible_tools_initial_pool_is_camera_basics(self):
        adapter = _make_openai_adapter("camera_basics")
        names = {t.name for t in adapter._visible_tools()}
        assert "toggle_grid_overlay" in names
        assert "set_microscope_objective" not in names

    def test_all_toolsets_within_size_limit(self):
        from voice_bench.toolsets import TOOLSETS, build_core
        core = build_core()
        for pool_name, pool in TOOLSETS.items():
            total = len(core) + len(pool)
            assert total <= 23


class TestSwapRunnerFactory:

    def test_make_adapter_openai(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        from voice_bench.swap_runner import _make_adapter
        adapter = _make_adapter("openai-realtime-swap", "camera_basics")
        assert adapter._current_toolset == "camera_basics"

    def test_make_adapter_gemini(self):
        from voice_bench.swap_runner import _make_adapter
        adapter = _make_adapter("gemini-live-swap", "lab_imaging")
        assert adapter._current_toolset == "lab_imaging"

    def test_make_adapter_unknown_raises(self):
        from voice_bench.swap_runner import _make_adapter
        with pytest.raises(ValueError, match="Unknown swap agent"):
            _make_adapter("bogus-agent", "camera_basics")

    def test_system_prompt_loaded_per_agent(self):
        from voice_bench.swap_runner import _load_system_prompt
        openai_prompt = _load_system_prompt("openai-realtime-swap")
        gemini_prompt = _load_system_prompt("gemini-live-swap")
        assert len(openai_prompt) > 0
        assert len(gemini_prompt) > 0


class TestModelsSwapFields:

    def test_turn_result_has_swap_events(self):
        from voice_bench.models import TurnResult, TurnTimeline, TerminalReason
        tl = TurnTimeline(turn_id="t1", agent="gemini-live-swap", prompt_id="p1")
        result = TurnResult(
            timeline=tl, tool_calls=[], raw_events=[],
            transcripts={"user": "", "ai": ""},
            terminal_reason=TerminalReason.TURN_COMPLETE,
        )
        assert result.swap_events == []

    def test_swap_mechanism_ms_computed(self):
        from voice_bench.models import TurnTimeline
        import time
        tl = TurnTimeline(turn_id="t1", agent="gemini-live-swap", prompt_id="p1")
        tl.ts_swap_request = 1000.0
        tl.ts_swap_session_opened = 1003.5
        assert tl.swap_mechanism_ms == 3500

    def test_swap_ux_delay_ms_computed(self):
        from voice_bench.models import TurnTimeline
        tl = TurnTimeline(turn_id="t1", agent="gemini-live-swap", prompt_id="p1")
        tl.ts_swap_session_opened = 1003.5
        tl.ts_swap_ack = 1007.0
        assert tl.swap_ux_delay_ms == 3500

    def test_swap_mechanism_ms_none_when_missing(self):
        from voice_bench.models import TurnTimeline
        tl = TurnTimeline(turn_id="t1", agent="gemini-live-swap", prompt_id="p1")
        assert tl.swap_mechanism_ms is None

    def test_turn_result_to_dict_includes_swap_events(self):
        from voice_bench.models import TurnResult, TurnTimeline, TerminalReason
        tl = TurnTimeline(turn_id="t1", agent="gemini-live-swap", prompt_id="p1")
        result = TurnResult(
            timeline=tl, tool_calls=[], raw_events=[],
            transcripts={"user": "", "ai": ""},
            terminal_reason=TerminalReason.TURN_COMPLETE,
            swap_events=[{"from_pool": "camera_basics", "to_pool": "lab_imaging", "mechanism": "clean_restart"}],
        )
        d = result.to_dict()
        assert "swap_events" in d
        assert d["swap_events"][0]["mechanism"] == "clean_restart"
