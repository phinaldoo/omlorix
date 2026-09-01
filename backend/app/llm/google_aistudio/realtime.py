from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException
from google import genai
from google.genai import types
from sqlalchemy.orm import Session

from app.llm.google_aistudio.model_list import GOOGLE_LIVE_MODELS
from app.llm.google_aistudio.text_to_speech import (
    GOOGLE_AISTUDIO_TTS_DEFAULT_VOICE,
    GOOGLE_AISTUDIO_TTS_VOICES,
)
from app.llm.google_aistudio.utils import _build_aistudio_tools_payload, list_models_google_aistudio
from app.llm.models import get_llm_provider


GOOGLE_AISTUDIO_LIVE_DEFAULT_MODEL = "gemini-3.1-flash-live-preview"
GOOGLE_AISTUDIO_LIVE_DEFAULT_API_VERSION = "v1alpha"
GOOGLE_AISTUDIO_LIVE_WS_BASE_URL = "wss://generativelanguage.googleapis.com"
# These endpoints use a specialized protocol that is incompatible with
# Omlorix's general-purpose voice assistant. In particular, Live Translate does
# not accept the assistant instructions and tools configured by this feature.
GOOGLE_AISTUDIO_SPECIALIZED_LIVE_MODELS = frozenset(
    {
        "gemini-3.5-live-translate-preview",
    }
)


def _supports_google_native_audio_dialog_features(model_name: str) -> bool:
    """Return whether a model accepts affective and proactive audio settings."""
    normalized_model = str(model_name or "").strip().lower().removeprefix("models/")
    return normalized_model.startswith("gemini-2.5-flash-native-audio")


def _normalize_supported_actions(raw_actions: Any) -> set[str]:
    values: set[str] = set()
    if not isinstance(raw_actions, (list, tuple, set)):
        return values
    for item in raw_actions:
        if isinstance(item, str):
            values.add(item.strip().lower())
        else:
            maybe_value = getattr(item, "value", None)
            if isinstance(maybe_value, str):
                values.add(maybe_value.strip().lower())
            else:
                values.add(str(item).strip().lower())
    return values


def get_google_aistudio_live_models(
    *,
    db: Session,
    google_provider_id: str,
) -> list[str]:
    discovered: list[str] = []
    try:
        models = list_models_google_aistudio(db, aistudio_provider_id=google_provider_id)
    except Exception:
        models = []

    for model in models or []:
        model_id = str(model.get("id") or "").strip()
        if not model_id or model_id in discovered:
            continue
        normalized_model_id = model_id.removeprefix("models/")
        if normalized_model_id in GOOGLE_AISTUDIO_SPECIALIZED_LIVE_MODELS:
            continue

        supported_actions = _normalize_supported_actions(model.get("supported_actions"))
        if model_id in GOOGLE_LIVE_MODELS or any(
            "bidigeneratecontent" in action or "live" in action
            for action in supported_actions
        ):
            discovered.append(model_id)

    for fallback_model in GOOGLE_LIVE_MODELS:
        if fallback_model not in discovered:
            discovered.append(fallback_model)

    if GOOGLE_AISTUDIO_LIVE_DEFAULT_MODEL not in discovered:
        discovered.insert(0, GOOGLE_AISTUDIO_LIVE_DEFAULT_MODEL)

    return discovered


def build_google_aistudio_live_client_setup(
    *,
    model_name: str,
) -> dict[str, str]:
    """Build the minimal raw-WebSocket setup allowed by a constrained token.

    The backend binds the complete LiveConnectConfig into the single-use
    ephemeral token. Sending those same settings from the browser would both
    use the wrong raw-WebSocket JSON shape and disclose privileged agent or
    skill instructions. A constrained connection only needs the matching model
    in its first setup message.
    """
    normalized_model = str(model_name or "").strip().removeprefix("models/")
    if not normalized_model:
        raise HTTPException(status_code=400, detail="Realtime model is missing")
    return {"model": f"models/{normalized_model}"}


