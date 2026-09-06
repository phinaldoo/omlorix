# Slide Presentations

A model with **Slide presentation** can create and edit a deck of up to 50 slides from a saved Markdown brief.

## Create the deck

Prepare a Markdown file containing the audience, purpose, key messages, evidence, source notes, desired slide count, and visual direction. Save it in Workspace Files, then ask the model to create a presentation from that brief. Add only approved images that the deck must use.

Generation can take time while Omlorix builds, renders, and reviews the slides. A presentation specialist keeps your brief in the same conversation while inspecting slide images and making targeted corrections. It may use Code Execution to check calculations or generate chart and image assets when helpful; this is optional. The preview updates after successful rendering passes. Keep the chat open and wait for the presentation card to become ready.

## Review, edit, and present

Select **View Presentation** to open the preview. Use **Outline** to navigate slides and **Present** for the slideshow view.

The slideshow displays the saved HTML directly, scaled to fit the screen while preserving the slide’s proportions. Text and graphics remain browser-rendered at fullscreen size. Saved decks can open and present before preview images finish loading. Interactive decks can include quizzes, clickable controls, scenario sliders, animated charts, custom slide transitions, public API data, and embeddable websites. Quiz answers and controls survive slide changes within the session; reopening starts fresh. They are not automatically shared with other viewers or saved to your account.

Select **Edit presentation** for direct changes to text, slides, layout, typography, colors, and other visible design settings. **Present** saves your edits and opens the HTML slideshow without waiting for image rendering. The visual editor preserves scripts, network declarations, embed URLs, and export templates, but does not execute the interactions while editing. Use **Present** to try them. The sidebar preview and thumbnails refresh separately. Save and wait for the preview to update before exporting. If the presentation changed elsewhere, reload it before saving.

Both the preview sidebar and fullscreen editor offer **HTML source**, **PPTX**, **PDF**, and **Images** downloads. **HTML source** downloads the complete saved `.html` document, including all slides, styles, scripts, embedded assets. The editor saves pending edits first; this download remains available while rendered previews are updating or unavailable. To replace or edit the entire source, use **Code → Whole deck → Apply changes** in the presentation editor. The downloaded source does not include Omlorix’s injected slideshow player or the current viewer’s interactive state; use it for source editing and reuse in Omlorix.

Rendered images are used for sidebar previews, thumbnails, AI visual review, PDF export and image downloads. **PPTX**, **PDF** and **Images** capture the live HTML in the external browser renderer, including JavaScript and internet content. There is no separate static or offline version. Exports start a fresh session, so they do not capture your current slideshow answers or control values. The visual editor also runs the deck's JavaScript and external content. Applying HTML changes or undoing them restarts the scripts. Check exported deliverables for clipping, font changes and missing media.

Public APIs must permit browser CORS requests; websites may prohibit embedding. Decks report errors when a service is unavailable. No account tokens or API secrets should be placed in a presentation.

Presentation records and their stored artifacts are included in **Download Everything** and are restored by the complete account-archive import. A downloaded PPTX, PDF, or image bundle is a deliverable, not an Omlorix presentation archive, and cannot be imported through Data Control. After an account import, open the restored deck and verify its source file, preview, and every output format.

Verify every fact, number, citation, name, date, and image before presenting. Use only material you are authorized to process and distribute.

See [Canvas](22_canvas.md), [File Attachments](21_file_attachments.md), and [Workspace Files](../10_workspace/6_files.md).
