from fastapi import HTTPException, status

from app.groups.init import get_group_setting_value, get_user_group_setting_value



# -------------------
# Check projects access
# -------------------
def check_projects_access(db, group_id: str):
    """Check if projects are enabled for the given group."""
    access = get_group_setting_value(group_id, "projects", "enable_projects", db)
    if not access:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Projects are disabled for your group")


def ensure_project_sharing_allowed(user_id: str, db):
    """Ensure project sharing is allowed for the user's group."""
    allowed = get_user_group_setting_value(user_id, "projects", "allow_project_share", db)
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project sharing is disabled for your group")