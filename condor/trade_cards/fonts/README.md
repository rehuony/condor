# Trade card fonts

These unmodified upstream fonts ship with Condor so card rendering uses the same
typefaces on macOS, Linux, and Windows. `chart.py` loads them relative to its own
package directory; no system font installation, working-directory assumption,
or runtime download is required.

- `LibreBaskerville-Regular.ttf`: Latin headings and prices, from
  [Libre Baskerville](https://github.com/impallari/Libre-Baskerville) commit
  `d20160cfa0ac4c532327f85b3ca4054acf92ed38`, upstream path
  `fonts/ttf/LibreBaskerville-Regular.ttf`.
  License: [LibreBaskerville-OFL.txt](LibreBaskerville-OFL.txt), copied from the
  same revision's `OFL.txt`.
- `SourceHanSansCN-Regular.otf`: Chinese labels and supporting text, from
  [Source Han Sans](https://github.com/adobe-fonts/source-han-sans) release
  `2.005R`, commit `6c709ca72d3d7c46ab42ebecc1a26e7d69595a37`, upstream path
  `SubsetOTF/CN/SourceHanSansCN-Regular.otf` (Adobe's Simplified Chinese regional
  subset).
  License: [SourceHanSans-LICENSE.txt](SourceHanSans-LICENSE.txt), copied from
  the same revision's `LICENSE.txt`.

License text is stored as UTF-8 with LF line endings and no trailing whitespace.
The font binaries are unchanged.

Both fonts are distributed under the SIL Open Font License 1.1. Retain their
copyright notices and complete licenses when redistributing the package. Do
not replace them with proprietary system fonts copied from a developer's machine.

SHA-256 of the vendored files:

```text
df9fddf43dbd7de435c316b86a52b3d6b3ad2f6fb2ed3f6fd8bdc1835f30eec1  LibreBaskerville-Regular.ttf
e4f122b61d0d33efd3ffba016a65db2ec5d5d3a16d9047e94d6779a703ce7b66  LibreBaskerville-OFL.txt
e2bc8a2e7f37474b774fff8db758681ece40bb6947a90d571bce9dd60671a8e4  SourceHanSansCN-Regular.otf
fcac737e761ec63dbfbdce11030a1780161920d80315edba9c8beff1c2bac5a2  SourceHanSans-LICENSE.txt
```

Keep files readable and non-executable (`0644`) and the directory traversable
(`0755`). Include the fonts, licenses, and this notice in package data. Git
attributes mark the font binaries as binary to preserve their bytes.
