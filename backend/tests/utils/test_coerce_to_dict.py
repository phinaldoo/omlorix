import logging

from app.utils.utils import coerce_to_dict


class PydanticLikeValue:
    def model_dump(self, *, exclude_none: bool):
        assert exclude_none is False
        return {"source": "model_dump", "optional": None}


class LegacyModelValue:
    def dict(self, *, exclude_none: bool):
        assert exclude_none is False
        return {"source": "dict", "optional": None}


def test_coerce_to_dict_supports_normal_settings_representations_without_error_logs(caplog):
    caplog.set_level(logging.ERROR, logger="app.utils.utils")

    assert coerce_to_dict({"source": "mapping"}) == {"source": "mapping"}
    assert coerce_to_dict(PydanticLikeValue()) == {"source": "model_dump", "optional": None}
    assert coerce_to_dict(LegacyModelValue()) == {"source": "dict", "optional": None}
    assert coerce_to_dict('{"source": "json"}') == {"source": "json"}
    assert coerce_to_dict([("source", "iterable")]) == {"source": "iterable"}
    assert not caplog.records


def test_coerce_to_dict_returns_empty_mapping_for_unsupported_or_invalid_values():
    assert coerce_to_dict(None) == {}
    assert coerce_to_dict(42) == {}
    assert coerce_to_dict("") == {}
    assert coerce_to_dict("not-json") == {}
    assert coerce_to_dict("[]") == {}
