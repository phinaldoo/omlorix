import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.utils import _build_model_select_modalities, _has_user_acp_terminal


def test_model_select_modalities_hide_mcp_tools():
    """MCP markers and generated public names must not become preview chips."""
    input_formats, output_formats, tools = _build_model_select_modalities(
        {},
        [
            "web_search",
            "mcp",
            "mcp_notion_search",
            "image_generation",
            "weather",
        ],
    )

    assert input_formats == ["text"]
    assert output_formats == ["text", "image"]
    assert tools == ["web_search", "weather"]


def test_terminal_capability_is_limited_to_user_owned_acp_profiles():
    """Do not show the terminal button for ordinary or administrator ACP models."""
    personal_meta = {
        "user_managed": True,
        "owner_user_id": "user-one",
        "acp_profile_id": "profile-one",
    }
    assert _has_user_acp_terminal("acp", personal_meta) is True
    assert _has_user_acp_terminal("openai", personal_meta) is False
    assert _has_user_acp_terminal("acp", {"user_managed": False, "acp_profile_id": "profile-one"}) is False
