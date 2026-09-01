# Image Generation and Editing

**Image Generation** lets a chat model call a separate image model. The chat model interprets the request; the configured image model creates or edits the image.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify the media-specific options below.

## Configure

1. Create and test a supported [Provider](../5_llmprovider/1_introduction.md).
2. Open **Admin Settings > Tools > Image Generation** and select **Configure Image Generation**.
3. Choose the provider and image model, then review the visible format, size, quality, editing, reference-image, timeout, and cost settings.
4. Save, edit each allowed chat model, and select **Image Generation**.
5. Keep access with the pilot group until the following tests pass.

Test at least:

- a simple text-to-image request;
- every allowed size or aspect ratio;
- editing with one and multiple current-chat images, when enabled;
- refusal or invalid-input handling;
- file download, deletion, and storage quota behavior.

## Provider differences

Supported sizes, formats, quality levels, image editing, number of references, and moderation vary by provider and model. A discovered model is not proof that an image endpoint or editing is available. Keep the allowed choices narrow and verified.

Generated images are stored as user files and count toward file storage limits. Deleting a chat does not necessarily remove every related file; apply the normal file and retention policies.

Prompts and reference images are sent to the selected provider. Review rights, consent, personal data, provider training and retention, moderation, regional processing, and generated-content labeling. Use provider billing as the cost authority.

If the chat model does not call the tool, confirm that **Image Generation** is selected on that model and allowed for the group. If editing fails, verify that editing is enabled, the provider supports it, and the reference image is in the current accessible chat.
