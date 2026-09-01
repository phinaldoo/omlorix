from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
UTILS = REPO_ROOT / "backend/app/llm/google_aistudio/utils.py"
CHAT = REPO_ROOT / "backend/app/llm/google_aistudio/chat.py"
MESSAGES = REPO_ROOT / "backend/app/llm/google_aistudio/messages.py"
IMAGE_GENERATION = REPO_ROOT / "backend/app/llm/google_aistudio/image_generation.py"


def test_aistudio_file_wait_has_short_per_request_budget():
    source = UTILS.read_text()

    assert "AISTUDIO_FILE_ACTIVE_TIMEOUT_SECONDS = 30.0" in source
    assert "AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS = 30.0" in source
    assert "AISTUDIO_FILE_ACTIVE_TIMEOUT_SECONDS = 300.0" not in source
    assert "deadline_monotonic: float | None = None" in source
    assert "deadline = min(deadlines)" in source


def test_request_paths_share_aistudio_file_wait_deadline():
    chat_source = CHAT.read_text()
    messages_source = MESSAGES.read_text()
    image_source = IMAGE_GENERATION.read_text()

    deadline_expression = (
        "time.monotonic() + AISTUDIO_FILE_ACTIVE_REQUEST_TIMEOUT_SECONDS"
    )
    assert deadline_expression in chat_source
    assert deadline_expression in messages_source
    propagation = "file_active_deadline_monotonic=file_active_deadline_monotonic"
    assert chat_source.count(propagation) >= 2
    assert messages_source.count(propagation) >= 4
    assert deadline_expression in image_source
    assert "deadline_monotonic=file_active_deadline_monotonic" in image_source
