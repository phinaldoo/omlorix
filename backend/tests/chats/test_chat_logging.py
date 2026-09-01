import ast
from pathlib import Path


_SOURCE_PATH = Path(__file__).resolve().parents[2] / "app" / "chats" / "utils.py"


def _logger_calls_by_message(function_name: str) -> dict[str, str]:
    tree = ast.parse(_SOURCE_PATH.read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name
    )
    calls: dict[str, str] = {}
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name) or node.func.value.id != "logger":
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        calls[node.args[0].value] = node.func.attr
    return calls


def test_successful_chat_generation_milestones_are_debug_logs():
    calls = _logger_calls_by_message("send_message")

    assert calls["[ChatGeneration] utils.provider_resolved user=%s chat=%s gen_id=%s provider=%s"] == "debug"
    assert calls["[ChatGeneration] utils.skills_loaded user=%s chat=%s skill_ids=%s"] == "debug"
    assert calls["[ChatGeneration] utils.prompts_loaded user=%s chat=%s prompt_ids=%s"] == "debug"


def test_successful_pin_ownership_check_is_a_debug_pin_log():
    calls = _logger_calls_by_message("pin_chat")

    assert calls["[PinChat] utils.chat_verified user=%s chat=%s"] == "debug"


def test_regeneration_reserves_error_level_for_failures():
    calls = _logger_calls_by_message("regenerate_message")
    successful_milestones = (
        "[Regenerate] utils.user_msg_verified user=%s chat=%s user_msg=%s",
        "[Regenerate] utils.latest_user_msg_confirmed user=%s chat=%s",
        "[Regenerate] utils.retry_count user=%s chat=%s new_retry=%s",
        "[Regenerate] utils.cancelled_active_generation user=%s chat=%s prev_gen=%s",
        "[Regenerate] utils.new_generation user=%s chat=%s gen_id=%s",
        "[Regenerate] utils.emitted_regen_event user=%s chat=%s gen_id=%s retry=%s",
        "[Regenerate] utils.finished user=%s chat=%s gen_id=%s",
    )

    assert all(calls[message] == "debug" for message in successful_milestones)
    assert calls["[Regenerate] utils.exception user=%s chat=%s gen_id=%s meta=%s"] == "error"
