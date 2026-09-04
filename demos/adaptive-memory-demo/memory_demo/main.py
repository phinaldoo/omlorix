from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APITimeoutError, RateLimitError

from .config import PROJECT_ROOT, Settings
from .database import Database, iso, utc_now
from .llm import GatewayError, GatewayResult, LLMGateway, Usage
from .memory import MemoryService
from .schemas import (
    ActionResult,
    AppState,
    ChatRequest,
    ChatResult,
    EditMemoryRequest,
    HealthResponse,
    MetricsView,
    ProfileView,
    RuntimeView,
    SweepRequest,
)

LOGGER = logging.getLogger("adaptive_memory_demo")
SUPPORTED_LOCALES = {"ar", "de", "en", "es", "fr", "hi", "it", "ja", "pt", "ru", "zh"}
STATIC_ROOT = PROJECT_ROOT / "static"


def _app_now(app: FastAPI) -> datetime:
    return utc_now() + timedelta(days=app.state.clock_offset_days)


def _record_usage(
    database: Database,
    usage: Usage,
    *,
    operation: str,
    source_message_id: str | None,
) -> None:
    database.record_usage(
        source_message_id=source_message_id,
        operation=operation,
        model=usage.model,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        estimated_cost_usd=usage.estimated_cost_usd,
    )


def _memory_error_code(error: Exception) -> str:
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return "connection"
    if isinstance(error, RateLimitError):
        return "rate_limit"
    return "provider"


def _build_state(app: FastAPI, conversation_id: str) -> AppState:
    database: Database = app.state.database
    settings: Settings = app.state.settings
    memory_service: MemoryService = app.state.memory_service
    profile = database.latest_profile()
    return AppState(
        conversation_id=conversation_id,
        runtime=RuntimeView(
            mode=settings.runtime_mode,
            chat_model=(
                "local-simulator"
                if settings.runtime_mode == "simulation"
                else settings.openai_chat_model
            ),
            memory_model=(
                "local-simulator"
                if settings.runtime_mode == "simulation"
                else settings.openai_memory_model
            ),
            clock_offset_days=app.state.clock_offset_days,
        ),
        messages=database.list_messages(conversation_id),
        profile=ProfileView(
            content=str(profile["content"]) if profile else "",
            version=int(profile["version"]) if profile else 0,
            updated_at=profile["created_at"] if profile else None,
        ),
        memories=memory_service.memory_views(_app_now(app)),
        events=database.list_events(),
        metrics=MetricsView(**memory_service.metrics(_app_now(app))),
    )


