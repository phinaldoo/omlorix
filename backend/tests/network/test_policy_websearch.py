from app.network.policy import DEFAULT_WEBSEARCH_PROVIDER_TARGETS, get_websearch_provider_target


def test_google_pse_is_no_longer_a_websearch_provider_target():
    assert "google_pse" not in DEFAULT_WEBSEARCH_PROVIDER_TARGETS
    assert get_websearch_provider_target("google_pse", {}) is None
