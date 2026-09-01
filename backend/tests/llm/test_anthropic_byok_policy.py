import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.llm.anthropic.models import list_anthropic_models
from app.llm.schemas import ProviderEnum
from app.network.policy import OutboundAccessMode, OutboundRequestBlockedError


def _blocked_policy_error(target: str) -> OutboundRequestBlockedError:
    return OutboundRequestBlockedError(
        target=target,
        feature="LLM provider model listing",
        policy_mode=OutboundAccessMode.allowlist_only,
        reason="the destination is not in the configured allowlist",
    )


class AnthropicByokPolicyTests:
    def test_byok_base_url_is_policy_checked_before_client_request(self):
        db = MagicMock()
        base_url = "http://127.0.0.1:35421"

        with patch(
            "app.llm.anthropic.models.assert_llm_config_allowed",
            side_effect=_blocked_policy_error(base_url),
        ) as mock_assert_allowed, patch(
            "app.llm.anthropic.models.get_anthropic_client"
        ) as mock_get_client:
            with pytest.raises(HTTPException) as exc_info:
                list_anthropic_models(db, api_key="dummy-user-key", base_url=base_url)

        assert exc_info.value.status_code == 403
        assert "LLM provider model listing blocked" in exc_info.value.detail
        mock_assert_allowed.assert_called_once_with(
            db,
            provider_type=ProviderEnum.anthropic_base.value,
            settings={"base_url": base_url},
            feature="LLM provider model listing",
        )
        mock_get_client.assert_not_called()

    def test_stored_provider_base_url_is_policy_checked_before_client_request(self):
        db = MagicMock()
        provider = SimpleNamespace(
            id="anthropic-provider-1",
            provider=ProviderEnum.anthropic_base.value,
            settings={},
        )

        with patch(
            "app.llm.anthropic.models.get_llm_provider", return_value=provider
        ) as mock_get_provider, patch(
            "app.llm.anthropic.models.assert_llm_provider_allowed",
            side_effect=_blocked_policy_error("http://127.0.0.1:35421"),
        ) as mock_assert_allowed, patch(
            "app.llm.anthropic.models.get_anthropic_client"
        ) as mock_get_client:
            with pytest.raises(HTTPException) as exc_info:
                list_anthropic_models(db, anthropic_provider_id=provider.id)

        assert exc_info.value.status_code == 403
        mock_get_provider.assert_called_once_with(db, provider.id)
        mock_assert_allowed.assert_called_once_with(
            db, provider, feature="LLM provider model listing"
        )
        mock_get_client.assert_not_called()
