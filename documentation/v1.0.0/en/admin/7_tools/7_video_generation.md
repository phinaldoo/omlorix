# Video Generation

**Video Generation** lets a chat model call a separate video model. Depending on the provider, it can use text and approved current-chat images as references.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify the asynchronous media workflow below.

## Configure

1. Create a supported provider with working billing and video access.
2. Open **Admin Settings > Tools > Video Generation** and select **Configure Video Generation**.
3. Choose the provider and model, then review duration, size, aspect ratio, reference-image, polling, timeout, retry, and cost settings shown.
4. Save and select **Video Generation** on each allowed chat model.
5. Test a short text-only job before testing each reference-image mode.

Video generation is asynchronous and can take several minutes. A job may succeed upstream but fail while Omlorix downloads or stores the result. Test cancellation, timeout, provider error, and storage-quota behavior.

Provider support for duration, resolution, format, start or end frames, multiple references, and moderation varies. Keep settings to combinations verified on the exact model. Generated videos are stored as user files and count toward storage limits.

Prompts and reference images are sent to the provider. Review consent, likeness and copyright policy, provider retention, regional processing, moderation, and disclosure requirements. Apply budgets and use provider billing as the cost authority.

If a model lists but cannot generate, check account entitlement, the selected option combination, reference-image requirements, provider quota, polling timeout, and the application service's access to the download location.
