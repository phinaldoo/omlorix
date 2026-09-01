# LaTeX Rendering

LaTeX rendering is part of the **Canvas** workflow. A model creates or edits a LaTeX document; a shared renderer produces the PDF preview.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then validate the renderer-specific controls below.

## Configure

1. Deploy a compatible, isolated LaTeX rendering service.
2. Add it under **Admin Settings > Service Connections** with **LaTeX rendering** enabled.
3. Enable [Canvas](16_canvas.md) and LaTeX support on the intended models and groups.
4. Test creation, edit, rerender, download, referenced images, invalid LaTeX, and storage limits.

There is no separate user-facing LaTeX tool to assign: Canvas owns the document workflow, while **Service Connections** owns renderer routing.

## Security and operations

LaTeX compilation processes untrusted model and user input. Run the renderer as a separate, strongly isolated service with no unnecessary network access, strict time and resource limits, and only the packages and file access required. Do not use unrestricted shell escape.

Source, referenced assets, and generated PDFs are user files and count toward storage limits. A successful source save can still be followed by a rendering failure; preserve the source and show the compile error without exposing renderer internals.

A healthy Service Connection does not prove that a real document compiles. If preview fails, check renderer health and logs, supported packages, referenced-file access, timeouts, storage quota, and whether the current Canvas revision was saved.
