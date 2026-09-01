from app.groups.init import get_user_group_setting_value
from app.users.init import get_user_setting_value




# ---------------
# Title Generation
# ---------------
def get_title_generation_prompt(user_id: str, db):
    """Return the title generation system prompt.

    If the user's group's setting "general.title_generation_prompt" is a non-empty
    string, use it. Otherwise, return the default hardcoded prompt.
    """
    user_language = ""
    try:
        user_language = get_user_setting_value(user_id, "general", "language", db)
    except Exception:
        user_language = ""
    language_code = (user_language or "").strip().lower()
    if not language_code:
        language_code = "en"
    language_instruction = f"\n\nUser language (ISO 639-1): {language_code}.\nAlways output the title in this language."

    custom_prompt = None
    try:
        custom_prompt = get_user_group_setting_value(user_id, "general", "title_generation_prompt", db)
    except Exception:
        custom_prompt = None

    if isinstance(custom_prompt, str) and custom_prompt.strip() != "":
        return custom_prompt.rstrip() + language_instruction

    sys_instruct = f"""
    You are a title generator for new chat threads.
    Task: Read only the user's first message and produce a single, short, descriptive title capturing the core topic or goal.

    Rules:
    - Output plain text only. No markdown, quotes, code fences, or surrounding punctuation.
    - Target 2–4 words; hard cap 6 words.
    - English: use Title Case. Other languages: use natural casing for that language.
    - Do NOT include personal data, emails, phone numbers, IDs, file paths, URLs/domains, API keys/tokens, timestamps/dates, or provider/model names.
    - No emojis, hashtags, brackets, or trailing/leading punctuation.
    - Avoid generic words like: Chat, Conversation, Request, Help, Question.
    - Prefer concise noun phrases over sentences.
    - If the message is a question or troubleshooting, convert to a topic-oriented title reflecting the subject (e.g., "PostgreSQL Deadlock Debugging").
    - If a specific technology/framework/library/service is central, include its canonical name (e.g., "Next.js Middleware Caching"), but avoid version numbers unless essential.
    - If code/files are mentioned, focus on the concept (e.g., "JWT Auth Middleware") rather than paths or secrets.
    - Non‑English prompts should receive titles in that language.
    - Trim whitespace.

    Return only the title.
    {language_instruction}
    """
    return sys_instruct
