# PDF component third-party notices

This notice covers the dependencies and assets used by Omlorix's PDF preview,
PDF-to-image, and Canvas/chat PDF-export paths. Corresponding upstream license
files are retained with the backend assets and installed Python packages.

## pypdfium2 5.13.0 and PDFium

- Component: `pypdfium2` Python bindings and the platform PDFium binary in the
  selected wheel.
- Binding license: BSD-3-Clause or Apache-2.0.
- PDFium license: BSD-style; additional permissive notices apply to libraries
  compiled into each platform binary.
- Documentation, examples, and certain project data: CC-BY-4.0.
- Source: <https://github.com/pypdfium2-team/pypdfium2/tree/5.13.0>

The installed wheel's `.dist-info/licenses/` directory is part of the
redistributable artifact and must not be removed. In particular, retain its
BSD-3-Clause, Apache-2.0, and CC-BY-4.0 license texts and its platform-specific
`BUILD_LICENSES/` directory. The latter contains the exact PDFium,
pdfium-binaries, Abseil, AGG, fast_float, FreeType, ICU, Little CMS,
libjpeg-turbo, OpenJPEG, libpng, libtiff, LLVM libc, simdutf, and zlib notices
for that wheel build. If the PDFium binary is replaced or built locally,
regenerate the notice set from that exact build.

## ReportLab 5.0.1

- Component: PDF document generation and layout.
- License: BSD.
- Source: <https://pypi.org/project/reportlab/5.0.1/>

Retain `reportlab-5.0.1.dist-info/licenses/LICENSE` from the installed wheel.
ReportLab also ships Bitstream Vera fonts; their license remains under
`reportlab/fonts/bitstream-vera-license.txt`, even though Omlorix's Canvas PDF
path uses the bundled Noto fonts below.

## uharfbuzz 0.56.0

- Component: Python binding and bundled HarfBuzz shaping engine used by
  ReportLab for complex scripts.
- Binding license: Apache-2.0.
- Bundled HarfBuzz engine license: Old MIT.
- Source: <https://github.com/harfbuzz/uharfbuzz/tree/v0.56.0>
- HarfBuzz source revision used by the binding: `56feae4`.

Retain `uharfbuzz-0.56.0.dist-info/licenses/LICENSE` from the installed wheel.
The wheel incorporates minimal upstream HarfBuzz sources, so distributions
must also retain Omlorix's verbatim upstream HarfBuzz notice at
[`assets/licenses/HARFBUZZ-COPYING.txt`](assets/licenses/HARFBUZZ-COPYING.txt).
Its source is <https://github.com/harfbuzz/harfbuzz/blob/56feae4/COPYING>.

## Noto fonts

- Components: Noto Sans, Noto Sans Italic, Noto Sans Arabic, Noto Sans
  Devanagari, and Noto Sans SC variable TrueType fonts.
- License: SIL Open Font License 1.1.
- Source and hashes: [`assets/fonts/README.md`](assets/fonts/README.md).
- Verbatim license: [`assets/fonts/OFL.txt`](assets/fonts/OFL.txt).

Generated PDFs contain subsets of these fonts as permitted by the OFL. Do not
sell the font files by themselves, and keep the OFL text with redistributions
of the original font binaries.
