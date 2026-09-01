from typing import Any
from ollama import Client

def ollama_web_search_combined(api_key: str, query: str, max_results: int) -> dict[str, Any]:
    try:
        client = Client(
            host="https://ollama.com",
            headers={"Authorization": "Bearer " + api_key},
        )
    except Exception as exc:
        raise Exception(f"Ollama client initialization failed: {exc}") from exc

    try:
        response = client.web_search(query, max_results)
    except Exception as exc:
        raise Exception(f"Ollama web search failed: {exc}")

    formatted = []
    for result in response.results:
        formatted.append({
            "title": result.title,
            "url": result.url,
            "content": result.content,
        })
    return {
        "result": formatted,
        "metadata": {
            "provider_combined": "ollama",
        },
    }
