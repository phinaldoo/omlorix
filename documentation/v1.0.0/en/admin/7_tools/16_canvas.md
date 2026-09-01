# Canvas

**Canvas** lets users create and edit longer artifacts beside the chat. The model-facing Canvas tool can create or update Markdown, Mermaid diagrams, CSV tables, HTML pages, and LaTeX documents with PDF previews. The Canvas editor can also open and edit compatible existing CSV, TSV, XLS, and XLSX spreadsheet files. A configured **Slide Presentation** tool creates presentations that users can then revise in the Canvas presentation editor.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then verify every enabled format and sharing mode.

## Enable and test

1. Review Canvas and sharing permissions for the intended groups.
2. Edit each allowed model and select **Canvas**.
3. Add healthy [Service Connections](14_service_connections.md) for LaTeX or Slides when those formats are needed.
4. Test each allowed format with a pilot user.

For each model-created Canvas format, test create, open, edit, concurrent edit, download, delete, storage quota, and an invalid or oversized file. For HTML, test external-content consent and public sharing. Open each supported spreadsheet format and verify saved values and formulas in another viewer. For LaTeX and Slides, test the configured renderer as well as source editing.

## Files and sharing

Canvas content, source, assets, previews, and generated downloads are user files and count toward storage limits. Referenced files must remain accessible to the user; deleting or moving them can affect later previews.

When **Canvas sharing** is allowed for the group, users can create a public link for supported content. Anyone with the link may be able to view it without signing in. Revoking a link stops future access but cannot recall copies already downloaded. See [Legal Pages](../3_admin_settings/22_3_legal_pages.md) and [File Storage](../3_admin_settings/20_file_storage.md) when defining policy.

## Security

Treat generated Markdown, HTML, spreadsheets, LaTeX, and presentations as untrusted. Omlorix applies preview restrictions, but administrators must still isolate renderers, limit external network access, scan uploaded assets, and teach users not to enable external content they do not trust.

Do not use model instructions as an authorization boundary. File ownership, group access, tool access, and public-share policy must carry the restriction.

If the model replies in chat instead of opening Canvas, check model assignment and group access. If source saves but preview fails, check the format-specific renderer, current revision, referenced assets, storage quota, and browser error. If **Share** is missing, verify group Canvas-sharing policy and that a current preview exists.
