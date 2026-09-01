from typing import Any, Set

# Tools that should be hidden from user events
tools_hidden_from_user_events: Set[str] = set()

# Tools that should not yield arguments in streaming
tools_not_yield_arguments = [
    "image_generation",
    "video_generation",
    "audio_generation",
    "music_generation",
    "flashcards",
    "quiz",
    "slide_presentation",
]


def is_tool_hidden_from_user(tool_name: str | None) -> bool:
    return bool(tool_name and str(tool_name).strip() in tools_hidden_from_user_events)


def should_hide_tool_call_from_user(tool_name: str | None, arguments: Any = None) -> bool:
    """Return true for tool calls that should not create visible chat UI blocks."""
    return is_tool_hidden_from_user(tool_name)
