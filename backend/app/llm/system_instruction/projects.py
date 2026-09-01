from app.projects.models import get_project_with_access, _with_settings_defaults



def get_project_context_start(db, user_id: str, project_id: str):
    project = get_project_with_access(db, user_id, project_id)
    project_title = project.title or "Unnamed Project"
    project_settings = _with_settings_defaults(project.settings)
    project_description = project_settings.get("system_instruction", "")
    project_start = f"""
    This chat conversation is part of the project "{project_title}".
    The user describes the project as follows: "{project_description}".
    Now there are the project files:
    """

    return project_start


def get_project_context_end():
    project_end = """
    This was all the project context and files. Now the main chat conversation starts.
    """
    return project_end
