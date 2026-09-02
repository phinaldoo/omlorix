"""Resolve application Host-header policy without importing an ASGI entrypoint."""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException

from app.database import SessionLocal
from app.settings.public_urls import normalize_public_urls
from app.settings.utils import get_value_by_page_and_key
from app.utils.trusted_hosts import load_trusted_hosts


logger = logging.getLogger(__name__)


def load_application_trusted_hosts() -> list[str]:
    """Load trusted Host values from environment and configured public URLs."""

    configured_candidates: list[str] = []
    public_url_settings_loaded = False
    db = None
    try:
        db = SessionLocal()
        configured_candidates.extend(
            normalize_public_urls(
                get_value_by_page_and_key("general", "public_url", db),
                allow_empty=True,
            )
        )
        public_url_settings_loaded = True
    except HTTPException as exc:
        if exc.status_code == 404:
            public_url_settings_loaded = True
        else:
            logger.warning(
                "Unable to load public_url from settings for host validation",
                exc_info=True,
            )
    except Exception:
        logger.warning(
            "Unable to load public_url from settings for host validation",
            exc_info=True,
        )
    finally:
        if db:
            db.close()

    trusted_hosts = load_trusted_hosts(
        public_url_candidates=configured_candidates,
        mode=os.getenv("MODE", "production"),
        allow_any_if_unconfigured=public_url_settings_loaded,
    )
    if trusted_hosts:
        logger.info("Trusted host validation enabled for: %s", ", ".join(trusted_hosts))
    return trusted_hosts
