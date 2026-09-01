from pathlib import Path


HELPER_SOURCE = Path(__file__).resolve().parents[2] / "app" / "tools" / "helper.py"


def _helper_source() -> str:
    return HELPER_SOURCE.read_text()


def test_skills_feature_check_uses_group_skills_setting():
    source = _helper_source()

    assert (
        '"skills": ("skills", "enabled_skills", "Skills feature disabled for your group."),'
        in source
    )


def test_skills_tool_checks_feature_gate_before_running_tool():
    source = _helper_source()
    branch_start = source.index('elif tool_name == "skills":')
    tool_call = source.index('skills_tool(', branch_start)
    gate_call = source.index('_ensure_feature_enabled(user_id, db, "skills")', branch_start)

    assert gate_call < tool_call