def get_google_aistudio_live_default_voice(raw_voice: str | None = None) -> str:
    """Return a supported Gemini Live voice, falling back for legacy values."""
    normalized_voice = str(raw_voice or "").strip()
    normalized_casefold = normalized_voice.casefold()
    return next(
        (
            voice
            for voice in GOOGLE_AISTUDIO_TTS_VOICES
            if voice.casefold() == normalized_casefold
        ),
        GOOGLE_AISTUDIO_TTS_DEFAULT_VOICE,
    )


def get_realtime_settings_schema(
    *,
    model_name: str,
    tool_options: list[dict] | None = None,
):
    """Return the controls implemented by Gemini Live for one model."""

    from app.llm.realtime_schema import (
        input_transcription_field,
        language_code_field,
        max_output_tokens_field,
        output_transcription_field,
        prefix_padding_field,
        silence_duration_field,
        tools_field,
        voice_field,
    )
    from app.utils.schemas import FieldAttributes, FieldSchema, Option, Section, Sections

    fields = [
        voice_field(
            [
                Option(value=voice, label=voice)
                for voice in GOOGLE_AISTUDIO_TTS_VOICES
            ],
            description=(
                "Voice used for assistant speech output in realtime sessions. "
                "OpenAI examples use values like alloy; Google Live supports "
                "prebuilt voices such as Kore or Puck."
            ),
        ),
        tools_field(tool_options or []),
        FieldSchema(
            key="realtime_temperature",
            label="Realtime temperature",
            description=(
                "Controls response variability for providers that expose "
                "temperature in realtime sessions."
            ),
            type="number",
            input_type="float",
            placeholder="Leave empty for provider default",
            dependency="realtime_enabled",
            dependency_value=True,
            attributes=FieldAttributes(min=0, max=2, step=0.1),
        ),
        max_output_tokens_field(),
        input_transcription_field(),
        output_transcription_field(),
        language_code_field(
            description=(
                "Optional language hint for realtime speech synthesis and "
                "transcription, especially useful for Google Live. Example: "
                "en-US or de-DE."
            )
        ),
        FieldSchema(
            key="realtime_enable_session_resumption",
            label="Session resumption",
            description=(
                "Allow supported providers to reconnect long-running realtime "
                "calls without losing context."
            ),
            type="boolean",
            dependency="realtime_enabled",
            dependency_value=True,
        ),
        FieldSchema(
            key="realtime_enable_context_window_compression",
            label="Context compression",
            description=(
                "Enable supported providers to compress long live sessions "
                "instead of ending when the context fills up."
            ),
            type="boolean",
            dependency="realtime_enabled",
            dependency_value=True,
        ),
        FieldSchema(
            key="realtime_compression_trigger_tokens",
            label="Compression trigger tokens",
            description=(
                "Optional token threshold that triggers realtime context "
                "compression on supported providers."
            ),
            type="number",
            input_type="int",
            placeholder="Provider default",
            dependency="realtime_enable_context_window_compression",
            dependency_value=True,
            attributes=FieldAttributes(min=1, step=1),
        ),
        FieldSchema(
            key="realtime_compression_target_tokens",
            label="Compression target tokens",
            description=(
                "Optional target token budget for the compressed realtime "
                "context window."
            ),
            type="number",
            input_type="int",
            placeholder="Provider default",
            dependency="realtime_enable_context_window_compression",
            dependency_value=True,
            attributes=FieldAttributes(min=1, step=1),
        ),
        FieldSchema(
            key="realtime_activity_handling",
            label="Activity handling",
            description=(
                "Google Live only. Choose whether new user speech interrupts "
                "the assistant or is queued without interruption."
            ),
            type="select",
            dependency="realtime_enabled",
            dependency_value=True,
            options=[
                Option(
                    value="START_OF_ACTIVITY_INTERRUPTS",
                    label="Interrupt on new speech",
                    i18n_label=(
                        "admin.shared.model_name.option.START_OF_ACTIVITY_INTERRUPTS"
                    ),
                ),
                Option(
                    value="NO_INTERRUPTION",
                    label="Do not interrupt",
                    i18n_label="admin.shared.model_name.option.NO_INTERRUPTION",
                ),
            ],
        ),
        FieldSchema(
            key="realtime_turn_coverage",
            label="Turn coverage",
            description=(
                "Google Live only. Control whether a turn includes only detected "
                "activity or all input accumulated so far."
            ),
            type="select",
            dependency="realtime_enabled",
            dependency_value=True,
            options=[
                Option(
                    value="TURN_INCLUDES_ONLY_ACTIVITY",
                    label="Only detected activity",
                    i18n_label=(
                        "admin.shared.model_name.option.TURN_INCLUDES_ONLY_ACTIVITY"
                    ),
                ),
                Option(
                    value="TURN_INCLUDES_ALL_INPUT",
                    label="All buffered input",
                    i18n_label=(
                        "admin.shared.model_name.option.TURN_INCLUDES_ALL_INPUT"
                    ),
                ),
            ],
        ),
        FieldSchema(
            key="realtime_start_sensitivity",
            label="Start sensitivity",
            description=(
                "Google Live only. Tweak how aggressively the server detects "
                "the beginning of user speech."
            ),
            type="select",
            dependency="realtime_enabled",
            dependency_value=True,
            options=[
                Option(
                    value="",
                    label="Provider default",
                    i18n_label="admin.shared.model_name.option.provider_default",
                ),
                Option(
                    value="START_SENSITIVITY_HIGH",
                    label="High",
                    i18n_label=(
                        "admin.shared.model_name.option.START_SENSITIVITY_HIGH"
                    ),
                ),
                Option(
                    value="START_SENSITIVITY_LOW",
                    label="Low",
                    i18n_label=(
                        "admin.shared.model_name.option.START_SENSITIVITY_LOW"
                    ),
                ),
            ],
        ),
        FieldSchema(
            key="realtime_end_sensitivity",
            label="End sensitivity",
            description=(
                "Google Live only. Tweak how aggressively the server decides user "
                "speech has ended."
            ),
            type="select",
            dependency="realtime_enabled",
            dependency_value=True,
            options=[
                Option(
                    value="",
                    label="Provider default",
                    i18n_label="admin.shared.model_name.option.provider_default",
                ),
                Option(
                    value="END_SENSITIVITY_HIGH",
                    label="High",
                    i18n_label="admin.shared.model_name.option.END_SENSITIVITY_HIGH",
                ),
                Option(
                    value="END_SENSITIVITY_LOW",
                    label="Low",
                    i18n_label="admin.shared.model_name.option.END_SENSITIVITY_LOW",
                ),
            ],
        ),
        prefix_padding_field(
            description=(
                "Google Live only. Buffer a small amount of speech before the "
                "detected utterance start."
            )
        ),
        silence_duration_field(
            description=(
                "Google Live only. Wait this long before finalizing the end of a "
                "spoken turn."
            )
        ),
    ]

    # Native affective/proactive audio is a model capability, not a general
    # Gemini Live switch.  Add these controls only for models that accept them.
    if _supports_google_native_audio_dialog_features(model_name):
        fields.extend(
            [
                FieldSchema(
                    key="realtime_enable_affective_dialog",
                    label="Affective dialog",
                    description=(
                        "Google Live only. Lets the model adapt its speaking style "
                        "to the caller’s tone and emotion."
                    ),
                    type="boolean",
                    dependency="realtime_enabled",
                    dependency_value=True,
                ),
                FieldSchema(
                    key="realtime_enable_proactive_audio",
                    label="Proactive audio",
                    description=(
                        "Google Live only. Allows the model to remain silent when "
                        "the input is not actionable."
                    ),
                    type="boolean",
                    dependency="realtime_enabled",
                    dependency_value=True,
                ),
            ]
        )

    return Sections(
        sections=[
            Section(
                title="Realtime advanced settings",
                description="Additional controls for provider-specific configuration.",
                fields=fields,
            )
        ]
    )


