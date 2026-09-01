# Group context
from app.groups.init import get_user_group_setting_value
from app.groups.models import get_group

def get_group_context_start(db, user_id: str):
    group_context = get_user_group_setting_value(user_id, "context", "group_context", db)
    group_name = ""
    start = """
    The user is part of the group """ + group_name + """. To make your response more accurate, consider the following context of the group as background knowledge for your response: """ + group_context + """.
    """
    group_context_file_ids = get_user_group_setting_value(user_id, "context", "group_context_file_ids", db)
    if group_context_file_ids:
        start += """
        The group has also the following files as context:
        """
    return {
        "context": start,
        "group_context_file_ids": group_context_file_ids 
    }



def get_group_context_end():
    end = """
    This was all the group context. Now the main chat conversations starts.
    """
    return end