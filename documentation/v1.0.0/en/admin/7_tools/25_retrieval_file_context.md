# File Context

Omlorix can read or extract content from files a user attaches or selects and include that content in a model request. Availability depends on the model's file capabilities and the user's group permissions.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the file-specific validation below.

## Configure safely

1. Enable only the file types and attachment limits supported by the exact model.
2. Verify group file access, storage limits, and malware scanning.
3. Test a permitted file, a denied file, an oversized file, unsupported content, and a source the user does not own.

Extracted text is untrusted and can contain false information or prompt injection. It must not override authorization or safety policy. The model may omit relevant passages or cite the wrong section, so users should verify important conclusions in the source.

Selected file content can be sent to the configured model provider. Review the provider's processing region, logging, retention, and file-support policy before enabling file input.

If a model ignores a file, check its input capabilities, the file's ownership and status, extraction support, limits, and selected context. If file context works for an administrator but not a user, verify group and source permissions with a normal account.
