# Custom Python Tools

**Custom Python Tools** let a model call administrator-supplied Python. They run with the Omlorix application service's permissions and are not a sandbox. A tool can access processes, files, connected services, deployment data, and the network available to that service.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then apply the code-review and isolation requirements below.

Use this feature only for code reviewed and maintained to the same standard as Omlorix itself. Prefer an MCP or dedicated service when stronger isolation, independent deployment, or narrower credentials are required.

## Create and pilot a tool

1. Open **Admin Settings > Tools > Custom Python Tools** and select **Create**.
2. Enter a clear name and description so the model knows when to call it.
3. Add the reviewed Python tool code and its input definition.
4. Validate and test with harmless inputs.
5. Save the tool, then assign it to one restricted model and pilot group.
6. Test valid, invalid, missing, oversized, slow, and repeated requests.

Keep input descriptions precise and outputs small and serializable. Avoid side effects at import or setup time; validation and runtime can load the tool more than once. Make destructive or costly actions explicit and require application-level authorization inside the tool.

For tools that retrieve market or other third-party data, use a contracted provider whose licence covers the intended territories and deployment model. Confirm the rights for automated access, commercial or self-hosted use, caching, derived data, attribution, display, and redistribution before enabling the tool. Omlorix does not include provider credentials or grant rights to external data merely because an administrator can install custom Python code.

## Security and operations

- Never embed secrets in source or exported files. Retrieve only the minimal credential through an approved server-side mechanism.
- Apply strict input validation, authorization, timeouts, rate limits, logging, and output filtering.
- Do not rely on the model to enforce user ownership or to request confirmation.
- Restrict filesystem and network access at the deployment level where possible.
- Review dependencies, outbound destinations, personal data, error messages, and denial-of-service risk.

Disabling or removing the tool stops new calls but does not undo external changes or remove data it already created. Exports can contain executable source and must be treated as sensitive. Review every imported tool before enabling or assigning it.

## Import and export

Open **Admin Settings > Tools > Custom Python Tools** and use **Export All** or **Import All**. The versioned JSON export contains every tool's executable source, resolved metadata, enabled state, and timeout. It can therefore contain embedded credentials or sensitive implementation details; store and transfer it like source code, not like a secret-free settings file.

Import creates fresh records and does not overwrite tools with the same name. It preserves `enabled` and `timeout_seconds`, validates the source contract, and loads the source in the trusted custom-tool runner during inspection. Reserved names, built-in name collisions, existing custom-tool names, invalid source, and invalid timeouts are reported per item.

Import-time inspection executes the source even when `enabled` is `false`. Disabled state blocks later model calls; it is not an import safety boundary. Do not upload source until it has been reviewed and trusted, preferably first on an isolated non-production instance.

For staged restoration of trusted source, set every `data.tools[].enabled` value to `false` in a protected offline copy, import selected tools, read all item errors, inspect and test each tool, then enable it deliberately. Re-enter credentials through an approved server-side mechanism; the bundle is not a credential-transfer format. Model assignments use tool names and must still be checked after model import.

If the model does not call a tool, check that it is enabled, assigned to the model, and allowed for the group. If validation passes but runtime fails, compare the application service's permissions, installed dependencies, inputs, timeout, and connected-service availability.
