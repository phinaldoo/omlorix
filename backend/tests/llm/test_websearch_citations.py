import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.websearch_citations import build_web_search_citations, collect_tool_result_citations


def test_build_web_search_citations_rejects_unsafe_urls_and_limits_text():
    long_title = "T" * 305
    long_content = "S" * 205

    assert build_web_search_citations(
        [
            {
                "url": " https://example.com/page ",
                "title": f" {long_title} ",
                "content": f" {long_content} ",
            },
            {
                "url": "javascript:alert(1)",
                "title": "unsafe",
                "content": "unsafe",
            },
            {
                "url": "data:text/html,<img src=x onerror=alert(1)>",
                "title": "unsafe",
                "content": "unsafe",
            },
        ]
    ) == [
        {
            "url": "https://example.com/page",
            "title": ("T" * 300) + "...",
            "snippet": ("S" * 200) + "...",
        }
    ]


def test_collect_tool_result_citations_normalizes_persisted_metadata():
    messages = [
        {
            "type": "tool_call_result",
            "meta": {
                "citations": [
                    {
                        "url": "http://example.test/a",
                        "title": " Title ",
                        "snippet": " Snippet ",
                        "extra": "is dropped",
                    },
                    {
                        "url": "http://example.test/a",
                        "title": "duplicate is dropped",
                    },
                    {
                        "url": "ftp://example.test/file",
                        "title": "unsafe scheme is dropped",
                    },
                ]
            },
        }
    ]

    assert collect_tool_result_citations(messages) == [
        {
            "url": "http://example.test/a",
            "title": "Title",
            "snippet": "Snippet",
        }
    ]