def build_google_aistudio_live_connect_config(
    *,
    instructions: str,
    model_name: str,
    voice: str | None,
    settings: dict[str, Any] | None,
    tool_schemas: list[dict[str, Any]] | None = None,
    session_handle: str | None = None,
    native_google_search_enabled: bool = False,
) -> types.LiveConnectConfig:
    realtime_settings = settings if isinstance(settings, dict) else {}

    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=get_google_aistudio_live_default_voice(voice),
            )
        )
    )
    language_code = str(realtime_settings.get("language_code") or "").strip()
    if language_code:
        speech_config.language_code = language_code

    input_transcription = (
        types.AudioTranscriptionConfig()
        if bool(realtime_settings.get("input_transcription_enabled", True))
        else None
    )
    output_transcription = (
        types.AudioTranscriptionConfig()
        if bool(realtime_settings.get("output_transcription_enabled", True))
        else None
    )

    session_resumption = None
    if bool(realtime_settings.get("enable_session_resumption", True)):
        # Session resumption must be requested on the initial connection too.
        # Google only emits SessionResumptionUpdate handles when this config is
        # present, so waiting until a handle already exists makes reconnecting
        # impossible for every newly-created Live session.
        session_resumption = types.SessionResumptionConfig(
            handle=str(session_handle or "").strip() or None,
        )

    context_window_compression = None
    if bool(realtime_settings.get("enable_context_window_compression", True)):
        sliding_window = types.SlidingWindow()
        target_tokens = realtime_settings.get("compression_target_tokens")
        if isinstance(target_tokens, int) and target_tokens > 0:
            sliding_window.target_tokens = target_tokens
        context_window_compression = types.ContextWindowCompressionConfig(
            sliding_window=sliding_window,
        )
        trigger_tokens = realtime_settings.get("compression_trigger_tokens")
        if isinstance(trigger_tokens, int) and trigger_tokens > 0:
            context_window_compression.trigger_tokens = trigger_tokens

    automatic_activity_detection = types.AutomaticActivityDetection()
    start_sensitivity = str(realtime_settings.get("start_sensitivity") or "").strip().upper()
    end_sensitivity = str(realtime_settings.get("end_sensitivity") or "").strip().upper()
    prefix_padding_ms = realtime_settings.get("prefix_padding_ms")
    silence_duration_ms = realtime_settings.get("silence_duration_ms")

    if start_sensitivity in {"START_SENSITIVITY_HIGH", "START_SENSITIVITY_LOW"}:
        automatic_activity_detection.start_of_speech_sensitivity = types.StartSensitivity(start_sensitivity)
    if end_sensitivity in {"END_SENSITIVITY_HIGH", "END_SENSITIVITY_LOW"}:
        automatic_activity_detection.end_of_speech_sensitivity = types.EndSensitivity(end_sensitivity)
    if isinstance(prefix_padding_ms, int) and prefix_padding_ms >= 0:
        automatic_activity_detection.prefix_padding_ms = prefix_padding_ms
    if isinstance(silence_duration_ms, int) and silence_duration_ms >= 0:
        automatic_activity_detection.silence_duration_ms = silence_duration_ms

    realtime_input_config = types.RealtimeInputConfig(
        automatic_activity_detection=automatic_activity_detection,
    )
    activity_handling = str(realtime_settings.get("activity_handling") or "").strip().upper()
    if activity_handling in {"START_OF_ACTIVITY_INTERRUPTS", "NO_INTERRUPTION"}:
        realtime_input_config.activity_handling = types.ActivityHandling(activity_handling)
    turn_coverage = str(realtime_settings.get("turn_coverage") or "").strip().upper()
    if turn_coverage in {"TURN_INCLUDES_ONLY_ACTIVITY", "TURN_INCLUDES_ALL_INPUT"}:
        realtime_input_config.turn_coverage = types.TurnCoverage(turn_coverage)

    tools = _build_aistudio_tools_payload(
        tool_schemas or [],
        native_websearch_enabled=native_google_search_enabled,
    )

    config = types.LiveConnectConfig(
        response_modalities=[types.Modality.AUDIO],
        system_instruction=instructions,
        speech_config=speech_config,
        input_audio_transcription=input_transcription,
        output_audio_transcription=output_transcription,
        session_resumption=session_resumption,
        context_window_compression=context_window_compression,
        realtime_input_config=realtime_input_config,
        tools=tools or None,
    )

    temperature = realtime_settings.get("temperature")
    if isinstance(temperature, (int, float)):
        config.temperature = float(temperature)

    max_output_tokens = realtime_settings.get("max_output_tokens")
    if isinstance(max_output_tokens, int) and max_output_tokens > 0:
        config.max_output_tokens = max_output_tokens

    supports_native_audio_dialog_features = (
        _supports_google_native_audio_dialog_features(model_name)
    )
    if (
        supports_native_audio_dialog_features
        and bool(realtime_settings.get("enable_affective_dialog", False))
    ):
        config.enable_affective_dialog = True

    if (
        supports_native_audio_dialog_features
        and bool(realtime_settings.get("enable_proactive_audio", False))
    ):
        config.proactivity = types.ProactivityConfig(proactive_audio=True)

    return config


