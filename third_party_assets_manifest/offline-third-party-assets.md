# Third-Party Frontend Assets

Omlorix vendors its browser libraries, plugins, styles, and fonts so the web UI does not need a public CDN at runtime. The authoritative machine-readable inventory is [`offline-third-party-assets.manifest.json`](offline-third-party-assets.manifest.json).

## Complete inventory

| Component | Version | License | Vendored location | Where it is used |
| --- | --- | --- | --- | --- |
| DOMPurify | 3.2.4 | Apache-2.0 OR MPL-2.0 | `frontend/js/vendor/purify.min.js` | Sanitizes HTML before it is inserted into chat, canvas, share, and legal pages. |
| QRCode.js | 1.0.0 | MIT | `frontend/js/vendor/qrcode.min.js` | Renders QR codes for two-factor authentication setup. |
| CodeMirror | 5.65.16 | MIT | `frontend/js/vendor/codemirror/`, `frontend/css/vendor/codemirror/` | Provides source editing, syntax modes, and editor helpers for canvas content. |
| D3 | 7.9.0 | ISC | `frontend/js/vendor/d3.min.js` | Builds interactive data visualizations in chat responses. |
| Mermaid | 11.4.1 | MIT | `frontend/js/vendor/mermaid.min.js` | Renders Mermaid diagrams in chats and shared canvases. |
| JSZip | 3.10.1 | MIT OR GPL-3.0-or-later | `frontend/js/vendor/jszip.min.js` | Creates and reads ZIP archives for data export and administration workflows. |
| markdown-it | 13.0.1 | MIT | `frontend/js/vendor/markdown/markdown-it.min.js` | Parses Markdown for chat messages and canvas documents. |
| markdown-it-abbr | 1.0.4 | MIT | `frontend/js/vendor/markdown/markdown-it-abbr.min.js` | Adds Markdown abbreviation syntax. |
| markdown-it-deflist | 2.1.0 | MIT | `frontend/js/vendor/markdown/markdown-it-deflist.min.js` | Adds Markdown definition-list syntax. |
| markdown-it-mark | 3.0.1 | MIT | `frontend/js/vendor/markdown/markdown-it-mark.min.js` | Adds highlighted-mark syntax to Markdown. |
| markdown-it-sub | 1.0.0 | MIT | `frontend/js/vendor/markdown/markdown-it-sub.min.js` | Adds subscript syntax to Markdown. |
| markdown-it-sup | 1.0.0 | MIT | `frontend/js/vendor/markdown/markdown-it-sup.min.js` | Adds superscript syntax to Markdown. |
| markdown-it-task-lists | 2.1.0 | ISC | `frontend/js/vendor/markdown/markdown-it-task-lists.min.js` | Adds task-list checkboxes to Markdown. |
| Vega | 5.33.1 | BSD-3-Clause | `frontend/js/vendor/vega.min.js` | Runs declarative Vega visualizations in chat responses. |
| PrismJS | 1.29.0 | MIT | `frontend/js/vendor/prism/`, `frontend/css/prism/` | Highlights source code with an autoloaded catalog of language grammars. |
| Vega-Lite | 5.23.0 | BSD-3-Clause | `frontend/js/vendor/vega-lite.min.js` | Compiles concise Vega-Lite specifications into Vega visualizations. |
| Vega-Embed | 6.29.0 | BSD-3-Clause | `frontend/js/vendor/vega-embed.min.js` | Embeds Vega and Vega-Lite visualizations into the page. |
| Chart.js | 4.4.1 | MIT | `frontend/js/vendor/chart.umd.min.js` | Draws administrative analytics charts. |
| TopoJSON Client | 3.1.0 | ISC | `frontend/js/vendor/topojson-client.min.js` | Decodes TopoJSON geography for D3 visualizations. |
| SheetJS Community Edition | 0.20.3 | Apache-2.0 | `frontend/js/vendor/xlsx.full.min.js` | Reads and writes Excel workbooks for imports, exports, and canvas files. |
| html2canvas | 1.4.1 | MIT | `frontend/js/vendor/html2canvas.min.js` | Captures rendered canvas widgets as images. |
| Lucide | 0.468.0 | ISC | `frontend/js/vendor/lucide.min.js` | Provides icons inside interactive chat visualizations. |
| KaTeX | 0.16.9 | MIT | `frontend/js/vendor/katex/`, `frontend/css/katex/` | Renders mathematical notation, including its bundled web fonts. |
| Inter | 4.001 (git-9221beed3) | OFL-1.1 | `frontend/assets/fonts/` | Provides the locally hosted variable UI font in roman and italic styles. |

The manifest also records each component's upstream source and license URLs, locally stored license files where present, public served paths, code references, and a deterministic SHA-256 tree hash. Directory entries deliberately cover all files below them; this keeps large distributions such as Prism language grammars and KaTeX fonts readable while still hashing every individual file.

## Coverage and verification

Run:

```bash
python3 dev_scripts/verify_frontend_vendor_assets.py
```

The verifier fails when:

- a file in an inventory root is not assigned to a component;
- two components claim the same file;
- a component path escapes the repository or lies outside the inventory roots;
- component content or file names no longer match the recorded tree hash;
- a local license file is missing or is not included in its component;
- required metadata, an upstream HTTPS URL, or an application reference is invalid.

For an intentional vendor update, inspect newly calculated hashes and file counts with:

```bash
python3 dev_scripts/verify_frontend_vendor_assets.py --print-tree-hashes
```

The source inventory roots are `frontend/js/vendor/`, `frontend/css/vendor/`, `frontend/css/katex/`, `frontend/css/prism/`, and `frontend/assets/fonts/`. Files under `frontend_dist/` are generated, cache-busted mirrors of source frontend files and are not independent third-party copies; their provenance remains attached to the corresponding source component above.

## External runtime services

The manifest inventories redistributed third-party files. It does not describe remote services that Omlorix links to or embeds without redistributing their software. For example, `frontend/js/chat/youtubeEmbed.js` embeds privacy-enhanced YouTube content from `youtube-nocookie.com`.
