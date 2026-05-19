"""Centralized adapter construction — single source of truth for agent → adapter mapping."""


def build_adapter(agent: str, force_tool_call: bool = True):
    """Return a configured adapter instance for the given agent name."""
    if agent == "gemini-live":
        from .gemini_live import GeminiLiveAdapter
        return GeminiLiveAdapter(agent_name="gemini-live", force_tool_call=force_tool_call)

    if agent == "openai-realtime":
        from .openai_realtime import OpenAIRealtimeAdapter
        # v1 pins the previous-generation gpt-realtime model for an honest baseline.
        return OpenAIRealtimeAdapter(model="gpt-realtime", agent_name="openai-realtime", force_tool_call=force_tool_call)

    if agent == "claude-opus":
        from .claude_text import ClaudeTextAdapter
        return ClaudeTextAdapter(model="claude-opus-4-7", agent_name="claude-opus")

    if agent == "claude-sonnet":
        from .claude_text import ClaudeTextAdapter
        return ClaudeTextAdapter(model="claude-sonnet-4-6", agent_name="claude-sonnet")

    if agent == "gpt-text":
        from .gpt_text import GPTTextAdapter
        return GPTTextAdapter(model="gpt-4o", agent_name="gpt-text")

    if agent == "gpt-4o":
        from .gpt_text import GPTTextAdapter
        return GPTTextAdapter(model="gpt-4o", agent_name="gpt-4o")

    if agent == "gpt-5":
        from .gpt_text import GPTTextAdapter
        return GPTTextAdapter(model="gpt-5", agent_name="gpt-5")

    if agent == "gemini-pro":
        from .gemini_text import GeminiTextAdapter
        return GeminiTextAdapter(model="gemini-3.1-pro-preview", agent_name="gemini-pro")

    if agent == "gemini-flash":
        from .gemini_text import GeminiTextAdapter
        return GeminiTextAdapter(model="gemini-3.1-flash-lite", agent_name="gemini-flash")

    if agent == "gemini-3-flash":
        from .gemini_text import GeminiTextAdapter
        return GeminiTextAdapter(model="gemini-3-flash-preview", agent_name="gemini-3-flash")

    if agent == "gemini-live-v2":
        from .gemini_live import GeminiLiveAdapter
        return GeminiLiveAdapter(agent_name="gemini-live-v2", force_tool_call=force_tool_call)

    if agent == "openai-realtime-v2":
        from .openai_realtime import OpenAIRealtimeAdapter
        # v2 uses gpt-realtime-2 (released May 7, 2026 — GPT-5 reasoning enabled).
        return OpenAIRealtimeAdapter(model="gpt-realtime-2", agent_name="openai-realtime-v2", force_tool_call=force_tool_call)

    if agent == "openai-realtime-swap":
        from .openai_realtime_swap import OpenAIRealtimeSwapAdapter
        from ..toolsets import TOOLSETS, build_core
        return OpenAIRealtimeSwapAdapter(toolsets=TOOLSETS, core_tools=build_core())

    if agent == "gemini-live-swap":
        from .gemini_live_swap import GeminiLiveSwapAdapter
        from ..toolsets import TOOLSETS, build_core
        return GeminiLiveSwapAdapter(toolsets=TOOLSETS, core_tools=build_core())

    raise NotImplementedError(f"No adapter registered for agent: {agent!r}")
