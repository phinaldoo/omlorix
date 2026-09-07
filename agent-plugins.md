# Agent Plugins

Omlorix supports portable OpenAI Agent Plugin ZIP bundles through **Workspace →
Plugins**. A bundle must contain exactly one `.codex-plugin/plugin.json`
manifest. Omlorix currently installs the capabilities it can execute through its
provider-neutral runtime:

- Agent Skills from declared skill folders, including their `SKILL.md`,
  `scripts/`, `references/`, and `assets/` files.
- Remote streamable-HTTP and SSE MCP servers from a referenced `.mcp.json` or
  an inline `mcpServers` map.
- MCP-hosted app UIs exposed by those servers through Omlorix's existing MCP Apps
  sandbox.

The workflow validates the complete archive, shows a component and warning
preview, then installs the plugin disabled. Enabling or disabling the plugin
controls every installed skill and MCP server as one aggregate. Export returns
the original validated bundle, and uninstall removes all components that still
belong to the plugin.

## Compatibility boundaries

Omlorix preserves the full manifest and source bundle so unknown and future
fields survive export. Two OpenAI-specific capabilities are intentionally not
executed:

- Plugin hooks are untrusted executable automation. They remain portable
  metadata and never run in Omlorix.
- Registered ChatGPT app IDs depend on OpenAI's hosted app registry. They remain
  portable metadata; a plugin must expose its app UI through an installed MCP
  server for Omlorix to render it.

Personal plugins cannot install stdio MCP servers because that would allow an
uploaded archive to start a local backend process. Administrators can continue
to configure reviewed stdio servers through the existing admin MCP controls.

## Security model

Plugin parsing rejects absolute paths, traversal, backslashes, NUL bytes,
duplicate archive paths, symbolic links, excessive file/entry/expanded sizes,
and suspicious compression ratios. Manifest references are independently
confined to the bundle root. MCP headers are persisted through Omlorix's existing
encrypted secret columns and are omitted from plugin API responses. Lifecycle
changes are authenticated, owner-scoped, CSRF-protected by the shared request
dependency, and audit logged.

The implemented format follows OpenAI's current [plugin bundle
documentation](https://developers.openai.com/plugins/build/plugins) and
[security guidance](https://developers.openai.com/plugins/guides/security-privacy).
