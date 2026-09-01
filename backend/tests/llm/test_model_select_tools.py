import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.utils import _build_model_select_modalities


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
