"""ACP client capabilities and prompt attachment construction.

The historical ``runtime`` module remains the public facade. Dependencies are
synchronized before calls to preserve its established patching surface.
"""

from __future__ import annotations

# Extracted implementations retain intentional diagnostic assignments.
# ruff: noqa: F821, F841

from app.llm.acp import runtime as _compat_source

_COMPAT_DEPENDENCIES = {
    "omlorix_client_capabilities": (
        "BooleanConfigOptionCapabilities",
        "ClientCapabilities",
        "ClientSessionCapabilities",
        "SessionConfigOptionsCapabilities",
    ),
    "attachment_has_usable_acp_representation": (),
    "build_acp_prompt_blocks": (
        "_attachment_metadata_text",
        "_attachment_uri",
        "audio_block",
        "embedded_blob_resource",
        "embedded_text_resource",
        "image_block",
        "resource_block",
        "text_block",
    ),
}


def _sync_compat_dependencies(function_name, facade_globals):
    """Refresh globals that callers historically patched on the facade."""
    for dependency_name in _COMPAT_DEPENDENCIES[function_name]:
        if dependency_name in facade_globals:
            globals()[dependency_name] = facade_globals[dependency_name]


for _dependency_name in (
    "BooleanConfigOptionCapabilities",
    "ClientCapabilities",
    "ClientSessionCapabilities",
    "SessionConfigOptionsCapabilities",
    "_attachment_metadata_text",
    "_attachment_uri",
    "audio_block",
    "embedded_blob_resource",
    "embedded_text_resource",
    "image_block",
    "resource_block",
    "text_block",
):
    if hasattr(_compat_source, _dependency_name):
        globals()[_dependency_name] = getattr(_compat_source, _dependency_name)


def _impl_omlorix_client_capabilities() -> ClientCapabilities:
    """Advertise the stable ACP session configuration features Omlorix renders."""
    return ClientCapabilities(
        session=ClientSessionCapabilities(
            config_options=SessionConfigOptionsCapabilities(
                boolean=BooleanConfigOptionCapabilities(),
            )
        )
    )


def _impl_attachment_has_usable_acp_representation(
    attachment: dict[str, Any],
    capabilities: Any,
) -> bool:
    """Return whether the agent receives actual attachment content, not metadata alone."""
    if not isinstance(attachment, dict):
        return False
    prompt_capabilities = getattr(capabilities, "prompt_capabilities", None)
    category = str(attachment.get("category") or "unknown").lower()
    has_data = bool(str(attachment.get("data") or ""))
    has_text = bool(str(attachment.get("text") or "").strip())
    if has_text:
        # Extracted text is always usable, either as a Resource or plain text.
        return True
    if bool(getattr(prompt_capabilities, "embedded_context", False)) and has_data:
        return True
    if category == "image":
        return bool(getattr(prompt_capabilities, "image", False)) and has_data
    if category == "audio":
        return bool(getattr(prompt_capabilities, "audio", False)) and has_data
    return False


def _impl_build_acp_prompt_blocks(
    prompt_text: str,
    attachments: list[dict[str, Any]] | None,
    capabilities: Any,
) -> list[Any]:
    """Build capability-gated ACP content blocks for one prompt.

    Binary content is sent only through ACP-native blocks explicitly advertised
    by the agent. Extracted document text remains available to text-only agents.
    """
    blocks: list[Any] = [text_block(prompt_text)]
    prompt_capabilities = getattr(capabilities, "prompt_capabilities", None)
    supports_images = bool(getattr(prompt_capabilities, "image", False))
    supports_audio = bool(getattr(prompt_capabilities, "audio", False))
    supports_resources = bool(getattr(prompt_capabilities, "embedded_context", False))

    for attachment in attachments or []:
        if not isinstance(attachment, dict):
            continue
        file_id = str(attachment.get("file_id") or "").strip()
        name = str(attachment.get("name") or file_id or "attachment")
        mime_type = str(attachment.get("mime_type") or "application/octet-stream")
        category = str(attachment.get("category") or "unknown").lower()
        data = str(attachment.get("data") or "")
        extracted_text = str(attachment.get("text") or "").strip()
        uri = _attachment_uri(file_id)

        if category == "image" and supports_images and data:
            blocks.append(
                text_block(
                    _attachment_metadata_text(
                        attachment,
                        representation="native_image",
                    )
                )
            )
            blocks.append(image_block(data, mime_type, uri=uri))
            continue

        if category == "audio" and supports_audio and data:
            blocks.append(
                text_block(
                    _attachment_metadata_text(
                        attachment,
                        representation="native_audio",
                    )
                )
            )
            blocks.append(audio_block(data, mime_type))
            continue

        if supports_resources and extracted_text:
            blocks.append(
                text_block(
                    _attachment_metadata_text(
                        attachment,
                        representation="embedded_text_resource",
                    )
                )
            )
            blocks.append(
                resource_block(
                    embedded_text_resource(
                        uri,
                        extracted_text,
                        mime_type=mime_type,
                    )
                )
            )
            continue

        if supports_resources and data:
            blocks.append(
                text_block(
                    _attachment_metadata_text(
                        attachment,
                        representation="embedded_binary_resource",
                    )
                )
            )
            blocks.append(
                resource_block(
                    embedded_blob_resource(
                        uri,
                        data,
                        mime_type=mime_type,
                    )
                )
            )
            continue

        # Text extraction is deliberately a plain text fallback because an
        # agent that did not advertise embeddedContext cannot accept Resource.
        representation = "extracted_text" if extracted_text else "metadata_only"
        blocks.append(
            text_block(
                _attachment_metadata_text(
                    attachment,
                    representation=representation,
                )
            )
        )
        if extracted_text:
            blocks.append(
                text_block(f"[Extracted content from {name}]\n{extracted_text}")
            )
    return blocks
