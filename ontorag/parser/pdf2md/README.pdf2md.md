# pdf2md — Structure-Aware PDF / EPUB / DOCX → Markdown

A converter that reads PDFs at the span level (font, size, weight, position), decides what kind of document it is holding, infers structure from evidence rather than regex guesswork, and emits clean Markdown with YAML metadata, a grouped table of contents, and every content type rendered appropriately.

Reads PDF, EPUB, DOCX, and — through LibreOffice — DOC, ODT and RTF. Validated on eight PDFs of four genres and three production pipelines:

| Document | Type | Pages | Text layer | Sections | Tables | Figures |
|---|---|---|---|---|---|---|
| Sutton & Barto, *Reinforcement Learning* 2e | book | 548 | LaTeX, authored | 17 ch · 183 numbered | 5 | 152 |
| Banfield, *Design Leadership* | book | 333 | OCRmyPDF (AppleOCR) | 8 ch · 44 unnumbered | 0 | 0 |
| Ortony, Clore & Collins, *Cognitive Structure of Emotions* 2e | book | 575 | OCRmyPDF (AppleOCR) | 10 ch · 53 unnumbered | 0 | 22 |
| Ammar et al. 2018 (NAACL) | paper | 8 | LaTeX, two-column | 6 + 6 sub | 3 | 0 |
| Futoma et al. 2017 (arXiv / MLHC) | paper | 12 | LaTeX, arXiv sidebar | 5 + 16 sub | 0 | 1 |
| Wang & Neumann 2008 (workshop) | paper | 5 | Word → Distiller | 5 + 2 sub | 1 | 0 |
| Q3 growth review (generated) | deck | 12 | LibreOffice Impress | 4 dividers · 8 slides | 0 | 0 |
| Spam-detection PRD (generated) | document | 2 | LibreOffice Writer | 8 + 4 sub | 1 | 0 |

The zeros are correct: the OCR'd books have no typeset tables and Design Leadership's page images are pre-cropped so there is no running-head furniture; the arXiv paper and the deck have no tables.

A second, unseen sweep of 17 documents from library test corpora and generators — a Japanese academic slide deck, a NAACL paper, the DMCA summary, a Federal Register issue (three columns), a tax form, a Google Docs page, budget tables, a Japanese page, a memo, a whitepaper, a landscape vendor table, a 4:3 deck, and an image-only scan — converts 16 of 17 (the scan is refused by design) with every type call defensible. Ten regression tests cover the eight core documents, the four generated sweep documents, five optional third-party ones, and type-classification margins.

---

## Contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [Document type identification](#document-type-identification)
- [The outline tree](#the-outline-tree)
- [Content types handled](#content-types-handled)
- [Genre-specific paths](#genre-specific-paths)
- [EPUB, DOCX and DOC input](#epub-docx-and-doc-input)
- [Output format](#output-format)
- [CLI reference](#cli-reference)
- [Findings](#findings)
- [Design principles](#design-principles)
- [Robustness sweep](#robustness-sweep)
- [Testing](#testing)
- [Known gaps](#known-gaps)
- [Files](#files)

---

## Why this exists

The predecessor (`txt-cleaner.py`, and its TypeScript port `book-cleaner-cli-ollama`) operated on **already-extracted plain text**. That is the wrong input. Once a PDF has been flattened to a string, the font size, weight, and x/y position that identified an H2 are gone forever, and every subsequent decision is a regex guessing at what the layout used to be. That design could not:

- distinguish a heading from a bold figure caption, or a page number from a table cell
- detect running heads shorter than 25 characters (a `len(text) < 25` guard skipped every `Chapter 1: Introduction`)
- produce any heading hierarchy at all — the output was cleaned prose named `.md`
- tell a table cell from a one-word paragraph, or a two-column paper's left column from its right
- avoid Markdown injection (a body line starting with `#` became an H1)

pdf2md reads the PDF directly with PyMuPDF, so every line carries `(font, size, bold, italic, mono, bbox, direction, math-ratio)`. Structure is decided by evidence, evaluated **globally across the whole document**, within the **genre** the document was identified as, and against a **tree** the headings must fit.

---

## Architecture

```
Stage 0  IDENTIFY   book / paper / deck / document, from four independent
                    signal families, with recorded evidence  (doctype.py)

Stage 1  EXTRACT    span-level lines with full typographic metadata;
                    font-class-aware glyph repair; rotated text kept aside;
                    strict reading order, column-major on two-column pages

Stage 2  PROFILE    global passes over the whole document:
                      body style    = modal (font, size) by character count
                      margin bands  = fixed fractions; repetition discriminates
                      running heads = digit-normalised text repeated in a band
                      heading styles= bold/large styles filtered by family share,
                                      frequency, caption shape, text shape
                      OCR layer?    = one synthetic font -> relative size tiers

Stage 3  CLASSIFY   furniture | heading | caption | code | body | footnote
                    then, by genre: tables -> figure regions -> panel labels ->
                    [book: density demotion, index regime] ->
                    caption wraps -> regimes (front matter, references, glossary,
                    notes, index) -> acronym learning -> de-shouting ->
                    [book: front-matter labelling, copyright tightening] ->
                    footnote anchors

Stage 4  ASSEMBLE   merge split / wrapped headings -> OUTLINE TREE (levels,
                    run-ins, cross-refs, TOC membership from numbering
                    consistency) -> reflow paragraphs across pages and columns
                    -> render each kind -> head (YAML, title block, grouped TOC)
```

Two builds are shipped and are byte-for-byte equivalent in output:

- **`pdf2md_all.py`** — a single file (4,359 lines) merging all seven modules, produced by `build_single.py`, for dropping into a project or a tools directory. Section banners mark where each module begins; the design notes in each module docstring are kept. `python3 pdf2md_all.py in.pdf` is the whole tool.
- **the modular build** below, for development.

| Module | Lines | Role |
|---|---|---|
| `pdf2md.py` | 2,156 | extraction, profiling, classification, tables, figures, assembly, CLI |
| `regimes.py` | 490 | content-type engine: regime state machine, lists, footnotes, references, glossary, de-shouting, front matter, copyright |
| `outline.py` | 201 | heading tree: tokenise → validate numbering → level, kind, TOC membership |
| `doctype.py` | 203 | document-type classifier with evidence |
| `papers.py` | 284 | paper signals, document-level column template, title block, heading promotion |
| `decks.py` | 87 | slide rendering |
| `documents.py` | 66 | PRD / spec / memo title block and metadata fields |
| `structured.py` | ~480 | EPUB / DOCX readers, CSS style ranking, LibreOffice conversion |
| `build_single.py` | 85 | merges the modules into `pdf2md_all.py` |
| `test_books.py` | ~245 | regression suite (eleven tests, ~100 invariants, 16–21 documents) |

Requires `pymupdf`, Python 3.10+.

---

## Document type identification

Every later stage assumes a genre: front-matter labelling assumes a book, column reordering assumes a paper, per-slide rendering assumes a deck. A wrong genre cascades into confident nonsense — a PRD's author table becomes "Publication details", a deck's slide titles become an index. So the type is decided **once, first**, by scoring four independent signal families, and the evidence is recorded. `--profile` prints it; `stats.json` keeps it; `--doc-type` overrides it.

| Family | Signals | Points to |
|---|---|---|
| **Geometry** | exact screen dimensions (960×540, 720×540, 1024×768, 720×405, 1920×1080); landscape ratio 16:9 / 4:3 / 16:10; page count (≤3 → document; ≤25 → document, paper, deck; >60 → book) | deck, book, document |
| **Metadata** | producer / creator: PowerPoint, Keynote, Skia, Impress, beamer → deck; Word, Writer, Docs → document; InDesign, Quark → book; TeX → paper or book | any |
| **Density** | words per page (<100 deck; <250 document; more → paper, book); bullet share >25%; a *display-sized* title at the same position on ≥60% of pages | deck, document |
| **Markers** | `Abstract` on page 1, arXiv id, keywords / related work, two-column pages → paper; ISBN / © / edition, `Chapter N` headings, dot-leader contents, cover page → book; `Agenda`, `Thank you`, `Confidential`, `N / M` → deck; `Product Requirements`, `Revision History`, `Status: Draft`, `Version`, `Author:`, `Reviewers:`, `To: / From:` → document | any |

No family can decide alone. The generated deck and PRD carry the same generic `LibreOffice` producer and are still classified at 89% and 100% from geometry, density and markers; a book with a cover, chapters and an ISBN scores those three independently.

| Fixture | Result | Winner / runner-up |
|---|---|---|
| Sutton & Barto | book | 15 / 4 |
| Design Leadership | book | 11 / 2 |
| Cognitive Structure | book | 13 / 1 |
| Ammar 2018 | paper | 10 / 2 |
| Futoma 2017 | paper | 12 / 2 |
| Wang 2008 | paper | 7 / 1 |
| Growth-review deck | deck | 17 / 2 |
| Spam-detection PRD | document | 10 / 0 |

The suite asserts every type and requires the winner to beat the runner-up by at least 3.

---

## The outline tree

Headings are not classified one at a time. They are tokenised into `(label, number, title)` and walked as a tree whose numbering must be internally consistent — and that consistency is the evidence:

- **Spine.** A number must extend the open path and advance: after `6.2` comes `6.3` (sibling), `6.2.1` (child), or `7` (new chapter). A number that runs backwards — `5.7` appearing after `5.10 Summary` — is a cross-reference label, not a heading, and is returned to body text. Chapter counters (integers) and appendix counters (letters) are separate sequences.
- **Labelled series.** Any word followed by a counter that advances in parallel with the spine and restarts per chapter — `Example 6.1, 6.2, 6.3`; `Figure 6.1, 6.2`; `Characterization 5.1, 5.2`. The word is never looked up; the counter's behaviour identifies it. Members are run-in lead-ins (`**Example 6.2 Random Walk**`), never sections, never in the contents. A "series" of one is just an unnumbered heading.
- **Unnumbered headings** take the depth of the open spine node plus one, and are in the contents only when the document does not number that depth. In the RL book, `Bibliographical and Historical Remarks` sits among `6.1 … 6.9` and is body structure, not skeleton; in the OCR'd trade books nothing is numbered below chapter, so their unnumbered sections *are* the skeleton. If an unnumbered heading's typographic style ranks below every numbered section's, it is a run-in, not a heading.
- **Rendered level** comes from tree depth, not from counting dots.

Per document the tree reports what it found: RL `section 183, labelled 11, unnumbered 31, runin 12, depths [1,2,3]`; Design Leadership `section 8, unnumbered 44, depths [1]`; the ACL paper `section 12, depths [1,2]`. The RL contents went from 231 entries across six depths to 177 across three.

Tokenising is where regex still lives, and where its bugs live: `A simple bandit algorithm` once tokenised as appendix `A`, a string entered the chapter counter, and 161 sections went off-sequence. A bare capital letter is a number only under an explicit `Appendix` or with a dotted child (`A.1`).

---

## Content types handled

A book is a sequence of *regimes* — front matter, body, back matter — each with its own block vocabulary. The same physical shape (a short isolated line) is a heading in the body, an entry in an index, a term in a glossary, an author on a title page. Regime is decided by level-1 headings and page signatures; block type is decided within the regime.

| Type | Detected by | Rendered as |
|---|---|---|
| **Title / cover page** | ≤14 short lines, ≥2 display-sized, no prose (shape, not size: LaTeX titles are 1.4× body, trade covers 5×) | canonical `# Title` + subtitle + `**authors**` + imprint line, once |
| **Praise page** | "Praise for…" opener | `> quote` / `> — attribution` |
| **Copyright page** | ≥3 of ©, ISBN, LCCN, "rights reserved", "published by" | `## Publication details` — keeps ©, ISBN, DOI, edition/printing history, publisher; drops addresses, CIP dumps, legal text |
| **Dedication** | <220 chars, ≤5 lines, "To…" / "In memory of…" | `> *text*` |
| **Jacket copy / author bios** | front pages before Contents; `NAME is Professor…` shape | `## About this book` / `## About the authors` |
| **Contents page** | dot-leader density (ratio ≥0.25 or ≥6 leader lines) or a `Contents` heading regime for leaderless layouts | consumed; replaced by the generated TOC; entries harvested as heading-level truth |
| **List of Figures / Tables** | `Figures` / `Tables` heading regime | `## List of Figures` with `- ` entries |
| **Preface, Foreword, Notation, Abstract** | keyword headings | `#` sections, grouped under **Front matter** |
| **Part / Chapter** | style ranking + `Chapter N` pattern; wrapped titles merged on the page and across a page break | `#` |
| **Section / subsection** | style ranking validated by text shape and isolation; a title beside an accepted section number gets a lenient gate (`TD(λ)`); level from the outline tree | `##` / `###` |
| **Run-in heading** | labelled series in the tree; or unnumbered with a style below the section floor | `**bold**` lead-in, never in the TOC |
| **Body** | default | paragraphs reflowed across pages and columns, de-hyphenated |
| **Lists** | bullet glyph; or `-` / `*` **with a sibling within two lines** (OCR renders quote em-dashes as `-`) | `- item`, wrapped lines rejoined; indentation = nesting on slides |
| **Epigraph / quote** | short block + `— Author` at chapter start | `> quote` / `> — author` |
| **Footnote** | 0.70–0.93× body size, lower page, leading marker; continuations joined | `[^n]: text` at section end; in-text `word.1` → `word[^1]` when n exists on that page and the digit is not part of a decimal |
| **Caption** | `Figure N.N` + a capitalised word (prose "Figure 2.3 illustrates…" excluded); wraps folded to three lines | `> **caption**` |
| **Figure text** | <0.72× body, off-margin, ≥3 lines in a region | image crop (`--figure-dir`) + deduplicated label list; optional VLM transcription |
| **Panel labels** | `a) … b) … c) …` on one baseline, interleaved with panel titles | one italic line joined with `·` |
| **Table** | ≥3 rows at regular pitch, short cells on ≥3 shared columns (clustered on **both** edges, chosen greedily by coverage); cells ≥0.55× body; fill ≥0.7; <25% math cells; wordy or word-headed; per column on two-column pages | Markdown pipe table |
| **Code** | monospace font | fenced block |
| **References** | `Surname, I.` / `(Year)` / `[12]` / `12.` at the column margin; hanging-indent continuations joined | `- entry` |
| **Glossary** | short term + definition (implemented, no fixture) | `- **Term**: definition` |
| **Notes / endnotes** | numbered entries after `Notes` (implemented, no fixture) | `1. text` |
| **Index** | regime after `Index`; adaptive — hanging-indent mode or isolation mode, chosen per index; ends at the next chapter, About the Author, Colophon | `- entry`, `**A**` dividers |
| **Running heads / folios** | margin band + repetition, no minimum length | dropped |
| **Rotated text** | line direction vector | out of prose; the arXiv id harvested from it |

`--profile` prints a census of these kinds for any document.

---

## Genre-specific paths

**Book** runs everything above.

**Columns** (papers, and anything else typeset in columns) are a property of the document's *template*, not of a page. They are found once: a histogram of the left edges of narrow lines over the whole document has one peak per column — 72 and 307 for an ACL paper; 45, 222 and 399 for the Federal Register. A peak inside the previous column's extent (a table's numeric column) is not a column. Boundaries sit in the **gutters** — between a column's right edge and the next start — never at the midpoint of the starts, which the left column's own text would cross. Columns must be *filled* (text runs at least halfway to the gutter), which is what keeps a key/value table from posing as two columns. Per page, a peak populated by enough of that page's lines is active. Two and three columns are handled.

**Paper** — nearly every book assumption inverts:

| Book assumption | Paper reality | What changed |
|---|---|---|
| One column; sort by y | Two or three columns; y-sort interleaves them | Column-major order from the document-level column template (above); full-width or gutter-straddling lines split the page into bands; within a band each column is read top to bottom, left to right. Each line carries `col`, `col_left`. |
| A title page exists | The title block is the top of page 1 | Title = largest contiguous lines; then emails, affiliations (University / Institute / city-state-zip), authors with superscripts and glued digits stripped, Unicode-aware. `Abstract` becomes a level-1 heading; inline `Abstract. text…` is split. |
| Bold is `BX` / `Bold` | Times/Nimbus bold is `-Medi`, `-Demi`, `-B` | Bold regex extended. |
| Headings are rare | 20 heading lines on 8 pages | Frequency cap gets a floor of 40. |
| `2.1 Title` on one line | `1. Introduction`; or `2.1` and `Node Types` as separate spans in a style captions also use | Numbered regex allows the period; a bare number at the column margin with a short bold title on its baseline is promoted regardless of style. |
| Indent = `x0 > page margin` | The right column is always "indented" | Every indent, list, and reference test uses `col_left`; a column change behaves like a page change for continuation. |
| Nothing is rotated | The arXiv id runs sideways | Non-horizontal lines are kept aside, not dropped; the id goes to YAML. |
| Tables are page-wide | The other column's prose shares every baseline | Table detection per `(page, column)`. |
| Columns are left-aligned | Label columns are right-aligned | Cluster on both edges; greedy selection by coverage. |
| A table is mostly words | A results table is mostly numbers | Accept when the header row is words. |

**Deck** — each page is a slide. Page 1 with one or two display lines → document title and subtitle. A page with ≤3 lines, all display-sized against the *median slide-title size* → section divider (`#`). Otherwise the largest line → slide title (`##`); glyph bullets and short lines → list items, with indentation as nesting; long lines → paragraphs; `---` between slides (Marp / reveal-compatible); `Thank you` / `Questions?` slides dropped. No regimes, no outline tree. Speaker notes are not present in exported PDFs.

**Document** (PRD, spec, memo) — page-1 title block: largest lines → title; `Key: value` lines and two-cell table rows (Author, Status, Version, Date, Reviewers, Owner…) → YAML fields; the first remaining short line → subtitle. Sections go through the outline tree. No book regimes.

---

## EPUB, DOCX and DOC input

These formats carry real structure — heading levels, list nesting, tables, footnotes, metadata — so nothing is inferred from geometry. `structured.py` turns the native markup into typed blocks, the **same outline tree** validates numbering and TOC membership, and the renderer emits the same YAML / title block / grouped contents / body layout as the PDF path. Only the standard library is used (`zipfile`, `xml.etree`, `html.parser`).

| Format | Read from | Notes |
|---|---|---|
| **EPUB 3** | `container.xml` → OPF metadata, manifest, spine → each XHTML in spine order; `nav.xhtml` for contents truth | `dc:title / creator / publisher / date / identifier(ISBN) / language`; `<h1>–<h6>`, lists with nesting, tables, `<blockquote>`, `<pre>`, `<figure>/<img>`, footnote `<aside epub:type="footnote">` and `role="doc-noteref"` references; back-links suppressed; **note ids namespaced per chapter** (pandoc restarts at `fn1` in every file) |
| **EPUB 2** | same, with `toc.ncx` for contents | LibreOffice and Calibre exports often have **no heading tags at all** — every block is `<p class="paraN">` and hierarchy lives in the stylesheet. The reader parses the CSS (`font-size`, `font-weight`), ranks paragraph styles by size — the PDF style-ranking idea in CSS clothing — and promotes a tier larger, or bold-and-short, paragraphs to headings. Body size defaults to 12pt when most paragraphs carry no size (their class sets only margins). A spine file with no heading takes its title from the contents, unless that title is a generic `Section N`. |
| **DOCX** | `word/document.xml`, `styles.xml` (outline levels and `Heading N` names through `basedOn` chains), `footnotes.xml` / `endnotes.xml`, `document.xml.rels` for images, `docProps/core.xml` | Title / Subtitle / Quote / Caption / List / Code styles; numbering → list nesting; short all-bold unstyled paragraphs → run-in headings; a Word-generated contents control is dropped and regenerated; a first table of `Key | Value` pairs (Author, Status, Version, Reviewers…) becomes YAML fields; generator defaults (`creator: python-docx`, the 2013 template date, `Unknown Title`) are rejected |
| **DOC / ODT / RTF** | `soffice --headless --convert-to docx`, then as DOCX | `--soffice PATH` if LibreOffice is not on `PATH`; times out at 180 s with a clear message |

Type classification for structured input is by content rather than geometry: an `Abstract` heading near the top → paper; two `Chapter N` headings, an ISBN, ≥8,000 words, or a contents of ≥8 entries → book; otherwise document. `--doc-type` overrides.

Fixtures: the PRD as `.docx` and `.doc`, a whitepaper exported by LibreOffice as EPUB 2 (CSS-only headings), and a three-chapter mini-book built with pandoc as EPUB 3 (nav, footnotes, table, list, quote, ISBN). The suite asserts, among other things, that the LibreOffice EPUB recovers `1.1 Constraints` from a 13pt bold span, that the mini-book's two chapter-local `fn1` notes become `[^2-fn1]` and `[^3-fn1]`, and that pandoc's title page is not echoed into the body.

**Prefer the EPUB or DOCX when you have one.** A PDF of the same book needs every inference in this document; the EPUB needs none of them.

---

## Output format

```markdown
---
title: The Cognitive Structure of Emotions
edition: Second Edition
authors:
  - Andrew Ortony
  - Gerald Clore
  - Allan Collins
publisher: Cambridge University Press
year: 2022
isbn:
  - 9781108844246
  - 9781108928755
  - 9781108934053
pages: 575
source: The_Cognitive_Structure_of_Emotions.pdf
generator: pdf2md
---

# The Cognitive Structure of Emotions
**Andrew Ortony, Gerald Clore and Allan Collins**
*Second Edition · Cambridge University Press, 2022*

## Contents

- **Front matter**
  - [Preface to the Second Edition](#preface-to-the-second-edition)
- [Chapter 1: Introduction](#chapter-1-introduction)
  - [The Study of Emotion](#the-study-of-emotion)
- …
- **Back matter**
  - [References](#references)
  - [Subject Index](#subject-index)

---
```

Papers add `type: paper`, `affiliations`, `arxiv`, `venue`; decks add `type: deck`, `subtitle`, `slides`; documents add `type: document` and whatever fields the title block carried (`status`, `version`, `reviewers`…).

Metadata provenance: **book authors come from the `©` line** (copyright holders are reliable; cover text kept yielding subtitle fragments), with initials handled; paper authors come from the title block; document authors from the `Author` field. ISBNs are found labelled or bare, including inside CIP records. The newest `©` year wins. Publisher is stripped of street addresses. The title block is a fixed layout — title, subtitle, **authors**, imprint — regardless of the order the cover printed them in. TOC indentation is by rank among levels used; duplicate headings get GitHub-style `#summary-1` anchors.

---

## CLI reference

```
python3 pdf2md.py in.pdf                          # -> in.md, type auto-detected
python3 pdf2md.py book.epub                       # EPUB 2 or 3
python3 pdf2md.py spec.docx                       # Word
python3 pdf2md.py old.doc --soffice /opt/libreoffice/program/soffice   # DOC / ODT / RTF via LibreOffice
python3 pdf2md.py in.pdf -o out.md
python3 pdf2md.py in.pdf --profile                # type + evidence, detection report, census
python3 pdf2md.py in.pdf --doc-type deck          # override the classifier
python3 pdf2md.py in.pdf --glyph-report           # unmapped non-ASCII with context
python3 pdf2md.py in.pdf --pages 44-120           # subset (1-based, inclusive)
python3 pdf2md.py in.pdf --artifacts DIR          # profile.json, stats.json, blocks.jsonl, pages/
python3 pdf2md.py in.pdf --emit-json blocks.json  # typed blocks for RAG chunking
python3 pdf2md.py in.pdf --figure-dir figs        # crop figures to PNG and link them
python3 pdf2md.py in.pdf --figure-dir figs --figure-vlm qwen2.5vl:7b
python3 pdf2md.py in.pdf --body-only              # chapters only
python3 pdf2md.py in.pdf --title T --author A --author B
python3 pdf2md.py in.pdf --no-toc
python3 pdf2md.py in.pdf --math-delims            # wrap math-heavy lines in $$
```

The filename convention `<author>#<title>[#index].pdf` is recognised for metadata. `--artifacts` writes what every stage decided — `blocks.jsonl` has every line with kind, regime, level, geometry and text — which is how to debug: read the decision log, don't add prints.

---

## Findings

What each document taught, in the order it was learned. Each is a rule in the code.

### Sutton & Barto (LaTeX, authored text layer)

1. **Section headings are bold at body size** (CMBX10 @ 10pt). No size threshold finds them; detection is by `(font, size)` style with boldness read from the font name.
2. **Section numbers and titles are separate spans on one baseline** (`2.1` / `A k-armed Bandit Problem`); reading order between them is not guaranteed. Sort by `(page, y-band, x)` before merging.
3. **Figure captions are bold too**, and text baked into vector figures (Helvetica, Times, Symbol) is large and bold. Heading candidates are restricted to font families carrying ≥2% of the document's characters; a style whose lines mostly start with `Figure N:` is a caption style.
4. **Spans are joined with no space character.** TeX writes each font run as its own span; `"".join()` welds words (`First-visitMCprediction`). The space is reinstated from the geometric gap between span boxes.
5. **Computer Modern has no ToUnicode map for many slots.** `↵` is `ff` in a text font but **α** in a math font; `⇡` is π; `✓` is θ. The glyph map is keyed by font class. CMEX10 decodes to arbitrary ASCII including `#`, which then renders as an H1: CMEX spans are dropped and **all body text is Markdown-escaped**.
6. **Bold math labels share the heading style exactly** (`xt`, `w3`, `w>x`). A text-shape gate rejects them: real headings are words, ≥90% prose characters, no operators; a one-word heading is Capitalised or ALL CAPS.
7. **The printed Contents page is ground truth when the PDF has no bookmarks** (none of the eight had any). Dot-leader pages are detected, parsed, and consumed; the old cleaner deleted them.
8. **Tables extract as one-word paragraphs.** Detection is geometric; geometry alone also matches multi-line equations and pseudocode, which content gates reject; diagram labels also align in columns but at ~0.3× body, where real tables are ≥0.6×.
9. **A contents page is a skeleton, not an index of bold lines.** Fifty run-in labels and fifteen `Bibliographical and Historical Remarks` were reaching the TOC — the problem the outline tree was built for.
10. **Density demotion was counting the wrong things.** Bold cross-reference numbers in one chapter's remarks pushed the next chapter's opener over the limit and demoted its title. Bare numbers and labelled series no longer count; the line under `Chapter N` is protected.

### Design Leadership (OCRmyPDF, glyphless layer)

11. **An OCR layer has one synthetic font.** Family, boldness, style identity are gone. Size survives as a noisy analog: body text smears across 12.7–14.8pt, producing 30 bogus heading levels if treated as discrete styles.
12. **Two boundaries, two methods.** Body-vs-heading is *anchored* at 1.15× the body median (jitter fills that valley); heading-vs-chapter-title is found by *detecting the gap*. Counts per line, not per character.
13. **Isolation is independent evidence.** Size jitter promotes random body lines; a real heading has whitespace above it.
14. **Wrapped headings fail the isolation test.** Continuation — same style, same page, directly below an accepted heading — is evidence too.
15. **Zero furniture can be correct.** These page images are pre-cropped; fixed-margin assumptions would have eaten body text.
16. **Headings are sparse.** Index entries with 40pt gaps passed isolation and OCR sized 88 of them above threshold. Density demotion caught 61; the index regime caught the rest.
17. **A hyphen bullet needs a sibling.** OCR renders the em dash of `— I think…` as `- I think`. Real lists have neighbours.
18. **This ebook has no printed Contents page** (the EPUB→PDF conversion dropped it); its 44 sections are what the size tiers find.

### Cognitive Structure of Emotions (OCRmyPDF, glyphless layer)

19. **Headings wrap across page breaks.** A heading ending on a dangling function word, followed by a same-style heading at the top of the next page, is one heading.
20. **Diagram text is a word salad.** No text method rebuilds a tree the OCR walked row by row. Recognise the region, keep it out of prose, crop the pixels, keep the labels searchable.
21. **A leaderless Contents page reflows into sentences.** A `contents` regime consumes it.
22. **De-shouting must protect acronyms, learned from the book** — any all-caps token recurring three times inside mixed-case sentences (OCC, EMA, APS here; TD, MDP, GPI in RL); small words excluded; `RNNS` → `RNNs`.
23. **Two index typographies.** Hanging indent vs. extra space above each entry; the script samples the index's x-distribution and picks the rule (77/799 → 483/393 entries/continuations).
24. **Name regexes match sentence boundaries** (`Virginia. He is…`) and, once fixed, initials (`S. Sutton`). Name tokens may not carry a trailing period unless they are an initial.
25. **Prose cross-references look like captions.** A caption requires a capitalised word after the number.

### Research papers (three, different pipelines)

26. **Bold has many spellings** — `-Medi`, `-BoldMT`, `-Demi`, `,B`. The ACL paper had zero headings until the regex learned them.
27. **The abstract is indented, so it lies to a column detector** anchored on the mode of left edges. Ask whether a populous group sits at the margin instead.
28. **A centred figure's labels look like a left column** on a single-column page. Anchor the left column to the document-wide margin.
29. **The rotated arXiv identifier is furniture and metadata at once.** Keep rotated text aside and pass it through the profile — `papers.py` importing `pdf2md` while `pdf2md.py` runs as `__main__` created a second, empty module.
30. **Right-aligned label columns** split into three left-edge clusters. Cluster both edges; choose greedily by coverage.
31. **A results table is mostly numbers.** The gate that rejected equation blocks also rejected `Precision / Recall / F1`; the header row being words is the discriminator.
32. **Footnote anchoring vs. decimals**: `89.3` with footnote 3 on the page became `89.[^3]`.
33. **Papers set captions and subsections in the same bold face**, so the subsection style became a caption style and `2.1 Node Types` vanished. The geometry — bare number at the margin, short bold title on its baseline — identifies it without the style.

### Structure and genre

34. **Regex lists don't scale; the tree does.** A run-in word list, a density hack, and a lenient exception were three patches for one missing idea: the outline is a tree whose numbering must be consistent.
35. **A title beside its accepted number needs a lenient gate.** `Optimality of TD(0)` scored 0.895 against a 0.90 cut; `TD(λ)` failed on the λ.
36. **Genre must be decided first.** `is_paper` had been deciding paper-vs-book *inside* classification, after book-only passes had already run.
37. **"Largest line in the same place on every page" is a deck signal only when that line is display-sized.** On a book's body pages the largest line is the first body line, always at the top.
38. **Decks have no reliable body size.** Sparse slides let the title style dominate character count; section dividers are judged against the median slide-title size.
39. **The producer string is a bonus, not a basis.** LibreOffice stamps decks and documents identically.

### Robustness sweep (17 unseen documents)

40. **`Skia/PDF` renders Google Docs and Google Slides alike.** A one-word Docs page scored `deck +4` on its producer. Only `slides` in the string is a deck signal.
41. **Checkbox glyphs are bullets too.** A Japanese deck's `□` bullets scored eight "form markers" and nearly flipped it to document. Glyphs count once; form vocabulary counts.
42. **Sparse words on one page is not deck evidence.** A landscape vendor table tied deck 4 – document 4 and lost on insertion order. The words-per-page signal needs three pages; ties break document > paper > book > deck.
43. **A template can have two title placements.** Section slides and content slides put the title at different heights; the top two placements together covering 70% of pages is the template.
44. **Columns are a document template, not a page property.** Per-page chain-merging of left edges collapsed two columns into one on any page with a wide table (cells fill the gap in ≤25pt steps). A document-level histogram has one peak per column and is robust to any single page.
45. **Boundaries belong in the gutter.** The midpoint between column *starts* is inside the left column's text, so every left-column line "crossed" it and became spanning. Gutters lie between a column's right edge and the next start.
46. **A cluster inside a column's extent is not a column.** A results table's numeric column formed a third "column" until starts were required to lie beyond the previous column's right edge.
47. **A key/value table is not two columns.** Columns must be *filled* — text running at least halfway to the gutter. A PRD's `Author / Jin` table has two short columns and fails that.
48. **A document's title block must stop at the first table.** With no heading below the title, "everything above the first heading" was the whole page, and a vendor table became metadata fields.
49. **My own grep lied twice.** `awk '/^  - /'` prints affiliations as well as authors; a check that matches on a crash's stale output is not a check. Read the artifact, not the terminal.

### EPUB, DOCX and DOC (four generated fixtures)

50. **LibreOffice's EPUB export has no heading tags.** Every block is `<p class="paraN">`; the hierarchy is entirely in the stylesheet. Parsing the CSS and ranking paragraph styles by size is the PDF style-ranking idea again, and it recovers sections and subsections the nav never mentions.
51. **Body paragraphs may carry no CSS size at all** — their class sets only margins — so the modal size of *sized* paragraphs is the size of the decorated ones, not the body. When most paragraphs are unsized, the body is the browser default.
52. **pandoc numbers footnotes per chapter file.** Both chapters had `fn1`; note ids are namespaced by spine position. Its back-link anchors (`class="footnote-back"`) contain the word "footnote" and were read as references until back-links were handled first and their text suppressed.
53. **A generator's defaults are not metadata.** python-docx writes `creator: python-docx` and a 2013 template date; LibreOffice writes `dc:title: Unknown Title`. These are rejected, and a `Key | Value` table in the document (`Author | Jin`) fills the gap.
54. **A generated title page echoes the metadata into the body.** Headings and paragraphs equal to the title, an author, the publisher or the year are consumed.
55. **The largest first line is the title** regardless of genre — the same rule the PDF path uses for papers — which is what let the CSS-only EPUB name itself.

### Process

56. **A silent failure is worse than a loud one.** For two rounds, edits appeared to do nothing: a rewrite had swallowed `render_table`, the converter crashed on assembly, and `2>&1 >/dev/null` hid it while stale outputs were grepped. The suite exists because of this.
57. **Every change runs every fixture.** A fix for the OCR books broke the LaTeX book's chapter titles; a fix for one book's authors dropped another's; a tokenizer bug in the tree went unnoticed on five documents and collapsed the sixth. The heterogeneous corpus is the regression suite.
58. **Metadata from the book beats metadata from the cover.** Copyright holders are authors; cover text is subtitle fragments, brand marks and cities.

---

## Design principles

**Decide the genre first, with evidence.** Everything downstream assumes one. Score independent families, record why, allow override.

**Structure is a tree, and the tree is evidence.** Numbering consistency, sibling uniformity, and parent–child extension decide levels and TOC membership. Before reaching for a word list, ask what the surrounding hierarchy already implies.

**Evidence, then shape, then position, then density.** A line is a heading if its *style* is a heading style, its *text* looks like words, it has *air* above it, and it is not one of many. Each test was added because the previous ones let a specific failure through. Keep them all.

**Global before local.** Per-page heuristics cannot tell a running head from a real heading; "appears at y=39 on 400 pages" is unambiguous. Body size, margin bands, running heads, heading styles, acronyms, index typography, line pitch, column margins are all computed once over the whole document.

**Regimes.** A book is not one distribution of lines. The same shape means different things in front matter, body, and back matter.

**Never emit Markdown from unescaped text.** Extraction artefacts at line starts silently become structure.

**Don't chase OCR errors in the parser.** `Andrew Oriony`, `INDESIRARI E` are the OCR engine's mistakes; correcting them is a language-model job on the finished text. The parser's job is to put such text in the right *block*.

**Deterministic first, model last.** Everything here is deterministic and reproducible. The only model hook is `--figure-vlm`, isolated so a missing Ollama never breaks a conversion.

---

## Robustness sweep

Seventeen documents the converter had never seen, from pdfminer, pdfplumber, camelot, pypdf and olmOCR test corpora plus four generated with LibreOffice:

| Document | Pages | Type | Margin | Notes |
|---|---|---|---|---|
| NLP 2004 slides (Japanese, Quartz) | 31 | deck | 9/3 | two title placements; Wingdings `o` bullets |
| Shinyama & Sekine 2006 (NAACL) | 8 | paper | 9/5 | 12 sections, 4 tables, affiliations separated from authors |
| DMCA summary (US Copyright Office) | 18 | document | 5/2 | legal text; long headings wrap unmerged |
| Federal Register 85/152 (three columns) | 15 | document | 6/4 | reading order correct across three columns |
| IRS Form 1040-NR | 5 | document | 6/4 | form vocabulary (OMB, signature, checkboxes) |
| Google Docs one-word page | 1 | document | 4/1 | `Skia/PDF` is Docs *and* Slides |
| camelot budget / superscript tables | 1 | document | 2/1 | |
| Japanese government page (kampo) | 1 | document | 2/1 | script out of scope; no crash |
| NICS background-check report | 1 | document | 2/1 | large numeric table not detected |
| olmOCR dolma page, pypdf crazyones | 1 | document | | |
| Memo (To / From / Subject) | 1 | document | 10/0 | fields into YAML |
| Whitepaper (numbered sections, table) | 4 | document | 5/2 | `1.1` nested, results table |
| Landscape vendor-comparison table | 1 | document | 4/2 | landscape ≠ deck; table intact |
| 4:3 hardware-review deck | 5 | deck | 19/2 | |
| Image-only scan (pdfminer 175) | 2 | — | — | refused with an OCR-first message |

Six classifier and layout fixes came out of the sweep: `Skia/PDF` removed from deck producers (it renders Google Docs too); form vocabulary added as a document signal, counting checkbox *glyphs* once (they are also bullets); sparse words on a one-page document no longer scores as a deck; a deterministic tie-break (document > paper > book > deck); two title placements (section and content slides) counted as a template; and the document-level column template with gutter boundaries, which is what made three columns work and stopped a PRD's key/value table from reading as two columns.

---

## Testing

```
python3 test_books.py                          # modular build
PDF2MD=pdf2md_all.py python3 test_books.py     # single-file build
```

Runs the eight core fixtures, the four generated sweep documents, and any of the five third-party sweep documents that are present, with `--artifacts`, and asserts ~80 golden invariants:

- converter exits 0 on every fixture (the silent-failure guard)
- document type for all eight, with the winner ≥3 points clear of the runner-up
- YAML front matter, authors present, title precedes TOC, every chapter in the TOC
- exact chapter counts (17 / 8 / 10) and merged titles, including the cross-page wrap
- glyph repair (`different`, `| high | search | high | α | rsearch |`), no injection, no shouting, no dot leaders
- TOC depth ≤3; `Bibliographical…` and run-ins out; `**Example 6.2 Random Walk**` merged and bold in the body
- indexes free of fake headings, rendered as lists; `chasing that dragon` is prose
- front matter labelled; leaderless contents consumed; figure regions; ebook ISBN; diagrams are not tables
- papers: type, exact title, author lists (20th author, Unicode names), Abstract/References, `#`/`##`/`###` from numbering, arXiv id, contiguous two-column reading order, the exact right-aligned table row, subsections from the baseline, no anchors in decimals
- deck: title, dividers at `#`, slide titles at `##`, nested bullets, `---` separators, end slide dropped, no book regimes
- PRD: type, `status` / `version` / `reviewers`, nested `5.x`, revision-history table, no book regimes
- sweep: memo fields, whitepaper `1.1` and table, landscape table row, 4:3 deck slide; Federal Register three-column reading order; NAACL authors and `3.1`; types for all, with the winner ≥3 clear of the runner-up on documents of three or more pages

The suite found real bugs on its first run and on the first tree implementation. Run it after every change.

---

## Known gaps

- **Subsection depth on OCR layers.** Bold-at-body-size subsections are invisible in a glyphless layer; Design Leadership's "About the Author" and colophon therefore land inside the index list.
- **Sparse grids** (tic-tac-toe boards) render as code blocks; they are figures and a drawing-rule check would trigger a crop.
- **Large numeric tables** without a wordy header (the NICS state-by-state report) are not detected.
- **Long legal headings** that wrap (the DMCA summary's `Title I, the “WIPO Copyright…`) are emitted as separate headings.
- **Non-Latin scripts** convert without crashing but the de-shouting, name, and heading-shape rules are Latin-only.
- **Math is passed through, not reconstructed.** Equations survive as approximate Unicode; the ACL paper's math font maps `=` to `D`.
- **Multi-row table headers** render as two header rows.
- **Glossary and endnotes** are implemented against the taxonomy but no fixture exercises them.
- **Language.** Regime keywords, copyright vocabulary, and small-word lists are English-only.
- **Landscape books** (photo books, catalogs, manuals) are not distinguished from decks; the design (image coverage, template repetition, prose test) was worked out but not implemented.
- **Deck fixtures are synthetic.** A real PowerPoint export with charts, SmartArt and two-column layouts will stress the per-slide path further; speaker notes are not in exported PDFs.
- **Image-only PDFs** are refused; run Chandra 2 or Marker first.
- **Structured input coverage** is what four generated fixtures exercise: no real-world EPUB with images, nested lists in footnotes, or a multi-level nav has been run yet; DOCX tracked changes, comments and text boxes are ignored.

---

## Files

```
pdf2md_all.py                                  # single-file build (everything)
pdf2md.py  regimes.py  outline.py  doctype.py  papers.py  decks.py  documents.py  structured.py
build_single.py                                # regenerates pdf2md_all.py from the modules
test_books.py
README.md
sutton-barto-rl2e.md
design-leadership.md
cognitive-structure-of-emotions.md   + figures/*.png
papers/ammar2018-literature-graph.md
papers/futoma2017-mgp-rnn-sepsis.md
papers/wang2008-answer-validation.md
fixtures/deck.pdf  fixtures/deck.md  fixtures/prd.pdf  fixtures/prd.md
fixtures/prd.docx  prd.doc  gen-whitepaper.epub  minibook.epub  (+ .md outputs)
sweep/gen-memo.pdf  gen-whitepaper.pdf  gen-landscape.pdf  gen-deck43.pdf  (+ .md outputs)
```
