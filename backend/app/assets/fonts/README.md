# Bundled Canvas PDF fonts

These are unmodified Noto variable TrueType fonts from the official Google
Fonts repository. They make Canvas PDF output deterministic and
self-contained without embedding proprietary operating-system fonts.

| Local file | Official Google Fonts source | SHA-256 |
| --- | --- | --- |
| `NotoSans-wdth-wght.ttf` | `ofl/notosans/NotoSans[wdth,wght].ttf` | `bfb7bb691513f12e734dc346c03a03f784912432d7e3fa8e56efcf906fe86b3d` |
| `NotoSans-Italic-wdth-wght.ttf` | `ofl/notosans/NotoSans-Italic[wdth,wght].ttf` | `58e6e0ebd1931b29a365aa2d3e2ee9a9e831a3af7cf3ad1462d4e72154f0b291` |
| `NotoSansArabic-wdth-wght.ttf` | `ofl/notosansarabic/NotoSansArabic[wdth,wght].ttf` | `63111b5b2e074dd48cc67692e0a2726d86ee94c1c37fe8598257b7b4e87e869e` |
| `NotoSansDevanagari-wdth-wght.ttf` | `ofl/notosansdevanagari/NotoSansDevanagari[wdth,wght].ttf` | `9ce7b04f60e363d8870e5997744cf85cf69d38a4d7d129d364d92a3b14b461d7` |
| `NotoSansSC-wght.ttf` | `ofl/notosanssc/NotoSansSC[wght].ttf` | `a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da` |

Upstream: <https://github.com/google/fonts/tree/main/ofl>. Noto Sans SC is
version 2.004. Every file is licensed under the SIL Open Font License 1.1; the
consolidated upstream copyright statements and verbatim OFL 1.1 license text
are in `OFL.txt` beside the fonts.

The renderer embeds only the glyph subsets needed by each generated document.
The bundled font files themselves are not modified.
