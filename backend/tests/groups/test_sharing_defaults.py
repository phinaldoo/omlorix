from app.groups.defaults import DEFAULT_GROUP_SETTINGS


def test_all_group_sharing_permissions_default_to_enabled():
    """Keep every sharing surface enabled in newly initialized group settings."""

    sharing_permissions = {
        "project": DEFAULT_GROUP_SETTINGS["projects"]["allow_project_share"],
        "todo_list": DEFAULT_GROUP_SETTINGS["todo"]["allow_todo_list_share"],
        "notes": DEFAULT_GROUP_SETTINGS["notes"]["allow_notes_share"],
        "skills": DEFAULT_GROUP_SETTINGS["skills"]["allow_skill_share"],
        "prompts": DEFAULT_GROUP_SETTINGS["prompts"]["allow_prompt_share"],
        "bookmarks": DEFAULT_GROUP_SETTINGS["bookmarks"]["allow_bookmark_share"],
        "agents": DEFAULT_GROUP_SETTINGS["agents"]["allow_agent_share"],
        "chat": DEFAULT_GROUP_SETTINGS["sharing"]["enable_chat_sharing"],
        "artifacts": DEFAULT_GROUP_SETTINGS["sharing"]["enable_artifact_sharing"],
    }

    assert sharing_permissions == {
        "project": True,
        "todo_list": True,
        "notes": True,
        "skills": True,
        "prompts": True,
        "bookmarks": True,
        "agents": True,
        "chat": True,
        "artifacts": True,
    }
