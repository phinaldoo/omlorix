import sys
from types import ModuleType


class _HTTPException(Exception):
    def __init__(self, status_code=None, detail=None):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class _HTTPError(Exception):
    pass


if "fastapi" not in sys.modules:
    fastapi_stub = ModuleType("fastapi")
    fastapi_stub.HTTPException = _HTTPException
    sys.modules["fastapi"] = fastapi_stub

if "requests" not in sys.modules:
    requests_stub = ModuleType("requests")
    requests_stub.HTTPError = _HTTPError
    requests_stub.post = lambda *args, **kwargs: None
    sys.modules["requests"] = requests_stub

if "httpx" not in sys.modules:
    httpx_stub = ModuleType("httpx")
    httpx_stub.HTTPStatusError = type("HTTPStatusError", (Exception,), {})
    sys.modules["httpx"] = httpx_stub

from app.tools.websearch.combined import exa_combined


class _FakeResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "results": [
                {
                    "title": "Large result",
                    "url": "https://example.com/large",
                    "text": self._text,
                }
            ],
            "costDollars": {"total": 0.01},
        }


def test_exa_combined_search_uses_supported_text_request(monkeypatch):
    """Request full Exa text without sending unsupported character options."""

    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _FakeResponse("small result")

    monkeypatch.setattr(exa_combined.requests, "post", fake_post)

    exa_combined.exa_web_search_combined(
        api_key="exa-key",
        query="resource limits",
        max_results=1,
        search_type="neural",
        user_location="de",
    )

    assert captured["url"] == "https://api.exa.ai/search"
    assert captured["timeout"] == exa_combined.REQUEST_TIMEOUT_SECONDS
    assert captured["json"]["type"] == "auto"
    assert captured["json"]["userLocation"] == "DE"
    assert captured["json"]["contents"] == {"text": True}


def test_exa_combined_search_truncates_returned_text_locally(monkeypatch):
    oversized_text = "x" * (exa_combined.MAX_RETURNED_TEXT_CHARACTERS + 123)

    def fake_post(*args, **kwargs):
        return _FakeResponse(oversized_text)

    monkeypatch.setattr(exa_combined.requests, "post", fake_post)

    result = exa_combined.exa_web_search_combined(
        api_key="exa-key",
        query="large result",
        max_results=1,
    )

    returned_text = result["result"][0]["text"]
    assert returned_text == oversized_text[: exa_combined.MAX_RETURNED_TEXT_CHARACTERS]
    assert len(returned_text) == exa_combined.MAX_RETURNED_TEXT_CHARACTERS