def create_app(
    *,
    settings: Settings | None = None,
    database: Database | None = None,
    gateway: LLMGateway | None = None,
) -> FastAPI:
    resolved_settings = settings or Settings()
    resolved_database = database or Database(resolved_settings.database_path)
    owns_gateway = gateway is None
    resolved_gateway = gateway or LLMGateway(resolved_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        resolved_database.initialize()
        resolved_database.ensure_conversation()
        application.state.clock_offset_days = resolved_database.clock_offset_days()
        try:
            yield
        finally:
            if owns_gateway:
                await resolved_gateway.aclose()

    application = FastAPI(
        title="Omlorix Adaptive Memory Demo",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.state.database = resolved_database
    application.state.gateway = resolved_gateway
    application.state.memory_service = MemoryService(resolved_database, resolved_settings)
    application.state.user_lock = asyncio.Lock()
    application.state.clock_offset_days = 0

    @application.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        resolved_database.initialize()
        return HealthResponse(status="ok", mode=resolved_settings.runtime_mode, database="ok")

    @application.get("/api/state", response_model=AppState)
    async def get_state() -> AppState:
        async with application.state.user_lock:
            conversation_id = resolved_database.ensure_conversation()
            return _build_state(application, conversation_id)

    @application.post("/api/chat", response_model=ChatResult)
    async def chat(payload: ChatRequest) -> ChatResult:
        if resolved_settings.runtime_mode == "unconfigured":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "openai_api_key_required"},
            )
        locale = payload.locale if payload.locale in SUPPORTED_LOCALES else "en"
        if payload.conversation_id and not resolved_database.conversation_exists(
            payload.conversation_id
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "conversation_not_found"},
            )

        async with application.state.user_lock:
            current = _app_now(application)
            memory_service: MemoryService = application.state.memory_service
            llm_gateway: LLMGateway = application.state.gateway
            memory_service.sweep(now=current)

            conversation_id = resolved_database.ensure_conversation(payload.conversation_id)
            source_message_id = resolved_database.append_message(
                conversation_id, "user", payload.message, created_at=current
            )
            memories = resolved_database.list_memories()
            history = resolved_database.list_messages(conversation_id, limit=12)
            profile = resolved_database.latest_profile()

            memory_task = asyncio.create_task(
                llm_gateway.consolidate(
                    message=payload.message,
                    memories=memories,
                    now_iso=iso(current),
                    locale=locale,
                )
            )

            forget_request = memory_service.is_forget_request(payload.message)
            memory_profile_version = (
                int(profile["version"]) if profile is not None and not forget_request else None
            )
            chat_task = asyncio.create_task(
                llm_gateway.chat(
                    history=history,
                    memory_context=memory_service.prompt_context(
                        None if forget_request else profile
                    ),
                    locale=locale,
                )
            )

            memory_result: GatewayResult[Any] | Exception
            chat_result: GatewayResult[str] | Exception
            memory_result, chat_result = await asyncio.gather(
                memory_task, chat_task, return_exceptions=True
            )

            memory_status = "failed"
            memory_error = None
            if isinstance(memory_result, GatewayResult):
                _record_usage(
                    resolved_database,
                    memory_result.usage,
                    operation="memory_consolidation",
                    source_message_id=source_message_id,
                )
                applied = memory_service.apply_consolidation(
                    memory_result.value,
                    source_message_id=source_message_id,
                    source_message=payload.message,
                    now=current,
                )
                memory_status = applied.status
            elif isinstance(memory_result, Exception):
                memory_error = _memory_error_code(memory_result)
                if isinstance(memory_result, GatewayError):
                    _record_usage(
                        resolved_database,
                        memory_result.usage,
                        operation="memory_consolidation_incomplete",
                        source_message_id=source_message_id,
                    )
                LOGGER.warning(
                    "Memory consolidation failed after provider retries: %s: %s",
                    type(memory_result).__name__,
                    memory_result,
                )

            if isinstance(chat_result, Exception):
                if isinstance(chat_result, GatewayError):
                    _record_usage(
                        resolved_database,
                        chat_result.usage,
                        operation="chat_incomplete",
                        source_message_id=source_message_id,
                    )
                LOGGER.warning("Chat generation failed: %s", type(chat_result).__name__)
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={"code": "chat_provider_error", "memory_status": memory_status},
                ) from None

            _record_usage(
                resolved_database,
                chat_result.usage,
                operation="chat",
                source_message_id=source_message_id,
            )
            resolved_database.append_message(
                conversation_id,
                "assistant",
                chat_result.value,
                created_at=current + timedelta(microseconds=1),
            )
            return ChatResult(
                state=_build_state(application, conversation_id),
                memory_status=memory_status,
                memory_error=memory_error,
                memory_profile_version=memory_profile_version,
            )

    @application.post("/api/conversations", response_model=ActionResult)
    async def create_conversation() -> ActionResult:
        async with application.state.user_lock:
            conversation_id = resolved_database.create_conversation()
            return ActionResult(state=_build_state(application, conversation_id), status="created")

    @application.patch("/api/memories/{memory_id}", response_model=ActionResult)
    async def edit_memory(memory_id: str, payload: EditMemoryRequest) -> ActionResult:
        async with application.state.user_lock:
            memory_service: MemoryService = application.state.memory_service
            if not memory_service.edit_memory(
                memory_id,
                payload.content,
                now=_app_now(application),
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "memory_not_found_or_rejected"},
                )
            conversation_id = resolved_database.ensure_conversation()
            return ActionResult(state=_build_state(application, conversation_id), status="saved")

    @application.post("/api/memories/{memory_id}/confirm", response_model=ActionResult)
    async def confirm_memory(memory_id: str) -> ActionResult:
        async with application.state.user_lock:
            if not application.state.memory_service.confirm_memory(
                memory_id, now=_app_now(application)
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "memory_not_found"},
                )
            conversation_id = resolved_database.ensure_conversation()
            return ActionResult(
                state=_build_state(application, conversation_id), status="confirmed"
            )

    @application.delete("/api/memories/{memory_id}", response_model=ActionResult)
    async def forget_memory(memory_id: str) -> ActionResult:
        async with application.state.user_lock:
            if not application.state.memory_service.forget_memory(
                memory_id, now=_app_now(application)
            ):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "memory_not_found"},
                )
            conversation_id = resolved_database.ensure_conversation()
            return ActionResult(
                state=_build_state(application, conversation_id), status="forgotten"
            )

    @application.post("/api/lifecycle/sweep", response_model=ActionResult)
    async def run_sweep(payload: SweepRequest) -> ActionResult:
        async with application.state.user_lock:
            application.state.clock_offset_days += payload.advance_days
            resolved_database.set_clock_offset_days(application.state.clock_offset_days)
            sweep_time = _app_now(application)
            expired_count = application.state.memory_service.sweep(now=sweep_time)
            conversation_id = resolved_database.ensure_conversation()
            return ActionResult(
                state=_build_state(application, conversation_id),
                status=f"expired:{expired_count}",
            )

    @application.post("/api/reset", response_model=ActionResult)
    async def reset_demo() -> ActionResult:
        async with application.state.user_lock:
            conversation_id = resolved_database.reset_demo()
            application.state.clock_offset_days = 0
            return ActionResult(state=_build_state(application, conversation_id), status="reset")

    @application.get("/api/export")
    async def export_data() -> JSONResponse:
        async with application.state.user_lock:
            conversation_id = resolved_database.ensure_conversation()
            return JSONResponse(
                content=resolved_database.export_bundle(conversation_id),
                headers={
                    "Content-Disposition": 'attachment; filename="adaptive-memory-export.json"'
                },
            )

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_ROOT / "index.html")

    application.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
    return application


app = create_app()
