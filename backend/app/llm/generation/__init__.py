"""Shared contracts and orchestration for lightweight LLM generation tasks.

Provider chat loops remain separate because they coordinate tools, files, and
conversation persistence. This package is intentionally limited to smaller
generation workflows.
"""

from app.llm.generation.contracts import (
    GenerationAdapter,
    GenerationErrorDetails,
    GenerationRequest,
    GenerationResult,
)
from app.llm.generation.service import (
    GenerationRunContext,
    run_generation_once,
)

__all__ = [
    "GenerationAdapter",
    "GenerationErrorDetails",
    "GenerationRequest",
    "GenerationResult",
    "GenerationRunContext",
    "run_generation_once",
]
