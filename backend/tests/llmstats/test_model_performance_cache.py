import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.models import Models
from app.llmstats import models as llmstats_models
from app.llmstats.models import (
    LLMGenerationStatistic,
    calculate_model_tokens_per_second_summary,
    get_model_cached_tokens_per_second,
    get_model_cached_tokens_per_second_sample_count,
    refresh_model_tokens_per_second_cache,
)


def _stat(*, success=True, tokens_per_second=10, generation_time=3, output_tokens=50):
    return SimpleNamespace(
        status={"success": success},
        meta={
            "tokens_per_second": tokens_per_second,
            "generation_time": generation_time,
            "output_tokens": output_tokens,
        },
    )


def test_calculate_model_tokens_per_second_summary_averages_recent_valid_samples():
    summary = calculate_model_tokens_per_second_summary(
        [
            _stat(tokens_per_second=10, generation_time=3, output_tokens=25),
            _stat(tokens_per_second=20, generation_time=1, output_tokens=150),
            _stat(tokens_per_second=999, generation_time=0.5, output_tokens=999),
            _stat(success=False, tokens_per_second=999),
            _stat(tokens_per_second=30, generation_time=3, output_tokens=25),
        ],
        sample_limit=2,
    )

    assert summary == {
        "tokens_per_second": 15.0,
        "sample_count": 2,
    }


def test_calculate_model_tokens_per_second_summary_rejects_zero_sample_limit():
    with pytest.raises(ValueError):
        calculate_model_tokens_per_second_summary([], sample_limit=0)


def test_model_tokens_per_second_cache_helpers_read_performance_meta():
    meta = {
        "performance": {
            "tokens_per_second": "42.75",
            "sample_count": "12",
        }
    }

    assert get_model_cached_tokens_per_second(meta) == 42.75
    assert get_model_cached_tokens_per_second_sample_count(meta) == 12


def test_model_stat_filter_matches_provider_group_metadata():
    model = SimpleNamespace(
        id="model-1",
        model_name="provider-model-1",
        name="other-provider-model",
        provider="openai",
        provider_id="group-1",
    )

    class GroupQueryStub:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return object()

    class DbStub:
        def query(self, *_args, **_kwargs):
            return GroupQueryStub()

    expression = llmstats_models._model_stat_identifier_filter(DbStub(), model)
    compiled = str(
        expression.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "llm_generation_statistics.provider_id = 'group-1'" in compiled
    assert "other-provider-model" not in compiled
    assert "requested_provider_id" in compiled
    assert "provider_group_id" in compiled


def test_refresh_model_tokens_per_second_cache_updates_model_meta(monkeypatch):
    model = SimpleNamespace(
        id="model-1",
        model_name="provider-model-1",
        name="Model One",
        provider="openai",
        provider_id="provider-1",
        meta={},
    )
    stats = [
        _stat(tokens_per_second=40, generation_time=3, output_tokens=80),
        _stat(tokens_per_second=60, generation_time=3, output_tokens=80),
    ]

    class QueryStub:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def limit(self, *_args, **_kwargs):
            return self

        def all(self):
            return self.rows

    class DbStub:
        def __init__(self):
            self.commits = 0

        def query(self, model_class):
            if model_class is Models:
                return QueryStub([model])
            if model_class is LLMGenerationStatistic:
                return QueryStub(stats)
            raise AssertionError(f"Unexpected query for {model_class}")

        def commit(self):
            self.commits += 1

    modified = []
    monkeypatch.setattr(llmstats_models, "flag_modified", lambda obj, attr: modified.append((obj, attr)))

    updated_count = refresh_model_tokens_per_second_cache(DbStub(), sample_limit=25)

    assert updated_count == 1
    assert model.meta["performance"]["tokens_per_second"] == 50.0
    assert model.meta["performance"]["sample_count"] == 2
    assert model.meta["performance"]["sample_limit"] == 25
    assert modified == [(model, "meta")]
