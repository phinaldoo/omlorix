from pydantic import BaseModel, field_validator, model_validator
from typing import Any, Optional
from typing import List, Literal, Sequence


class OperationResult(BaseModel):
    status: str
    detail: Optional[str] = None


class LegalDocumentAvailability(BaseModel):
    """Navigation-link visibility for the shared public legal-document page."""

    privacy: bool
    terms: bool


class ProxyVerificationResponse(BaseModel):
    """Credential-free result returned to the local proxy controller."""

    client_ip: str
    scheme: Literal["http", "https"]
    host: str
    nonce: str
    trust_chain_accepted: bool





class Option(BaseModel):
    value: str
    label: str
    metadata: Optional[dict] = None
    i18n_label: Optional[str] = None
    translatable: bool = True

    @model_validator(mode="after")
    def _attach_default_i18n_key(self):
        if self.translatable and not self.i18n_label:
            from app.utils.schema_i18n import get_schema_i18n_key

            self.i18n_label = get_schema_i18n_key(self.label)
        return self


class FieldAttributes(BaseModel):
    min: Optional[float] = None
    max: Optional[float] = None
    # Numeric settings can require fractional increments (for example xAI's
    # 0–1 confidence thresholds). Preserve the schema-provided HTML step so
    # browsers do not reject valid decimal values as step mismatches.
    step: Optional[float] = None



FieldTypeLiteral = Literal[
    "boolean",
    "string",
    "string_list",
    "textarea",
    "select",
    "select_multi",
    "number",
    "access_rules",
    "context_files",
    "boolean_map",
    "json",
]


class FieldSchema(BaseModel):
    key: str
    label: str
    description: str
    type: FieldTypeLiteral
    options: Optional[Sequence[Option]] = None
    metadata: Optional[dict] = None
    attributes: Optional[FieldAttributes] = None
    placeholder: Optional[str] = None
    input_type: Optional[str] = None
    multiple: Optional[bool] = None
    searchable: Optional[bool] = None
    required: Optional[bool] = None
    default: Optional[Any] = None
    value: Optional[Any] = None
    dependency: Optional[str] = None
    dependency_value: Optional[Any] = None
    dependency2: Optional[str] = None
    dependency2_value: Optional[Any] = None
    dependency3: Optional[str] = None
    dependency3_value: Optional[Any] = None
    redact_value: Optional[bool] = None
    masked_placeholder: Optional[bool] = None
    # Indicates that a redacted value exists without returning the value. This
    # lets schema-driven clients preserve an untouched secret on full-form save.
    masked_value_set: Optional[bool] = None
    mask_preview_chars: Optional[int] = None
    hidden: Optional[bool] = None
    max_length: Optional[int] = None
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    rows: Optional[int] = None
    i18n_label: Optional[str] = None
    i18n_description: Optional[str] = None
    i18n_placeholder: Optional[str] = None
    hide_on_byok: Optional[bool] = None

    @model_validator(mode="after")
    def _attach_default_i18n_keys(self):
        from app.utils.schema_i18n import get_schema_i18n_key

        if not self.i18n_label:
            self.i18n_label = get_schema_i18n_key(self.label)
        if not self.i18n_description:
            self.i18n_description = get_schema_i18n_key(self.description)
        if self.placeholder and not self.i18n_placeholder:
            self.i18n_placeholder = get_schema_i18n_key(self.placeholder)
        return self

    @field_validator("options", mode="before")
    @classmethod
    def _coerce_options(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, list):
            coerced: List[Option] = []
            for item in value:
                if isinstance(item, Option):
                    coerced.append(item)
                elif isinstance(item, dict):
                    coerced.append(Option(**item))
                else:
                    raise TypeError("FieldSchema options must be Option or dict entries")
            return coerced
        return value


class Section(BaseModel):
    key: str | None = None
    title: str | None = None
    description: str | None = None
    fields: List[FieldSchema]
    i18n_title: Optional[str] = None
    i18n_description: Optional[str] = None
    # Optional group metadata lets settings pages express a two-level visual
    # hierarchy while preserving the existing flat list of fields and sections.
    # Consecutive sections with the same group title are rendered together.
    group_title: Optional[str] = None
    group_description: Optional[str] = None
    i18n_group_title: Optional[str] = None
    i18n_group_description: Optional[str] = None

    @model_validator(mode="after")
    def _attach_default_i18n_keys(self):
        from app.utils.schema_i18n import get_schema_i18n_key

        if self.title and not self.i18n_title:
            self.i18n_title = get_schema_i18n_key(self.title)
        if self.description and not self.i18n_description:
            self.i18n_description = get_schema_i18n_key(self.description)
        if self.group_title and not self.i18n_group_title:
            self.i18n_group_title = get_schema_i18n_key(self.group_title)
        if self.group_description and not self.i18n_group_description:
            self.i18n_group_description = get_schema_i18n_key(self.group_description)
        return self



class Sections(BaseModel):
    sections: List[Section]


def _set_schema_field_value(schema: Sections | None, field_key: str, value) -> bool:
    if not schema or not getattr(schema, "sections", None) or value is None:
        return False
    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            if getattr(field, "key", None) == field_key:
                field.value = value
                return True
    return False


def _get_field_from_section(sections: list[Section], section_title: str, field_key: str):
    for section in sections or []:
        if section.title == section_title:
            for field in section.fields:
                if field.key == field_key:
                    return field
    return None


def _remove_field_from_section(sections: list[Section], section_title: str, field_key: str):
    section = next((s for s in (sections or []) if s.title == section_title), None)
    if not section:
        return
    section.fields = [field for field in section.fields if field.key != field_key]


def _remove_section_from_sections(sections: list[Section], section_title: str):
    if not sections:
        return
    sections[:] = [s for s in sections if s.title != section_title]


def resolve_schema_value(payload: Any, dotted_key: str):
    if not isinstance(dotted_key, str) or not dotted_key:
        return None
    current = payload
    for part in dotted_key.split('.'):
        if current is None:
            return None
        if isinstance(current, dict):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
    return current


def _mask_preview(value: Any, visible_chars: int = 3) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) <= 6:
        return None
    visible_chars = max(0, min(visible_chars, 3, len(text) - 1))
    if visible_chars == 0:
        return None
    preview = text[:visible_chars]
    return f"{preview}..."


def populate_sections_with_values(schema: Sections | None, payload: dict | None):
    if not schema or not getattr(schema, "sections", None) or not payload:
        return schema
    for section in schema.sections:
        for field in getattr(section, "fields", []) or []:
            value = resolve_schema_value(payload, getattr(field, "key", ""))
            if value is None:
                continue

            if getattr(field, "redact_value", False):
                if getattr(field, "masked_placeholder", False):
                    has_value = bool(value.strip()) if isinstance(value, str) else bool(value)
                    field.masked_value_set = has_value
                    visible_chars = getattr(field, "mask_preview_chars", None)
                    placeholder_value = _mask_preview(value, visible_chars or 3)
                    if has_value:
                        # Do not expose prefixes for short secrets. The fixed
                        # mask still tells the administrator a value is saved.
                        field.placeholder = placeholder_value or "********"
                continue

            field.value = value
    return schema