def build_google_aistudio_live_session_config(
    *,
    instructions: str,
    model_name: str,
    voice: str | None,
    settings: dict[str, Any] | None,
    tool_schemas: list[dict[str, Any]] | None = None,
    session_handle: str | None = None,
    native_google_search_enabled: bool = False,
) -> dict[str, Any]:
    """Serialize the provider-side token constraint for tests and diagnostics.

    This is an SDK-shaped config snapshot, not a raw BidiGenerateContent setup
    payload. Browser callers must use
    :func:`build_google_aistudio_live_client_setup` instead.
    """
    config = build_google_aistudio_live_connect_config(
        instructions=instructions,
        model_name=model_name,
        voice=voice,
        settings=settings,
        tool_schemas=tool_schemas,
        session_handle=session_handle,
        native_google_search_enabled=native_google_search_enabled,
    )
    payload = config.model_dump(by_alias=True, exclude_none=True, mode="json")
    payload["model"] = f"models/{model_name}"
    return payload


def mint_google_aistudio_live_ephemeral_token(
    *,
    db: Session,
    provider_id: str,
    model_name: str,
    session_config: types.LiveConnectConfig,
) -> str:
    provider = get_llm_provider(db, provider_id)
    if not provider.api_key:
        raise HTTPException(status_code=400, detail="Realtime provider API key is missing")

    client = genai.Client(
        api_key=provider.api_key,
        http_options=types.HttpOptions(api_version=GOOGLE_AISTUDIO_LIVE_DEFAULT_API_VERSION),
    )
    now = datetime.now(timezone.utc)
    try:
        token = client.auth_tokens.create(
            config=types.CreateAuthTokenConfig(
                uses=1,
                expire_time=now + timedelta(minutes=30),
                new_session_expire_time=now + timedelta(minutes=1),
                # Keep lock_additional_fields unset. In google-genai, None
                # omits the field mask and globally locks the embedded setup;
                # an empty list would lock only fields populated below.
                live_connect_constraints=types.LiveConnectConstraints(
                    model=model_name,
                    config=session_config,
                ),
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to mint Google Live ephemeral token: {exc}") from exc

    token_name = str(getattr(token, "name", "") or "").strip()
    if not token_name:
        raise HTTPException(status_code=502, detail="Google Live provider did not return an ephemeral token")
    return token_name


def build_google_aistudio_live_websocket_url(token_name: str) -> str:
    normalized_token = str(token_name or "").strip()
    if not normalized_token:
        raise HTTPException(status_code=500, detail="Realtime token is missing")
    return (
        f"{GOOGLE_AISTUDIO_LIVE_WS_BASE_URL}/ws/"
        "google.ai.generativelanguage.v1alpha.GenerativeService.BidiGenerateContentConstrained"
        f"?access_token={normalized_token}"
    )
