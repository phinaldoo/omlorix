import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.lmstudio.utils import _lmstudio_request, list_models_all
from app.network.policy import OutboundAccessMode, OutboundRequestBlockedError


class LMStudioNetworkPolicyTests:
    def test_byok_model_listing_checks_outbound_policy_before_request(self):
        db = MagicMock()
        blocked = OutboundRequestBlockedError(
            target="http://127.0.0.1:1234",
            feature="LM Studio model listing",
            policy_mode=OutboundAccessMode.allowlist_only,
            reason="the destination is not in the configured allowlist",
        )

        with patch("app.llm.lmstudio.utils.assert_url_allowed", side_effect=blocked) as mock_assert, patch(
            "app.llm.lmstudio.utils._lmstudio_request"
        ) as mock_request:
            with pytest.raises(HTTPException) as exc_info:
                list_models_all(db, byok_base_url="http://127.0.0.1:1234", byok_api_key="secret")

        assert exc_info.value.status_code == 403
        assert "LM Studio model listing blocked by external requests policy" in exc_info.value.detail
        mock_assert.assert_called_once_with(
            db,
            url="http://127.0.0.1:1234",
            feature="LM Studio model listing",
        )
        mock_request.assert_not_called()

    def test_lmstudio_requests_disable_redirects_by_default(self):
        response = MagicMock()
        response.raise_for_status.return_value = None

        with patch("app.llm.lmstudio.utils.requests.request", return_value=response) as mock_request:
            assert _lmstudio_request("GET", "https://example.test/api/v1/models") is response

        mock_request.assert_called_once_with(
            method="GET",
            url="https://example.test/api/v1/models",
            headers={"Accept": "application/json"},
            json=None,
            timeout=15,
            allow_redirects=False,
        )
