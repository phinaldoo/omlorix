from app.llm.elevenlabs.transcription import ELEVENLABS_TRANSCRIPTION_MODELS


def test_transcription_models_preserve_supported_file_transcription_choices():
    assert ELEVENLABS_TRANSCRIPTION_MODELS == ["scribe_v1", "scribe_v2"]
