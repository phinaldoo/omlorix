from app.llm.elevenlabs.transcription import _get_transcription_model_ids


def test_model_discovery_does_not_invent_a_fallback_for_an_empty_sdk_annotation():
    """An SDK without discoverable IDs must leave the model selector empty."""

    assert _get_transcription_model_ids(object()) == []
