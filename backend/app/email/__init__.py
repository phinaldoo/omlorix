"""Durable transactional delivery for Omlorix system email."""

from app.email.models import EmailOutbox

__all__ = ["EmailOutbox"]
