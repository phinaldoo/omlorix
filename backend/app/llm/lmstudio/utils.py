"""Backward-compatible public API for the LM Studio integration.

Focused sibling modules own client/network behavior and model lifecycle
operations. Imports remain here as intentional compatibility seams.
"""

from __future__ import annotations

# ruff: noqa: F401, E402

from datetime import datetime, timezone
from typing import Any

import json
import logging
import time
from urllib.parse import urlparse

import requests
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder

from app.llm.models import LLMProvider, Models, create_llm_provider
from app.network.policy import OutboundRequestBlockedError, assert_url_allowed


logger = logging.getLogger(__name__)

LMSTUDIO_REQUEST_TIMEOUT = 15
LMSTUDIO_MODEL_ACTION_TIMEOUT = 60
LMSTUDIO_MODEL_LOAD_TIMEOUT = 600
LMSTUDIO_DOWNLOAD_POLL_INTERVAL_SECONDS = 1.0
LMSTUDIO_RESPONSES_REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh"}
)
LMSTUDIO_REASONING_EFFORT_ALIASES = {
    # Native model metadata and the native chat API expose on/off, while the
    # OpenAI-compatible Responses endpoint requires an OpenAI effort enum.
    "on": "medium",
    "off": "none",
}
# Downloads can legitimately take much longer than five minutes. Keep an upper
# bound so an abandoned server-side job cannot hold an application worker
# forever, while allowing large models to finish on ordinary connections.
LMSTUDIO_DOWNLOAD_POLL_TIMEOUT_SECONDS = 21600.0


def normalize_lmstudio_base_url(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("normalize_lmstudio_base_url", globals())
    return _implementation._impl_normalize_lmstudio_base_url(*args, **kwargs)


def get_lmstudio_openai_base_url(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("get_lmstudio_openai_base_url", globals())
    return _implementation._impl_get_lmstudio_openai_base_url(*args, **kwargs)


def normalize_lmstudio_responses_reasoning_effort(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies(
        "normalize_lmstudio_responses_reasoning_effort", globals()
    )
    return _implementation._impl_normalize_lmstudio_responses_reasoning_effort(
        *args, **kwargs
    )


def _lmstudio_auth_headers(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_lmstudio_auth_headers", globals())
    return _implementation._impl__lmstudio_auth_headers(*args, **kwargs)


def _resolve_lmstudio_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_resolve_lmstudio_provider", globals())
    return _implementation._impl__resolve_lmstudio_provider(*args, **kwargs)


def _extract_lmstudio_base_url(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_extract_lmstudio_base_url", globals())
    return _implementation._impl__extract_lmstudio_base_url(*args, **kwargs)


def _get_lmstudio_credentials(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_get_lmstudio_credentials", globals())
    return _implementation._impl__get_lmstudio_credentials(*args, **kwargs)


def _lmstudio_request(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_lmstudio_request", globals())
    return _implementation._impl__lmstudio_request(*args, **kwargs)


def _lmstudio_json_object(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_lmstudio_json_object", globals())
    return _implementation._impl__lmstudio_json_object(*args, **kwargs)


def _assert_lmstudio_url_allowed(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_assert_lmstudio_url_allowed", globals())
    return _implementation._impl__assert_lmstudio_url_allowed(*args, **kwargs)


def _lmstudio_error_message(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_lmstudio_error_message", globals())
    return _implementation._impl__lmstudio_error_message(*args, **kwargs)


def _coerce_lmstudio_model_entry(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_coerce_lmstudio_model_entry", globals())
    return _implementation._impl__coerce_lmstudio_model_entry(*args, **kwargs)


def _lmstudio_reasoning_options(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies("_lmstudio_reasoning_options", globals())
    return _implementation._impl__lmstudio_reasoning_options(*args, **kwargs)


def lmstudio_capabilities_to_list(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import client as _implementation

    _implementation._sync_compat_dependencies(
        "lmstudio_capabilities_to_list", globals()
    )
    return _implementation._impl_lmstudio_capabilities_to_list(*args, **kwargs)


def create_lmstudio_provider(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("create_lmstudio_provider", globals())
    return _implementation._impl_create_lmstudio_provider(*args, **kwargs)


def list_models_all(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_all", globals())
    return _implementation._impl_list_models_all(*args, **kwargs)


def list_models_lmstudio(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_lmstudio", globals())
    return _implementation._impl_list_models_lmstudio(*args, **kwargs)


def get_model_info(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("get_model_info", globals())
    return _implementation._impl_get_model_info(*args, **kwargs)


def list_models_loaded(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("list_models_loaded", globals())
    return _implementation._impl_list_models_loaded(*args, **kwargs)


def _plain_json_value(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("_plain_json_value", globals())
    return _implementation._impl__plain_json_value(*args, **kwargs)


def lmstudio_create_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("lmstudio_create_model", globals())
    return _implementation._impl_lmstudio_create_model(*args, **kwargs)


def _build_load_payload(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("_build_load_payload", globals())
    return _implementation._impl__build_load_payload(*args, **kwargs)


def load_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("load_model", globals())
    return _implementation._impl_load_model(*args, **kwargs)


def unload_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("unload_model", globals())
    return _implementation._impl_unload_model(*args, **kwargs)


def _normalize_download_progress(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("_normalize_download_progress", globals())
    return _implementation._impl__normalize_download_progress(*args, **kwargs)


def download_model(*args, **kwargs):
    """Delegate to the focused implementation while preserving patch seams."""
    from . import models as _implementation

    _implementation._sync_compat_dependencies("download_model", globals())
    return _implementation._impl_download_model(*args, **kwargs)
