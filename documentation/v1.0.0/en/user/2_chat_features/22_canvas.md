# Canvas

Canvas creates and edits longer content in a focused panel beside the chat. A model with Canvas can create **Markdown**, **Mermaid diagram**, **CSV table**, **HTML website**, and **LaTeX PDF** or **LaTeX document** artifacts. Canvas can also open and edit compatible existing **CSV**, **TSV**, **XLS**, and **XLSX** spreadsheet files, including files created through Code Execution or added to Workspace.

## Create and revise

Choose a model that lists **Canvas** and ask for the format, filename, audience, structure, and content you need. Select **Open Canvas** when the result card appears.

Edit the content directly or ask for a focused revision. Identify the file, exact section, and what must remain unchanged. For Markdown, switch between **Editor** and **Markdown**, and use formatting, tables, links, images, Undo, Redo, or History as needed. Wait for **Saved** before closing or referring to the latest version.

Generated Canvas files are stored in Workspace Files. Available previews and downloads depend on the format: Markdown can download as **MD** or **PDF**, HTML as **HTML** or **PNG image**, spreadsheets as CSV, TSV, or Excel, and LaTeX as **PDF** or **TeX source**.

## Edit spreadsheets and LaTeX

The spreadsheet editor can provide **Undo**, **Redo**, **Find in sheet**, a formula bar, multiple sheets, row and column controls, formulas, and selection statistics. **Compatibility mode** preserves workbook features such as charts, merged cells, or external links and restricts structural changes that could damage them. A **Legacy XLS mode** notice means cells and formulas can be edited, but embedded objects may not survive; use XLSX for safer workbook editing.

A LaTeX preview can show **Preview is out of date** after an edit. Save the source, then select **Render preview**. A successful save does not mean the document compiled successfully, so resolve any render error before downloading or sharing the PDF.

## References and previews

A Canvas can reference Workspace files. Files you own are available through that Canvas. A file owned by someone else can require its owner to select **Allow in Canvas**; until then, the preview remains blocked. Removing a reference removes that Canvas access.

HTML previews block external connections until you review the requested destinations. Use **Keep blocked** unless the preview genuinely needs trusted external content; you can change this later under **HTML preview settings**. Treat generated pages, links, scripts, formulas, and diagrams as untrusted until checked.

Preview and exported output can differ from editable content. Verify layout, calculations, links, accessibility, and referenced files before publishing. For public links and the separate approval required for another person’s files, see [Canvas Sharing](14_artifact_sharing.md).
