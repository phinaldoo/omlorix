# Interactive Visualizations

Omlorix can show live charts, comparisons, simulations, maps, and interface previews directly in a response. With **Visualization** enabled for the model, ask to explore a concept or provide data and the controls you want. The model can also choose a visual when it helps explain an answer.

Local interactions work as soon as the visual appears. Adjust sliders, change selections, or explore details without sending another message. The toolbar lets you:

- **View source** to inspect the generated HTML while preserving your selection.
- **Reset visualization** to restore its initial values.
- **Download HTML** to save a self-contained copy with its embedded data and local libraries.
- **Open large preview** to expand the running visual without restarting it. Close it with the close button or Escape.

When supplied by the model, **Text alternative** provides an accessible summary, including assumptions and key results. Ask for a table or a clearer explanation if the visual is difficult to use. Generated calculations still need checking against the original data.

Omlorix's light and dark themes apply to the visual. Controls adapt to narrow screens. Selections survive source view and expansion, but reset when you reload the chat. Revisions appear as new visuals so the previous result remains in the conversation.

Examples include stacked charts with inspectable values, activity heatmaps, zoomable code treemaps with filters, and geographic maps with country selection. Maps need embedded geographic data as well as the values being plotted; the bundled TopoJSON library does not contain country shapes. Remote map tiles and CDN libraries are unavailable inside the sandbox. Simplified embedded geometry keeps maps interactive and portable in HTML exports.

Visualizations run in an isolated sandbox. They cannot access your application session, navigate away, or contact external services directly. Optional actions can propose a follow-up message, request public data, or offer a generated download. Omlorix asks you to review these actions before proceeding. Public chat shares keep embedded local interactions but disable follow-up messages and external-data requests.

Chat backup/export and restore retain the visual's source and metadata. The toolbar's HTML download preserves local interactivity in a standalone file, with host actions disabled; it starts from the original values. Plain-text or Markdown chat downloads do not provide an interactive renderer.

Use [Canvas](22_canvas.md) for a durable editable document and [Image Generation](24_image_generation.md) for a static picture.
