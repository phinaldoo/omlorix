"""Shared labels and translation keys for reasoning effort selectors."""

from app.utils.schemas import Option


REASONING_EFFORT_OPTION_I18N: dict[str, tuple[str, str]] = {
    "none": ("Off", "llm.shared.option.off"),
    "on": ("On", "llm.shared.settings.reasoning_effort.option.on"),
    "minimal": ("Minimal", "llm.shared.settings.reasoning_effort.option.minimal"),
    "low": ("Low", "llm.shared.settings.reasoning_effort.option.low"),
    "medium": ("Medium", "llm.shared.settings.reasoning_effort.option.medium"),
    "high": ("High", "llm.shared.settings.reasoning_effort.option.high"),
    "xhigh": ("Extra high", "llm.shared.settings.reasoning_effort.option.xhigh"),
    "max": ("Max", "llm.shared.settings.reasoning_effort.option.max"),
}


def build_reasoning_effort_option(value: str) -> Option:
    """Build a translated reasoning effort option for a provider-specific value."""
    normalized = str(value or "").strip().lower()
    label, i18n_label = REASONING_EFFORT_OPTION_I18N.get(normalized, (str(value), None))
    return Option(value=value, label=label, i18n_label=i18n_label)


def build_reasoning_effort_options(values: list[str] | tuple[str, ...]) -> list[Option]:
    """Build translated reasoning effort options while preserving provider order."""
    return [build_reasoning_effort_option(value) for value in values]
