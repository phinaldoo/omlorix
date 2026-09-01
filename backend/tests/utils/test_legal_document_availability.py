from app.utils import router as utils_router


def test_legal_document_availability_uses_public_page_settings(monkeypatch):
    """The public bootstrap must expose normalized link visibility booleans."""
    values = {
        ("login_general", "show_privacy_notice_link"): "true",
        ("login_general", "show_terms_of_service_link"): 0,
    }
    monkeypatch.setattr(
        utils_router,
        "get_value_by_page_and_key",
        lambda page, key, db: values.get((page, key)),
    )

    result = utils_router.legal_document_availability(object())

    assert result.model_dump() == {
        "privacy": True,
        "terms": False,
    }


def test_legal_document_availability_defaults_to_disabled(monkeypatch):
    """Missing settings hide both optional navigation links."""
    monkeypatch.setattr(
        utils_router,
        "get_value_by_page_and_key",
        lambda page, key, db: None,
    )

    result = utils_router.legal_document_availability(object())

    assert result.model_dump() == {
        "privacy": False,
        "terms": False,
    }
