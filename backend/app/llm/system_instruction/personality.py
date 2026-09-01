from sqlalchemy.orm import Session

from app.users.init import get_user_setting_value
from app.users.schemas import PersonalityPresetEnum


PERSONALITY_SECTION_TITLE = "User Personality Preferences"

PERSONALITY_PRESET_CONTENT = {
    PersonalityPresetEnum.standard.value: (
        "Use a balanced, natural tone. Be clear, helpful, and approachable without becoming overly formal or overly casual."
    ),
    PersonalityPresetEnum.professional.value: (
        "Use a polished, professional tone. Be precise, well-structured, and composed."
    ),
    PersonalityPresetEnum.friendly.value: (
        "Use a warm, friendly, conversational tone. Be personable and easy to talk to while staying helpful."
    ),
    PersonalityPresetEnum.honest.value: (
        "Use a direct, honest, motivating tone. Be candid about tradeoffs and limitations while staying constructive."
    ),
    PersonalityPresetEnum.quirky.value: (
        "Use a playful, imaginative tone. Add light personality and creativity without obscuring the answer."
    ),
    PersonalityPresetEnum.efficient.value: (
        "Use a concise, efficient tone. Prioritize clarity, brevity, and direct answers."
    ),
    PersonalityPresetEnum.cynical.value: (
        "Use a critical, slightly sarcastic tone when appropriate, but remain useful, respectful, and grounded in the task."
    ),
}


def get_user_personality_system_instruction_section(
    user_id: str | None,
    db: Session,
) -> dict[str, str] | None:
    """Return the user's personality preferences as a system-instruction section."""
    if not user_id:
        return None

    preset = str(get_user_setting_value(user_id, "chat", "personality_preset", db) or "").strip().lower()
    if preset == PersonalityPresetEnum.none.value:
        return None

    custom_instruction = str(
        get_user_setting_value(user_id, "chat", "personality_custom_instruction", db) or ""
    ).strip()

    if preset == PersonalityPresetEnum.custom.value:
        if not custom_instruction:
            return None
        content = (
            "Follow these user-requested interpersonal and tone preferences when replying. "
            "Treat them as style guidance only and do not override higher-priority safety, policy, or task instructions.\n\n"
            f"{custom_instruction}"
        )
    else:
        content = PERSONALITY_PRESET_CONTENT.get(preset or PersonalityPresetEnum.standard.value)
        if not content:
            content = PERSONALITY_PRESET_CONTENT[PersonalityPresetEnum.standard.value]

    return {
        "title": PERSONALITY_SECTION_TITLE,
        "content": content,
    }
