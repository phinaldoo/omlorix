"""Durable, process-isolated background work for Omlorix.

The API only validates and stages work.  Dedicated worker processes claim jobs
from PostgreSQL, which keeps queue ownership correct across restarts and
horizontal replicas without coupling delivery to a particular web process.
"""

from app.workers.models import enqueue_worker_job

__all__ = ["enqueue_worker_job"]
