from types import SimpleNamespace

from app.llmstats.router import get_llm_throughput_by_model


class _Query:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def with_entities(self, *args, **kwargs):
        return self

    def group_by(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return self._rows


class _DB:
    def __init__(self, query_rows):
        self._query_rows = iter(query_rows)

    def query(self, *args, **kwargs):
        return _Query(next(self._query_rows))


def test_throughput_uses_configured_display_name_with_recorded_name_fallback():
    throughput_rows = [
        SimpleNamespace(
            model_id="configured-model-id",
            model_name="provider/model-id",
            provider="openai",
            provider_id="provider-id",
            avg_throughput=42.125,
            min_throughput=30,
            max_throughput=50,
            sample_count=3,
        ),
        SimpleNamespace(
            model_id="deleted-model-id",
            model_name="provider/deleted-model",
            provider="openai",
            provider_id="provider-id",
            avg_throughput=20,
            min_throughput=20,
            max_throughput=20,
            sample_count=1,
        ),
    ]
    db = _DB(
        [
            throughput_rows,
            [("provider-id", "OpenAI Production")],
            [("configured-model-id", "GPT-5.6 Terra")],
        ]
    )

    result = get_llm_throughput_by_model(
        db=db,
        admin_user=SimpleNamespace(id="admin-id"),
        days=30,
        provider=None,
        provider_id=None,
    )

    assert [row["display_name"] for row in result["models"]] == [
        "GPT-5.6 Terra",
        "provider/deleted-model",
    ]
    assert result["models"][0]["model_name"] == "provider/model-id"
