# Visualization

**Visualization** lets a model create an interactive chart, comparison, simulator, or explorable explanation inside a chat response. It needs no separate rendering service.

Complete the shared [Tool Rollout Checklist](0_tool_rollout.md), then use the checks below for this tool.

## Enable and test

1. Select **Visualization** on each allowed model.
2. Test a small chart, adjustable controls, wide view, keyboard operation, theme changes, and an invalid visualization.
3. Test each optional action the visualization offers, such as preparing a follow-up message, requesting public external data, or downloading a file.

Visualizations run in a restricted preview and must ask the user before supported host actions. They are still model-generated interactive content: treat inputs, labels, calculations, links, and external data requests as untrusted.

Require accessible labels, keyboard support, sufficient contrast, and a text or table alternative. Users should verify important values against the source data. Interactivity may not survive export or sharing, so important conclusions should also appear in normal chat text.

Use [Canvas](16_canvas.md) for durable editable documents and [Image Generation](6_image_generation.md) for raster artwork.
