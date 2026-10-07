# Interactive Visualizations

Omlorix can show live charts, comparisons, simulations, maps, and interface previews directly in a response. With **Visualization** enabled for the model, ask to explore a concept or provide data and the controls you want. The model can also choose a visual when it helps explain an answer.

Local interactions work as soon as the visual appears. Adjust sliders, change selections, or explore details without sending another message. The toolbar lets you:

- **View source** to inspect the generated HTML while preserving your selection.
- **Reset visualization** to clear saved selections and design changes and restore the initial values.
- **Design controls**, when the model declares them, to adjust sliders, colors, toggles, and choices. Compare with **Preview original**, use **Reset design**, or **Apply in chat** to review a follow-up describing the changes.
- **Download HTML** to save a self-contained copy with its embedded data and local libraries.
- **Open large preview** to expand the running visual without restarting it. Close it with the close button or Escape.

When supplied by the model, **Text alternative** provides an accessible summary, including assumptions and key results. Ask for a table or a clearer explanation if the visual is difficult to use. Generated calculations still need checking against the original data.

Omlorix's light and dark themes apply to the visual. Controls adapt to narrow screens. Visuals authored with saved state restore their selections when you reload your chat. Mockup design settings and named variant selections are saved automatically. The status shows whether changes have been saved; retry failed saves, or reload saved selections if another tab changed the same visual. Temporary conversations and shared previews retain changes only for the current session. Revisions appear as new visuals so the previous result remains in the conversation.

Examples include stacked charts with inspectable values, activity heatmaps, zoomable code treemaps with filters, and geographic maps with country selection. Maps need embedded geographic data as well as the values being plotted; the bundled TopoJSON library does not contain country shapes. Remote map tiles and CDN libraries are unavailable inside the sandbox. Simplified embedded geometry keeps maps interactive and portable in HTML exports.

Visualizations run in an isolated sandbox. They cannot access your application session, navigate away, or contact external services directly. Optional actions can propose a follow-up message, request public data, or offer a generated download. Omlorix asks you to review these actions before proceeding. Public chat shares keep embedded local interactions but disable follow-up messages and external-data requests.

Chat backup/export and restore retain the visual's source, metadata, and saved selections. Private restore data is kept out of model prompts; the model can receive concise selections declared for follow-ups and saved design changes. Saving or switching variants does not send a message. The toolbar's HTML download preserves local interactivity in a standalone file, with host actions disabled; it starts from the current selections and design, including private restore data. Plain-text or Markdown chat downloads do not provide an interactive renderer.

Use [Canvas](22_canvas.md) for a durable editable document and [Image Generation](24_image_generation.md) for a static picture.
