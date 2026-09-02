# Vendored from the author's pdf2md single-file build (pdf2md-files.zip,
# 2026-09-02, 4360 lines). Deliberate edits, both to re-apply when
# re-vendoring: (1) ``main()`` is split into ``main()`` (argparse) +
# ``run(args)`` so ``convert.py`` can drive it as a library; (2) one f-string
# containing a backslash (``attribution`` in the renderer) is hoisted into a
# local, because that syntax needs Python 3.12 and OntoRAG targets 3.10.
# Everything else — including ``print`` and ``sys.exit`` — is
# upstream behaviour and is contained by ``convert.py``.
# ruff: noqa
"""
pdf2md (single-file build) — structure-aware PDF -> Markdown for books,
research papers, slide decks, and standard documents.

This file is the merge of pdf2md.py, regimes.py, outline.py, papers.py,
doctype.py, decks.py, documents.py and structured.py, produced by build_single.py. Section
banners mark where each module begins; each module's design notes are kept.

Usage:  python3 pdf2md_all.py input.pdf [-o out.md] [--profile] [--artifacts DIR]
Requires: pymupdf   (pip install pymupdf)
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import unicodedata
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin
import xml.etree.ElementTree as ET

try:
    import pymupdf
except ImportError:  # pragma: no cover
    sys.exit("pymupdf required:  pip install pymupdf")


# =============================================================================
# regimes.py  —  regimes.py — Content-type engine for pdf2md.
# =============================================================================
"""
regimes.py — Content-type engine for pdf2md.

A book is not one distribution of lines; it is a sequence of REGIMES, each
with its own vocabulary of block types and its own rendering rules:

    front matter  -> title page, praise, copyright, dedication, list of
                     figures/tables, preface (a real section)
    body          -> headings, paragraphs, lists, block quotes/epigraphs,
                     footnotes, captions, figures, code
    back matter   -> references/bibliography, glossary, notes/endnotes,
                     appendix (body again), index

Regime is decided by level-1 headings and by page-level signatures; the block
type of a line is then decided *within* its regime, because the same physical
shape (short isolated line) means "heading" in the body, "entry" in an index,
"term" in a glossary and "author" on a title page.
"""



# ---------------------------------------------------------------------------
# regime triggers
# ---------------------------------------------------------------------------
REGIME_HEADING = [
    (re.compile(r"^(References|Bibliography|Works Cited|Literature Cited|"
                r"Further Reading|Suggested Reading)$", re.I), "references"),
    (re.compile(r"^(Glossary|Glossary of Terms|Definitions)$", re.I), "glossary"),
    (re.compile(r"^(Notes|Endnotes|Chapter Notes)$", re.I), "notes"),
    (re.compile(r"^(Subject Index|Author Index|Name Index|Index)$", re.I), "index"),
    (re.compile(r"^(List of Figures|List of Tables|Figures|Tables|Illustrations)$",
                re.I), "list_of"),
    (re.compile(r"^(Contents|Table of Contents)$", re.I), "contents"),
    (re.compile(r"^(Part|Chapter|Appendix|Appendices)\b", re.I), "body"),
    (re.compile(r"^(Abstract|Preface|Foreword|Introduction|Prologue|Acknowledg(e)?ments|"
                r"Summary of Notation|Notation|About the Authors?|"
                r"About This Book|Epilogue|Afterword|Conclusion)", re.I), "body"),
]

# page-level signatures inside front matter
COPYRIGHT_KEYS = re.compile(
    r"(©|\(c\)\s*\d{4}|copyright|all rights reserved|isbn|library of congress|"
    r"lccn|printed in|published by|first published|first edition|"
    r"\bprinting\b|british library cataloguing|catalog(ue|ing)-in-publication|"
    r"typeset|manufactured in)", re.I)
COPYRIGHT_KEEP = re.compile(
    r"(©|\(c\)\s*\d{4}|copyright|isbn|published by|first published|"
    r"[a-z]+ edition\b|printed in|\bpress\b|publishing|publishers?\b)", re.I)
PRAISE_RE = re.compile(r"^(Praise for|Advance praise|What (people|readers) are saying)",
                       re.I)
DEDICATION_RE = re.compile(
    r"^(To |For |In memory of|In memoriam|Dedicated to|For my|To my|To our)", re.I)
ATTRIBUTION_RE = re.compile(r"^\s*[—–\-]{1,2}\s*[A-Z]")
LIST_BULLET_RE = re.compile(r"^\s*([•▪●◦‣⁃∙·\u2022\u25aa\u25cf\u25e6]|[-*])\s+\S")
LIST_NUM_RE = re.compile(r"^\s*(\d{1,2}|[a-z]|[ivx]{1,4})[.)]\s+\S")
FOOTNOTE_NUM_RE = re.compile(r"^\s*(\d{1,3}|[*†‡§¶])\s*\S")
REF_ENTRY_RE = re.compile(
    r"^(?:\[\d{1,3}\]|\d{1,3}\.)\s+\S|"
    r"^(?:\[\d{1,3}\]\s*)?[A-Z][A-Za-z'’\-]+,\s+[A-Z]\.|^[A-Z][A-Za-z'’\-]+,\s+[A-Z][a-z]+"
    r"|^[A-Z][A-Za-z'’\-]+(\s+(and|&)\s+[A-Z][A-Za-z'’\-]+)?\s*\(\d{4}[a-z]?\)")
NOTE_ENTRY_RE = re.compile(r"^\s*(\d{1,3})[.)]?\s+\S")
IN_TEXT_ANCHOR_RE = re.compile(r"(?<=[A-Za-z.,;:)\]\"”’])(?<!\d[.,])(\d{1,2})(?=\s|$)")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _page_text(lines_on_page):
    return "\n".join(l.text for l in lines_on_page)


def _first_body_heading_index(lines) -> int:
    """Index of the first heading that starts the real book: a Chapter/Part,
    or a Preface/Introduction-style section. Everything before it is front."""
    for i, l in enumerate(lines):
        if l.kind != "heading":
            continue
        t = l.text.strip()
        for pat, reg in REGIME_HEADING:
            if reg == "body" and pat.match(t):
                return i
    return 0


# ---------------------------------------------------------------------------
# main entry
# ---------------------------------------------------------------------------
def assign_regimes(lines, prof) -> Counter:
    """Set `line.regime` for every line and re-type lines whose meaning is
    regime-dependent. Returns a census of block kinds for reporting."""
    body_start = _first_body_heading_index(lines)
    by_page = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)

    # ------------------------------------------------------------ front matter
    front_pages = {l.page for l in lines[:body_start]}
    front_pages -= {lines[body_start].page} if body_start < len(lines) else set()
    for p in sorted(front_pages):
        pl = by_page[p]
        txt = _page_text(pl)
        low = txt.lower()
        # LaTeX title pages are restrained (1.4x body); trade books shout (5x).
        # Anchor "big" to the smaller of 1.5x body and the book's largest
        # heading style, so both are caught.
        big_cut = prof.body_size * 1.3
        big = [l for l in pl if l.size >= big_cut or l.kind == "heading"]
        longest = max(len(l.text) for l in pl)
        chars = len(txt.strip())

        if any(l.kind == "furniture" for l in pl) and all(
                l.kind == "furniture" for l in pl):
            continue                                       # TOC pages: done
        if PRAISE_RE.match(pl[0].text.strip()):
            for l in pl:
                l.regime = "praise"
                if ATTRIBUTION_RE.match(l.text):
                    l.kind = "attribution"
                elif l.kind != "heading":
                    l.kind = "quote"
            continue
        n_keys = len(COPYRIGHT_KEYS.findall(txt))
        if n_keys >= 3 or ("isbn" in low and n_keys >= 2):
            for l in pl:
                l.regime = "copyright"
                l.kind = "copyright_keep" if COPYRIGHT_KEEP.search(l.text) \
                    and len(l.text) < 140 else "copyright_drop"
            continue
        if chars < 220 and len(pl) <= 5 and not big and \
                (DEDICATION_RE.match(pl[0].text.strip()) or len(pl) <= 3):
            for l in pl:
                l.regime, l.kind = "dedication", "dedication"
            continue
        # A title page is defined by shape, not by absolute size: a handful
        # of short lines, several of them display-sized or heading-styled,
        # and no running prose. (LaTeX titles can be smaller than chapter
        # titles; trade covers can be 5x body. Shape is what they share.)
        if len(big) >= 2 and len(pl) <= 14 and longest < 120:
            # cover / half-title / title page: a few very large lines plus
            # author and publisher. Collapse into one metadata block.
            for l in pl:
                l.regime = "title"
                # only the largest display size on the page is the title;
                # author, subtitle and publisher are metadata even when bold
                top = max(x.size for x in pl)
                l.kind = "title_line" if l.size >= top * 0.7 else "title_meta"
            continue
        for l in pl:
            l.regime = "front"

    # ------------------------------------------------------------ regimes by heading
    regime = "front" if body_start else "body"
    in_chapter_start = 0            # lines since last chapter heading
    pending_fn: dict[int, str] = {}
    for i, l in enumerate(lines):
        if i == body_start:
            regime = "body"
        if l.kind == "heading":
            t = l.text.strip()
            for pat, reg in REGIME_HEADING:
                if pat.match(t):
                    if reg in ("references", "glossary", "notes", "index",
                               "list_of", "contents"):
                        l.level = 1 if reg != "list_of" else max(1, l.level)
                    regime = reg if reg != "contents" else "front"
                    in_chapter_start = 0
                    break
            l.regime = regime
            if re.match(r"^(Part|Chapter)\b", t, re.I):
                in_chapter_start = 1
            continue
        if getattr(l, "regime", None) in ("title", "copyright", "dedication", "praise"):
            continue
        l.regime = regime
        if regime == "list_of" and l.kind == "caption":
            l.kind = "lof_entry"
            continue
        if l.kind in ("furniture", "consumed", "figure_text", "caption",
                      "code", "index_entry", "index_cont", "index_letter"):
            continue

        t = l.text.strip()

        # ---------------------------------------------------- references
        if regime == "references":
            if l.kind == "heading":
                l.kind = "ref_entry"          # OCR-lifted entry
            elif REF_ENTRY_RE.match(t) and l.x0 <= prof.body_left + 10:
                l.kind = "ref_entry"
            elif l.x0 > prof.body_left + 6 or not l.isolated:
                l.kind = "ref_cont"           # hanging-indent continuation
            else:
                l.kind = "ref_entry"
            continue

        # ---------------------------------------------------- glossary
        if regime == "glossary":
            is_term = (len(t) < 60 and (l.isolated or l.is_bold)
                       and not TAIL_OK_RE.search(t) and l.x0 <= prof.body_left + 10)
            l.kind = "gloss_term" if is_term else "gloss_def"
            continue

        # ---------------------------------------------------- notes / endnotes
        if regime == "notes":
            l.kind = "note_entry" if NOTE_ENTRY_RE.match(t) else "note_cont"
            continue

        # ---------------------------------------------------- list of figures
        if regime == "list_of":
            l.kind = "lof_entry" if re.match(r"^(Figure|Table|Fig\.)\s*[\dIVX]", t) \
                else "lof_cont"
            continue

        if regime == "index":
            continue                          # handled by mark_index_regime

        # ---------------------------------------------------- body regime
        if l.kind == "heading":
            continue

        # epigraph / block quote at chapter start: short paragraph(s) followed
        # by an attribution line ("— Author")
        if in_chapter_start and 0 < in_chapter_start <= 6:
            in_chapter_start += 1
            nxt = lines[i + 1] if i + 1 < len(lines) else None
            if ATTRIBUTION_RE.match(t):
                l.kind = "attribution"
                # retro-tag the preceding short lines as quote
                j = i - 1
                while j >= 0 and lines[j].kind == "body" and lines[j].page == l.page:
                    lines[j].kind = "quote"; j -= 1
                in_chapter_start = 0
                continue

        # lists. A glyph bullet is unambiguous. A hyphen is not: OCR turns the
        # em dash of a quote attribution ("— I think...") into "- I think",
        # so a hyphen bullet must have a hyphen-bullet sibling within 2 lines.
        if LIST_BULLET_RE.match(t):
            if t[0] in "-*":
                sib = any(LIST_BULLET_RE.match(lines[j].text.strip())
                          and lines[j].text.strip()[0] in "-*"
                          for j in (i - 2, i - 1, i + 1, i + 2)
                          if 0 <= j < len(lines) and j != i and lines[j].page == l.page)
                if not sib:
                    l.kind = "body"
                    l.text = re.sub(r"^\s*-\s+", "\u2014 ", l.text)   # restore the dash
                    continue
            l.kind = "list_item"
            continue
        if LIST_NUM_RE.match(t) and l.x0 >= prof.body_left - 2:
            l.kind = "list_item"
            continue
        prev = lines[i - 1] if i > 0 else None
        if prev is not None and prev.kind in ("list_item", "list_cont") \
                and prev.page == l.page and not l.isolated \
                and l.x0 > prof.body_left + 6:
            l.kind = "list_cont"              # wrapped list item
            continue

        # footnotes (works for OCR layers, which is where the base detector
        # is weakest): smaller than body, in the lower part of the page,
        # starting with a marker. Continuations are same-size lines that follow.
        small = prof.body_size * 0.70 < l.size < prof.body_size * 0.93
        low_on_page = l.y0 > prof.page_h * 0.55
        if small and low_on_page and FOOTNOTE_NUM_RE.match(t) and l.kind == "body":
            l.kind = "footnote"
            m = FOOTNOTE_NUM_RE.match(t)
            l.fn_num = m.group(1)
            continue
        if prev is not None and prev.kind in ("footnote", "footnote_cont") \
                and prev.page == l.page and small and not l.isolated:
            l.kind = "footnote_cont"
            continue

    return Counter(l.kind for l in lines)


def link_footnote_anchors(lines) -> int:
    """Turn the trailing superscript digit an OCR layer leaves glued to a word
    ("methods.1 That is") into a Markdown anchor, but only when a footnote
    with that number exists on the same page. Conservative on purpose."""
    n = 0
    by_page = {}
    for l in lines:
        if l.kind == "footnote" and getattr(l, "fn_num", None):
            by_page.setdefault(l.page, set()).add(l.fn_num)
    for l in lines:
        if l.kind != "body" or l.page not in by_page:
            continue
        nums = by_page[l.page]

        def rep(m):
            nonlocal n
            if m.group(1) in nums:
                n += 1
                return f"[^{m.group(1)}]"
            return m.group(1)
        l.text = IN_TEXT_ANCHOR_RE.sub(rep, l.text)
    return n


TAIL_OK_RE = re.compile(r"[.!?\u2026\"\u201d')\]]\s*$")


# ===========================================================================
# CASE NORMALISATION — de-shouting
# ===========================================================================
SMALL_WORDS = {"a", "an", "the", "and", "or", "but", "of", "for", "to", "in",
               "on", "at", "by", "with", "from", "as", "nor", "vs", "via"}
BUILTIN_ACRONYMS = {"ISBN", "DOI", "LCCN", "LCC", "DDC", "LCSH", "USA", "UK",
                    "NY", "CA", "DC", "MIT", "IEEE", "ACM", "AI", "II", "III",
                    "IV", "VI", "VII", "VIII", "IX", "XI", "XII", "PhD", "MD",
                    "BC", "AD", "TV", "US", "EU", "UN", "CEO", "CTO", "UX",
                    "UI", "API", "OCR", "PDF", "TD", "MC", "DP", "RL", "MDP"}
CAPS_TOKEN_RE = re.compile(r"^[A-Z][A-Z'’\-]*[A-Z]$")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’\-]*")


def learn_acronyms(lines) -> set[str]:
    """Acronyms are all-caps tokens that recur inside otherwise mixed-case
    prose. Learn them from the book itself so 'OCC' and 'EMA' survive
    de-shouting while 'CAMBRIDGE' does not."""
    seen = Counter()
    for l in lines:
        words = WORD_RE.findall(l.text)
        if len(words) < 4:
            continue
        caps = [w for w in words if CAPS_TOKEN_RE.match(w) and 2 <= len(w) <= 6]
        if 0 < len(caps) <= len(words) * 0.5:       # mixed-case line
            seen.update(caps)
    stop = {w.upper() for w in SMALL_WORDS} | {"THE", "THIS", "THAT", "NOT", "ALL",
            "ONE", "TWO", "NEW", "OLD", "END", "SEE", "ARE", "WAS", "HIS", "HER",
            "ITS", "OUR", "YOU", "WHO", "HOW", "WHY", "MAY", "CAN", "OUT", "OFF"}
    return ({w for w, n in seen.items() if n >= 3} - stop) | BUILTIN_ACRONYMS


def _titlecase_word(w: str, first: bool, acronyms: set[str]) -> str:
    if w in acronyms or any(c.isdigit() for c in w):
        return w
    if len(w) > 2 and w[-1] in "sS" and w[:-1] in acronyms:
        return w[:-1] + "s"                      # RNNS -> RNNs
    if re.fullmatch(r"[IVXLCDM]+", w):             # roman numeral
        return w
    low = w.lower()
    if not first and low in SMALL_WORDS:
        return low
    # keep internal hyphen/apostrophe parts capitalised: WELL-BEING -> Well-Being
    return re.sub(r"(^|[-'’])([a-z])", lambda m: m.group(1) + m.group(2).upper(), low)


def smart_case(text: str, acronyms: set[str]) -> str:
    """Convert shouting to readable case.

    * A line whose letters are >=90% upper-case and that has >=2 words
      becomes Title Case: THE COGNITIVE STRUCTURE OF EMOTIONS ->
      The Cognitive Structure of Emotions.
    * A mixed line whose *leading run* is >=2 all-caps words gets only that
      run converted: 'GERALD L. CLORE is Professor' -> 'Gerald L. Clore is
      Professor'. Field labels like 'NAMES:' become 'Names:'.
    Learned acronyms, roman numerals and tokens with digits are untouched.
    """
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 4:
        return text
    upper_ratio = sum(c.isupper() for c in letters) / len(letters)
    tokens = text.split(" ")

    def conv(tok, first):
        core = WORD_RE.search(tok)
        if not core:
            return tok
        w = core.group(0)
        return tok[:core.start()] + _titlecase_word(w, first, acronyms) + tok[core.end():]

    if upper_ratio >= 0.9 and len(WORD_RE.findall(text)) >= 2:
        return " ".join(conv(t, i == 0) for i, t in enumerate(tokens))

    # leading all-caps run
    run = 0
    for t in tokens:
        w = WORD_RE.search(t)
        if w and (CAPS_TOKEN_RE.match(w.group(0)) or re.fullmatch(r"[A-Z]\.?", w.group(0))):
            run += 1
        else:
            break
    if run >= 2 and any(len(WORD_RE.search(t).group(0)) >= 3 for t in tokens[:run]
                        if WORD_RE.search(t)):
        head = [conv(t, i == 0) for i, t in enumerate(tokens[:run])]
        return " ".join(head + tokens[run:])
    return text


# ===========================================================================
# FRONT-MATTER LABELLING — jacket copy, bios, leaderless contents
# ===========================================================================
BIO_START_RE = re.compile(
    r"^(?:[A-Z][A-Za-z'’\-]+\s+|[A-Z]\.\s+){1,5}[A-Z][A-Za-z'’\-]+\s+(is|was|has|teaches|holds|serves)\b")
PAGE_NUM_LINE_RE = re.compile(r"^\s*(\d{1,4}|[ivxlcdm]{1,7})\s*$", re.I)


def label_front_matter(lines, prof, canonical_title_key: str) -> None:
    """Second pass over plain 'front' pages:

    * an all-caps line equal to the book title is a jacket repeat -> drop
    * a paragraph starting 'NAME is Professor of ...' is an author bio
    * everything else on a jacket page is the blurb
    * pages after a 'Contents' heading (and before the next heading) are a
      leaderless contents list: consume them and harvest heading truth
    """
    regime = None
    for i, l in enumerate(lines):
        if l.kind == "heading":
            t = l.text.strip()
            if re.match(r"^(Contents|Table of Contents)$", t, re.I):
                regime = "contents"
                l.kind = "consumed"                   # generated TOC replaces it
                continue
            regime = None
            continue
        if regime == "contents":
            l.regime, l.kind = "contents", "consumed"
            t = l.text.strip()
            if PAGE_NUM_LINE_RE.match(t) or len(t) < 3:
                continue
            t = re.sub(r"\s+\d{1,4}$", "", t)         # trailing page number
            m = re.match(r"^(\d{1,2})\s+(.+)$", t)
            key = re.sub(r"[^a-z0-9]", "", (m.group(2) if m else t).lower())
            if key:
                prof.toc_truth.setdefault(key, 1 if m else 2)
            continue
        if l.regime != "front" or l.kind != "body":
            continue
        key = re.sub(r"[^a-z0-9]", "", l.text.lower())
        if key and key == canonical_title_key:
            l.kind = "consumed"
            continue
        l.kind = "front_bio" if BIO_START_RE.match(l.text.strip()) else "front_blurb"
    # bio continuation: lines after a bio start on the same page inherit it
    prev = None
    for l in lines:
        if l.kind in ("front_bio", "front_blurb") and prev is not None \
                and prev.kind == "front_bio" and prev.page == l.page \
                and not BIO_START_RE.match(l.text.strip()):
            l.kind = "front_bio"
        prev = l


BARE_ISBN_RE = re.compile(r"\b97[89][\-\s]?\d{1,5}[\-\s]?\d{1,7}[\-\s]?\d{1,7}[\-\s]?[\dX]\b")
COPYRIGHT_KEEP = re.compile(
    r"(©|\(c\)\s*\d{4}|^copyright\b|\bISBN\b|^DOI:|^doi:|"
    r"\b(First|Second|Third|Fourth|Fifth)\s+(Edition|Printing|Release)\b|"
    r"^\s*97[89][\-\s]?\d[\d\-\s]{9,15}[\dX]\s*$|"
    r"^(First|Second|Third|Fourth|Fifth|\d+(st|nd|rd|th))\s+(published|edition|paperback|printing)|"
    r"^(Published|Reprinted|Printed)\b|^\d{4}\s*©)", re.I)
COPYRIGHT_NOISE = re.compile(
    r"(\bfloor\b|\bplaza\b|\broad\b|\bstreet\b|\bhouse\b|www\.|https?://|\s\|\s|"
    r"^\s*(names|title|description|identifiers|subjects|classification|lc)\s*:|"
    r"cataloging|catalogue record|permission|responsibility|accuracy|"
    r"statutory|licensing|\bmission\b)", re.I)


def tighten_copyright(lines) -> None:
    """Keep only bibliographic facts: ©, ISBN, DOI, edition/printing history,
    publisher. Addresses, CIP field dumps and legal text are dropped. A kept
    line that wraps ("Second edition © Andrew Ortony, Gerald" / "Clore, and
    Allan Collins 2022") is rejoined."""
    n = len(lines)
    for i, l in enumerate(lines):
        if l.regime != "copyright":
            continue
        t = l.text.strip()
        keep = bool(COPYRIGHT_KEEP.search(t)) and not COPYRIGHT_NOISE.search(t) \
            and len(t) < 140
        if not keep and l.kind == "copyright_keep" and t.isupper() and len(t) < 40:
            keep = True                              # publisher name line
        l.kind = "copyright_keep" if keep else "copyright_drop"
    for i, l in enumerate(lines):
        if l.kind != "copyright_keep":
            continue
        t = l.text.rstrip()
        if (t.endswith(",") or not re.search(r"\d{4}|\.$", t)) and i + 1 < n:
            nxt = lines[i + 1]
            if nxt.regime == "copyright" and nxt.page == l.page and \
                    re.search(r"\d{4}", nxt.text) and len(nxt.text) < 80:
                l.text = t + " " + nxt.text.strip()
                nxt.kind = "consumed"


# =============================================================================
# outline.py  —  outline.py — hierarchical structure of headings, instead of regex lists.
# =============================================================================
"""
outline.py — hierarchical structure of headings, instead of regex lists.

A document's headings form a tree whose numbering must be internally
consistent. That consistency is the evidence:

  * A SPINE number must extend the open path and advance monotonically:
    after 6.2 comes 6.3 (sibling), 6.2.1 (child) or 7 (new chapter).
    "5.7" appearing after "5.10" runs BACKWARDS -> it is a cross-reference
    label, not a heading. No density heuristic needed.
  * A LABELLED SERIES is any word followed by a counter that advances in
    parallel with the spine and restarts per chapter: "Example 6.1, 6.2,
    6.3", "Figure 6.1, 6.2", "Characterization 5.1, 5.2". The word is
    irrelevant; the counter's behaviour identifies it. Members are run-in
    labels, never sections, never in the contents.
  * An UNNUMBERED heading takes the depth of the open spine node + 1. It is
    in the contents only when the document does not number that depth --
    in a book with "6.1, 6.2, ...", an unnumbered sibling like
    "Bibliographical and Historical Remarks" is body structure, not skeleton.
    If its typographic style ranks below every numbered section's style, it
    is a run-in lead-in (bold), not a heading at all.
  * The rendered level comes from tree depth, not from counting dots.

Regex is used here only to tokenise a heading into (label, number, title);
every decision is made on the tree.
"""



SPINE_LABELS = {"chapter", "part", "appendix", "section"}
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
         "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}

# label? number? title   — number may be 6 / 6.3 / 6.3.1 / A / A.1 / IV / 1.
HEAD_TOKEN_RE = re.compile(
    r"^\s*(?:(?P<label>[A-Z][a-zA-Z]{2,})\s+)?"
    r"(?P<num>\d{1,2}(?:\.\d{1,2}){0,3}|[A-H](?:\.\d{1,2}){0,2}|[IVX]{1,4})"
    r"(?![A-Za-z])[.:)]?\s*(?P<title>.*)$")


@dataclass
class Token:
    label: str | None
    num: tuple | None          # ('6', '3') as ints where possible
    title: str


def tokenize(text: str) -> Token:
    t = text.strip()
    m = HEAD_TOKEN_RE.match(t)
    if not m:
        return Token(None, None, t)
    label, num, title = m.group("label"), m.group("num"), m.group("title").strip()
    # a label with no title and a bare capital-letter "number" is prose
    # ("Note A" is unlikely; "Appendix A" is fine)
    if num in ROMAN and label and label.lower() not in SPINE_LABELS:
        return Token(None, None, t)
    if num in ROMAN and not label:
        return Token(None, None, t)                   # "IV" alone: not a heading number
    # a bare capital letter is a number only as "Appendix A" or "A.1";
    # "A simple bandit algorithm" starts with an article
    if re.fullmatch(r"[A-H]", num) and (label or "").lower() != "appendix":
        return Token(None, None, t)
    parts: list = []
    for p in num.split("."):
        parts.append(int(p) if p.isdigit() else (ROMAN[p] if p in ROMAN else p))
    return Token(label, tuple(parts), title)


@dataclass
class Node:
    line: object
    tok: Token
    depth: int = 0
    kind: str = "section"       # section | labelled | off_sequence | unnumbered | runin
    in_toc: bool = False
    parent: "Node | None" = field(default=None, repr=False)


def apply_outline(lines, prof) -> dict:
    """Decide level / kind / TOC membership for every heading from the tree.
    Mutates lines: sets .level, .in_toc, and may retype .kind to
    'runin' (labelled series, sub-section lead-ins) or 'body' (off-sequence
    cross-reference labels). Returns summary counts."""
    heads = [l for l in lines if l.kind == "heading"]
    if not heads:
        return {}

    # style rank: lower number = more prominent. From the profile's ranking.
    def rank(l):
        return prof.heading_styles.get(l.style, 99)

    nodes: list[Node] = []
    last_by_prefix: dict[tuple, int] = {}       # spine: prefix -> last child number
    series_last: dict[tuple, int] = {}          # (label, prefix) -> last counter
    series_count: dict[str, int] = {}
    open_path: list[Node] = []                  # current spine chain, root first
    numbered_depths: set[int] = set()

    for l in heads:
        tok = tokenize(l.text)
        node = Node(l, tok)
        label = (tok.label or "").lower()

        # ---------------------------------------------------- spine (numbered)
        if tok.num is not None and (not tok.label or label in SPINE_LABELS):
            num = tok.num
            if label == "part":
                node.depth, node.kind = 1, "section"     # parts are dividers
                nodes.append(node); continue
            prefix, last = num[:-1], num[-1]
            prev = last_by_prefix.get(prefix)
            advancing = (prev is None) or (type(last) is not type(prev)) \
                        or (isinstance(last, int) and last > prev) \
                        or (isinstance(last, str) and last > prev)
            # a deeper number must extend the currently open spine
            extends = len(num) == 1 or any(n.tok.num == prefix for n in open_path)
            if not extends and prefix in last_by_prefix:
                extends = True                       # parent seen earlier, path re-opened
            if advancing and extends:
                node.depth = len(num)
                node.kind = "section"
                last_by_prefix[prefix] = last
                # reset deeper counters under this node
                for k in [k for k in last_by_prefix if len(k) > len(num) and k[:len(num)] == num]:
                    del last_by_prefix[k]
                open_path = [n for n in open_path if n.tok.num and len(n.tok.num) < len(num)
                             and num[:len(n.tok.num)] == n.tok.num]
                node.parent = open_path[-1] if open_path else None
                open_path.append(node)
                numbered_depths.add(node.depth)
            else:
                node.kind = "off_sequence"           # "5.7" after "5.10": a cross-ref
            nodes.append(node); continue

        # ---------------------------------------------------- labelled series
        if tok.num is not None and tok.label:
            key = (label, tok.num[:-1])
            prev = series_last.get(key)
            series_last[key] = tok.num[-1]
            series_count[label] = series_count.get(label, 0) + 1
            node.kind = "labelled"
            node.depth = (open_path[-1].depth + 1) if open_path else 1
            nodes.append(node); continue

        # ---------------------------------------------------- unnumbered
        node.kind = "unnumbered"
        node.depth = (open_path[-1].depth + 1) if open_path else 1
        node.parent = open_path[-1] if open_path else None
        nodes.append(node)

    # ---- second pass: labelled series with a single member is just an
    # unnumbered heading that happens to start with a capitalised word+number
    for n in nodes:
        if n.kind == "labelled" and series_count.get((n.tok.label or "").lower(), 0) < 2:
            n.kind = "unnumbered"

    # ---- style floor: the least prominent numbered-section style
    section_ranks = [rank(n.line) for n in nodes if n.kind == "section" and n.depth >= 2]
    floor = max(section_ranks) if section_ranks else 99
    has_numbered_sections = any(d >= 2 for d in numbered_depths)

    counts = {"section": 0, "labelled": 0, "off_sequence": 0, "unnumbered": 0, "runin": 0}
    for n in nodes:
        l = n.line
        if n.kind == "off_sequence":
            l.kind, l.level = "body", 0
            counts["off_sequence"] += 1
            continue
        if n.kind == "labelled":
            l.kind, l.level = "runin", 0
            counts["labelled"] += 1
            continue
        if n.kind == "unnumbered":
            # front/back matter and part-level words stay level 1
            top = bool(re.match(r"^(Abstract|Preface|Foreword|Introduction|Prologue|"
                                r"Acknowledg(e)?ments|Summary of Notation|Notation|"
                                r"About the Authors?|Epilogue|Afterword|Conclusions?|"
                                r"References|Bibliography|Glossary|Notes|Index|Subject Index|"
                                r"Author Index|Appendix|Appendices|Contents|List of Figures|"
                                r"List of Tables|Figures|Tables)\b", l.text.strip(), re.I))
            if top:
                n.depth = 1
            elif has_numbered_sections and n.depth >= 2 and rank(l) > floor:
                # below every numbered section's style: a run-in lead-in
                l.kind, l.level = "runin", 0
                counts["runin"] += 1
                continue
            l.level = min(6, max(1, n.depth))
            l.in_toc = (n.depth == 1) or (not has_numbered_sections and n.depth <= 3)
            counts["unnumbered"] += 1
            continue
        # section
        l.level = min(6, max(1, n.depth))
        l.in_toc = n.depth <= 3
        counts["section"] += 1
    counts["numbered_depths"] = sorted(numbered_depths)
    return counts


# =============================================================================
# papers.py  —  papers.py — research-paper support for pdf2md.
# =============================================================================
"""
papers.py — research-paper support for pdf2md.

A paper differs from a book in ways that break every book assumption:

  * two-column layout: sorting by y interleaves the columns into gibberish;
    reading order must be column-major within bands delimited by full-width
    lines (title, abstract, wide figures)
  * no title page: the title block is the top of page 1 — title (largest
    line), authors, emails, affiliations — followed by an "Abstract" heading
  * numbered sections without "Chapter": "1 Introduction", "2.1 Method",
    plus unnumbered Abstract / Acknowledgments / References / Appendix A
  * an arXiv identifier printed sideways down the left margin of page 1
  * bracketed references "[12] Author, ..." with hanging-indent continuations
  * per-column left margins, so every "is this indented?" test needs the
    column's margin, not the page's
"""



ARXIV_RE = re.compile(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", re.I)
ABSTRACT_RE = re.compile(r"^\s*abstract\s*[.:—\-]?\s*$", re.I)
ABSTRACT_INLINE_RE = re.compile(r"^\s*abstract\s*[.:—\-]\s+(\S.*)$", re.I)
EMAIL_RE = re.compile(r"[\w.\-+]+@[\w.\-]+\.\w+|\{[\w., \-]+\}@[\w.\-]+")
AFFIL_RE = re.compile(r"\b(University|Institute|Department|Dept\.?|Laboratory|Lab\b|"
                      r"College|School of|Research|Center|Centre|Google|Microsoft|"
                      r"DeepMind|OpenAI|Meta AI|IBM|Inc\.|Ltd\.|Corporation)\b|"
                      r"\b[A-Z][a-z]+,\s+[A-Z]{2}\s+\d{5}\b|\bUSA\b|\bUK\b")
KEYWORDS_RE = re.compile(r"^\s*(keywords|index terms)\s*[:—\-]", re.I)
PLACE_RE = re.compile(r"\b(New York|Cambridge|London|Boston|Seattle|Berkeley|Stanford|Paris|Berlin|"
                      r"Tokyo|Beijing|Toronto|Montreal|Zurich|Oxford|Pittsburgh|Chicago|Los Angeles|"
                      r"San Francisco|Washington|Amsterdam|Edinburgh|Vancouver|Sydney|Melbourne)\b")
VENUE_RE = re.compile(r"(Proceedings of|Conference on|Workshop on|Journal of|"
                      r"Transactions on|JMLR|NeurIPS|ICML|ICLR|ACL|EMNLP|NAACL|CVPR|"
                      r"ICCV|ECCV|AAAI|IJCAI|KDD|SIGIR|arXiv preprint)", re.I)
SUPERSCRIPT_MARKS = re.compile(r"[\u2020\u2021\u00a7\u00b6*\u2217\u2660-\u2667\u00b9\u00b2\u00b3"
                               r"\u2070-\u2079\u0e8e\x8e\x8f]+")


# ---------------------------------------------------------------------------
# detection
# ---------------------------------------------------------------------------
def is_paper(lines, doc, prof) -> bool:
    """Short, has an Abstract on page 1 (or an arXiv id), no Chapter
    headings. Two-column layout alone is not enough (some books use it)."""
    if doc.page_count > 60:
        return False
    p1 = [l for l in lines if l.page == 0][:60]
    has_abstract = any(ABSTRACT_RE.match(l.text) or ABSTRACT_INLINE_RE.match(l.text) for l in p1)
    has_arxiv = any(ARXIV_RE.search(t) for t in list(getattr(prof, "rotated_text", [])) + [l.text for l in lines[:400]])
    has_chapter = any(re.match(r"^(Chapter|Part)\s+[\dIVX]", l.text.strip())
                      for l in lines if l.kind == "heading")
    return (has_abstract or has_arxiv) and not has_chapter


# ---------------------------------------------------------------------------
# column-major reading order
# ---------------------------------------------------------------------------
def reorder_columns(lines, page_w: float) -> int:
    """Re-sort each page's lines column-major. Returns the number of pages
    found to be multi-column.

    Columns are a property of the document's template, not of a page, so
    they are found ONCE: a histogram of the left edges of narrow lines over
    the whole document has one peak per column (72 and 307 for an ACL paper;
    45, 222 and 399 for the Federal Register). Per page, a peak that is
    populated by enough of that page's lines is an active column. Boundaries
    sit in the gutters -- between a column's right edge and the next start
    -- never at the midpoint of the starts, which the left column's own text
    would cross. Spanning lines (wider than 60% of the page, or straddling a
    gutter) split the page into bands; within a band the columns are read
    left to right, each top to bottom. Each line gets `col` (-1 spanning,
    else column index) and `col_left`.
    """
    by_page: dict[int, list] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)
    body_all = [l for l in lines if l.kind != "furniture"]
    all_x = [round(l.x0) for l in body_all]
    doc_left = statistics.mode(all_x) if all_x else 0
    narrow_all = [l for l in body_all if (l.x1 - l.x0) < page_w * 0.45]

    # ---- document-level column starts: peaks of the left-edge histogram
    cols: list[float] = []
    if len(narrow_all) >= 40:
        hist = Counter(int(l.x0 // 5) * 5 for l in narrow_all)
        floor = max(4, 0.03 * len(narrow_all))
        peaks = sorted(b for b, n in hist.items() if n >= floor
                       and n >= hist.get(b - 5, 0) and n >= hist.get(b + 5, 0))
        merged: list[float] = []
        for b in peaks:
            if merged and b - merged[-1] <= 25:
                continue
            merged.append(b)
        if merged and abs(merged[0] - doc_left) <= 12:
            merged[0] = doc_left
            # a start must lie beyond the previous column's right edge
            kept, edges = [], []
            for st in merged:
                if kept and st < edges[-1] - 10:
                    continue
                later = [s2 for s2 in merged if s2 > st + 40]
                col_lines = [l for l in narrow_all if st - 8 <= l.x0 < (later[0] - 20 if later else page_w)]
                x1s = sorted(l.x1 for l in col_lines)
                if len(x1s) < 10:
                    continue
                kept.append(st)
                edges.append(x1s[int(0.9 * (len(x1s) - 1))])
            gutters = [(edges[k] + kept[k + 1]) / 2 for k in range(len(kept) - 1)]
            filled = all((edges[k] - kept[k]) >= 0.5 * ((gutters[k] if k < len(gutters) else page_w * 0.95) - kept[k])
                         for k in range(len(kept)))
            if len(kept) >= 2 and filled:
                cols = kept
    multi_pages = 0
    order: list = []
    for page in sorted(by_page):
        pl = by_page[page]
        body = [l for l in pl if l.kind != "furniture"]
        narrow = [l for l in body if (l.x1 - l.x0) < page_w * 0.45]
        active = False
        if cols and len(body) >= 12 and len(narrow) >= 0.5 * len(body):
            use = [sum(1 for l in narrow if abs(l.x0 - c) <= 12) for c in cols]
            active = all(u >= max(3, 0.12 * len(narrow)) for u in use)
        if not active:
            lm = statistics.mode(round(l.x0) for l in body) if body else 0
            for l in pl:
                l.col, l.col_left = -1, lm
            order.extend(sorted(pl, key=lambda l: (round(l.y0 / 3.0), l.x0)))
            continue
        multi_pages += 1
        for l in pl:
            wide = (l.x1 - l.x0) > page_w * 0.6
            crosses = any(l.x0 < g - 8 and l.x1 > g + 8 for g in gutters)
            if wide or crosses:
                l.col, l.col_left = -1, cols[0]
            else:
                k = sum(1 for g in gutters if l.x0 >= g)
                l.col, l.col_left = k, cols[k]
        pl_sorted = sorted(pl, key=lambda l: (l.y0, l.x0))
        bands: list[list] = [[]]
        for l in pl_sorted:
            if l.col == -1:
                if bands[-1]:
                    bands.append([])
                bands[-1].append(l)
                bands.append([])
            else:
                bands[-1].append(l)
        for band in bands:
            if not band:
                continue
            if band[0].col == -1:
                order.extend(band)
                continue
            for k in range(len(cols)):
                order.extend(sorted((l for l in band if l.col == k), key=lambda l: (l.y0, l.x0)))
    lines[:] = order
    return multi_pages


# ---------------------------------------------------------------------------
# front block: title, authors, affiliations, abstract
# ---------------------------------------------------------------------------
def label_paper_front(lines, prof) -> dict:
    """Page 1 above the Abstract heading is the title block. Returns the
    metadata harvested from it."""
    meta = {"title": "", "authors": [], "affiliations": [], "emails": [],
            "arxiv": None, "venue": None}
    p1 = [l for l in lines if l.page == 0 and l.kind not in ("furniture", "consumed")]
    if not p1:
        return meta
    # arXiv id / venue anywhere in the document's furniture or first page
    for t in list(getattr(prof, "rotated_text", [])) + [l.text for l in lines[:400]]:
        m = ARXIV_RE.search(t)
        if m and not meta["arxiv"]:
            meta["arxiv"] = m.group(1) + (m.group(2) or "")
    for l in lines:
        if l.kind == "furniture" and VENUE_RE.search(l.text) and not meta["venue"]:
            meta["venue"] = l.text.strip()
    abs_idx = next((i for i, l in enumerate(p1)
                    if ABSTRACT_RE.match(l.text) or ABSTRACT_INLINE_RE.match(l.text)), None)
    head = p1[:abs_idx] if abs_idx is not None else p1[:14]
    if not head:
        return meta
    # title = the largest lines at the top, contiguous
    top_size = max(l.size for l in head)
    title_lines = []
    for l in head:
        if l.size >= top_size - 0.6 and (not title_lines or l.y0 - title_lines[-1].y0 < top_size * 2.2):
            title_lines.append(l)
        elif title_lines:
            break
    meta["title"] = " ".join(l.text.strip() for l in title_lines)
    for l in title_lines:
        l.kind = "paper_title"
    rest = [l for l in head if l.kind != "paper_title"]
    for l in rest:
        t = l.text.strip()
        if EMAIL_RE.search(t) and len(EMAIL_RE.sub("", t).strip()) < 6:
            l.kind = "paper_email"; meta["emails"].append(t)
        elif AFFIL_RE.search(t) and not re.search(r"\b(and)\b", t):
            l.kind = "paper_affil"; meta["affiliations"].append(t)
        elif re.search(r"[A-Z][a-z]+\s+[A-Z][a-z]+", t) and len(t) < 160:
            l.kind = "paper_author"
            clean = SUPERSCRIPT_MARKS.sub("", EMAIL_RE.sub("", t))
            clean = re.sub(r"\s+\d+(,\d+)*\b", "", clean)
            clean = re.sub(r"(?<=[A-Za-z])\d+(,\d+)*", "", clean)
            for part in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", clean):
                part = part.strip(" ,.;")
                if re.match(r"^[^\W\d_][^\W\d_'’\-]+(\s+[^\W\d_]\.)*(\s+(?:van|von|de|der|da|di|la|le))?\s+[^\W\d_][^\W\d_'’\-]+$", part) \
                        and part[0].isupper() and not AFFIL_RE.search(part) \
                        and not PLACE_RE.search(part):
                    meta["authors"].append(part)
        else:
            l.kind = "paper_affil"; meta["affiliations"].append(t)
    if abs_idx is not None:
        a = p1[abs_idx]
        m = ABSTRACT_INLINE_RE.match(a.text)
        if m:                                   # "Abstract. This paper ..." -> split
            import copy
            rest = copy.copy(a); rest.text = m.group(1); rest.kind = "body"; rest.y0 += 0.1
            lines.insert(lines.index(a) + 1, rest)
        a.kind, a.level, a.text = "heading", 1, "Abstract"
        a.regime = "body"
    return meta


# ---------------------------------------------------------------------------
# section headings for papers
# ---------------------------------------------------------------------------
PAPER_HEAD_RE = re.compile(
    r"^(?:(\d{1,2}(?:\.\d{1,2}){0,2})\.?\s+|(?:Appendix\s+)?([A-H])(?:\.\d{1,2})?\.?\s+)"
    r"[A-Z][A-Za-z]")
UNNUMBERED_HEADS = re.compile(
    r"^(Acknowledg(e)?ments?|References|Bibliography|Appendix|Appendices|"
    r"Conclusions?|Discussion|Related Work|Introduction|Methods?|Results|"
    r"Experiments|Supplementary Material|Ethics Statement|Limitations|"
    r"Broader Impact|Author Contributions|Funding)$", re.I)


def promote_paper_headings(lines, prof) -> int:
    """Papers set section titles in a bold or larger face at the column
    margin. When the style ranking found them, fine; when it did not (no
    bold face recognised, OCR layer, odd fonts), a numbered short line at the
    column margin with a following paragraph is a heading. Returns count."""
    n = 0
    BARE_NUM = re.compile(r"^\d{1,2}(?:\.\d{1,2}){0,2}\.?$")
    for i, l in enumerate(lines):
        if l.kind != "body":
            continue
        t = l.text.strip()
        at_margin = abs(l.x0 - getattr(l, "col_left", prof.body_left)) <= 6
        # "2.1" | "Node Types": a bare number at the margin with a short bold
        # title on the same baseline. The style may have been claimed by
        # captions (papers set both in the same bold face); the geometry is
        # unambiguous on its own.
        if BARE_NUM.match(t) and at_margin and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.page == l.page and abs(nxt.y0 - l.y0) < 3 and nxt.x0 > l.x1 - 2 \
                    and nxt.kind == "body" and (nxt.is_bold or l.is_bold) \
                    and len(nxt.text.strip()) <= 90:
                lvl = t.rstrip(".").count(".") + 1
                l.kind, l.level = "heading", lvl
                nxt.kind, nxt.level = "heading", lvl
                n += 1
            continue
        short = len(t) <= 90 and not re.search(r"[.!?:;,]$", t)
        if not (at_margin and short and (l.is_bold or l.size > prof.body_size + 0.4
                                          or l.isolated)):
            continue
        m = PAPER_HEAD_RE.match(t)
        if m:
            num = m.group(1)
            l.kind = "heading"
            l.level = (num.count(".") + 1) if num else 2
            n += 1
        elif UNNUMBERED_HEADS.match(t) and (l.is_bold or l.size > prof.body_size + 0.4 or l.isolated):
            l.kind, l.level = "heading", 1
            n += 1
    return n


# =============================================================================
# doctype.py  —  doctype.py — decide what kind of document this is BEFORE anything else.
# =============================================================================
"""
doctype.py — decide what kind of document this is BEFORE anything else.

Every later stage assumes a genre: front-matter labelling assumes a book,
column reordering assumes a paper, per-slide rendering assumes a deck. A
wrong genre cascades into confident nonsense (a PRD's author table becomes
"Publication details"; a deck's slide titles become an index). So the genre
is decided once, early, by scoring independent signals and recording the
evidence, and can be overridden on the command line.

Kinds:
  book      long-form: chapters, front/back matter, running heads, ISBN/©
  paper     research article: abstract, arXiv id, two columns, references
  deck      slides: screen-sized landscape pages, sparse text, a title in
            the same place on every page, agenda / thank-you slides
  document  standard document: PRD, spec, memo, report — portrait, short,
            title block with author/status/version, numbered sections

Signals are grouped so that no single family can decide alone:
  geometry   page dimensions, orientation, count
  metadata   producer / creator strings
  density    words per page, bullet share, title-position consistency
  markers    uniquely identifying content (Abstract, arXiv, ISBN, Chapter,
             Agenda, Revision History, Status: Draft ...)
"""



KINDS = ("book", "paper", "deck", "document")

DECK_DIMS = [(960, 540), (720, 540), (1024, 768), (720, 405), (1920, 1080),
             (1280, 720), (1280, 800), (1440, 900), (792, 612)]   # last: Letter landscape
DECK_PRODUCER = re.compile(r"powerpoint|keynote|impress|beamer|google slides|slides", re.I)
DOC_PRODUCER = re.compile(r"\bword\b|writer|google docs|pages\b|notion|confluence", re.I)
BOOK_PRODUCER = re.compile(r"indesign|quark|affinity|scribus|blurb|lightroom", re.I)
TEX_PRODUCER = re.compile(r"latex|pdftex|xetex|luatex|dvips", re.I)

DT_ABSTRACT_RE = re.compile(r"^\s*abstract\b", re.I)
BOOK_MARK_RE = re.compile(r"\bISBN\b|all rights reserved|library of congress|cataloging-in-publication|"
                          r"printed in (the )?[A-Z]|first published|\bedition\b", re.I)
CHAPTER_RE = re.compile(r"^\s*(Chapter|CHAPTER)\s+\d+", re.I)
DOC_MARK_RE = re.compile(r"product requirements|\bPRD\b|requirements document|design doc|technical design|"
                         r"\bRFC\b|\bmemo(randum)?\b|specification|proposal|revision history|"
                         r"status\s*:\s*(draft|final|review|approved)|^\s*version\s*:?\s*\d|"
                         r"^\s*author\s*:|^\s*reviewers?\s*:|last updated|^\s*owner\s*:|^\s*to\s*:\s+\S|^\s*from\s*:\s+\S", re.I)
FORM_MARK_RE = re.compile(r"\bForm\s+[0-9]{3,4}[A-Z\-]*\b|OMB No\.|Department of the Treasury|"
                          r"Internal Revenue Service|See (separate )?instructions|Attach to|"
                          r"Your (first|last) name|Social security number|Signature of|Date signed|"
                          r"[\u2610\u2611\u2612\u25a1\u25a0]|Check (one|all that apply)|For Paperwork Reduction", re.I)
DECK_MARK_RE = re.compile(r"^\s*agenda\s*$|^\s*thank you!?\s*$|^\s*questions\??\s*$|^\s*q\s*&\s*a\s*$|"
                          r"^\s*confidential\b|^\s*slide\s+\d+|^\s*\d{1,3}\s*/\s*\d{1,3}\s*$", re.I)
PAPER_MARK_RE = re.compile(r"^\s*(keywords|index terms)\s*[:—-]|^\s*related work\s*$|^\s*\d?\.?\s*references\s*$|"
                           r"^\s*acknowledg(e)?ments\s*$|proceedings of|conference on|journal of", re.I)
DT_BULLET_RE = re.compile(r"^\s*[•▪●◦‣⁃∙·\-–*]\s+\S")
LEADER_RE = re.compile(r"(?:\.\s?){4,}")


@dataclass
class DocType:
    kind: str
    confidence: float
    scores: dict
    evidence: list = field(default_factory=list)

    def __str__(self):
        return f"{self.kind} ({self.confidence:.0%})"


def classify_document(doc, lines, prof) -> DocType:
    scores = Counter({k: 0.0 for k in KINDS})
    ev: list[str] = []

    def add(kind, pts, why):
        scores[kind] += pts
        ev.append(f"+{pts:g} {kind}: {why}")

    n = doc.page_count
    w, h = prof.page_w, prof.page_h
    ratio = w / h if h else 1.0
    by_page: dict[int, list] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)
    npages = max(1, len(by_page))

    # ---------------------------------------------------------------- geometry
    if any(abs(w - dw) <= 2 and abs(h - dh) <= 2 for dw, dh in DECK_DIMS[:-1]):
        add("deck", 4, f"screen-sized page {w:.0f}x{h:.0f}")
    elif ratio > 1.2:
        add("deck", 2 if any(abs(ratio - r) < 0.03 for r in (16/9, 4/3, 16/10)) else 1,
            f"landscape ratio {ratio:.2f}")
    if n <= 3:
        add("document", 2, f"{n} pages")
    elif n <= 25:
        add("document", 1, f"{n} pages"); add("paper", 1, f"{n} pages"); add("deck", 1, f"{n} pages")
    elif n <= 60:
        add("paper", 1, f"{n} pages"); add("deck", 1, f"{n} pages")
    else:
        add("book", 3, f"{n} pages")
    if n > 150:
        add("book", 1, "very long")

    # ---------------------------------------------------------------- metadata
    meta = " ".join(str(doc.metadata.get(k, "")) for k in ("producer", "creator"))
    if DECK_PRODUCER.search(meta):
        add("deck", 4, f"producer: {meta.strip()[:40]}")
    if DOC_PRODUCER.search(meta):
        add("document", 2, f"producer: {meta.strip()[:40]}")
    if BOOK_PRODUCER.search(meta):
        add("book", 2, f"producer: {meta.strip()[:40]}")
    if TEX_PRODUCER.search(meta):
        add("paper", 1, "TeX producer"); add("book", 1, "TeX producer")

    # ---------------------------------------------------------------- density
    words = [sum(len(l.text.split()) for l in pl) for pl in by_page.values()]
    med_words = statistics.median(words) if words else 0
    if med_words < 100:
        add("deck", 3 if n >= 3 else 1, f"{med_words:.0f} words/page")
    elif med_words < 250:
        add("document", 1, f"{med_words:.0f} words/page")
    else:
        add("paper", 1, f"{med_words:.0f} words/page"); add("book", 1, f"{med_words:.0f} words/page")

    bullets = sum(1 for l in lines if DT_BULLET_RE.match(l.text))
    if lines and bullets / len(lines) > 0.25:
        add("deck", 2, f"{bullets / len(lines):.0%} bullet lines"); add("document", 1, "bullet-heavy")

    # title-position consistency: the largest line sits in the same place on most pages
    # only pages whose largest line is display-sized count: on a book's body
    # pages the "largest" line is just the first body line, always at the top
    tops = []
    for pl in by_page.values():
        big = max(pl, key=lambda l: l.size)
        if big.size >= prof.body_size * 1.25:
            tops.append((round(big.y0 / 15), round(big.size)))
    if len(tops) >= 4 and len(tops) >= 0.5 * npages:
        common = Counter(tops).most_common(2)
        top1 = common[0][1]
        top2 = top1 + (common[1][1] if len(common) > 1 else 0)
        if top1 / npages >= 0.6:
            add("deck", 4, f"display-sized title at same position on {top1}/{npages} pages")
        elif top2 / npages >= 0.7:
            add("deck", 3, f"two title placements cover {top2}/{npages} pages")

    # ---------------------------------------------------------------- markers
    p1 = [l.text for l in by_page.get(0, [])][:60]
    p12 = [l.text for p in (0, 1, 2) for l in by_page.get(p, [])]
    alltext = [l.text for l in lines]
    rotated = list(getattr(prof, "rotated_text", []))

    if any(DT_ABSTRACT_RE.match(t) for t in p1):
        add("paper", 4, "Abstract on page 1")
    if any(ARXIV_RE.search(t) for t in rotated + p12):
        add("paper", 4, "arXiv identifier")
    if sum(1 for t in alltext if PAPER_MARK_RE.search(t)) >= 2:
        add("paper", 1, "paper vocabulary (keywords/references/related work)")

    book_marks = sum(1 for t in p12 + alltext[-200:] if BOOK_MARK_RE.search(t))
    if book_marks >= 2:
        add("book", 3, f"{book_marks} publication markers (ISBN/©/edition)")
    chapters = sum(1 for l in lines if CHAPTER_RE.match(l.text) and l.size >= prof.body_size * 1.3)
    if chapters >= 2:
        add("book", 4, f"{chapters} 'Chapter N' headings")
    leader_pages = sum(1 for pl in by_page.values() if sum(1 for l in pl if LEADER_RE.search(l.text)) >= 4)
    if leader_pages:
        add("book", 2, f"{leader_pages} dot-leader contents pages")
    # cover: a very large line on page 1 of a long document
    if n >= 30 and by_page.get(0):
        big = max(l.size for l in by_page[0])
        if big >= prof.body_size * 2.2 and len(by_page[0]) <= 14:
            add("book", 2, f"cover page ({big:.0f}pt title)")

    doc_marks = sum(1 for t in p12 if DOC_MARK_RE.search(t))
    if doc_marks:
        add("document", min(5, 2 * doc_marks), f"{doc_marks} document markers (PRD/status/version/author)")
    form_words = sum(1 for t in alltext if FORM_MARK_RE.search(t) and re.search(r"[A-Za-z]{3,}", t)
                     and not re.fullmatch(r"\s*[\u2610\u2611\u2612\u25a1\u25a0]\s*.*", t))
    form_glyphs = any(re.search(r"[\u2610\u2611\u2612]", t) for t in alltext)   # real checkboxes only
    form_marks = form_words + (1 if form_glyphs else 0)
    if form_marks >= 3:
        add("document", min(5, form_marks), f"{form_marks} form markers (Form N / OMB / signature / checkboxes)")
    deck_marks = sum(1 for t in alltext if DECK_MARK_RE.match(t))
    if deck_marks:
        add("deck", min(4, deck_marks), f"{deck_marks} deck markers (agenda/thank you/confidential/N of M)")

    # two-column share
    two_col = sum(1 for pl in by_page.values() if len(pl) >= 12 and
                  sum(1 for l in pl if (l.x1 - l.x0) < w * 0.5) >= 0.6 * len(pl) and
                  sum(1 for l in pl if l.x0 > w / 2 + 5) >= 0.25 * len(pl))
    if two_col >= max(2, 0.4 * npages):
        add("paper", 2, f"{two_col}/{npages} two-column pages")

    # ---------------------------------------------------------------- decide
    priority = {"document": 0, "paper": 1, "book": 2, "deck": 3}
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], priority[kv[0]]))
    top, second = ranked[0], ranked[1]
    conf = top[1] / (top[1] + second[1]) if (top[1] + second[1]) else 0.5
    return DocType(top[0], conf, dict(scores), ev)


# =============================================================================
# decks.py  —  decks.py — slide decks.
# =============================================================================
"""
decks.py — slide decks.

A slide is a unit: its largest line is the title, everything else is
content, and the page boundary is hard. Books' assumptions (reflow across
pages, front/back regimes, heading styles ranked across the document) all
invert here, so decks take their own path:

  * page 1 with one or two display lines            -> document title
  * a page with <=3 lines, all display-sized         -> section divider (#)
  * otherwise: largest line                          -> slide title (##)
  * bullet-glyph lines / short lines                 -> list items
  * long lines                                       -> paragraphs
  * repeated small lines (slide number, footer)      -> furniture (already)
  * '---' between slides (Marp / reveal-compatible)
"""



DK_BULLET_RE = re.compile(r"^\s*(?:[•▪●◦‣⁃∙·\-–*□■]|[onlu§](?=\s))\s*")
END_RE = re.compile(r"^\s*(thank you!?|questions\??|q\s*&\s*a)\s*$", re.I)


def label_deck(lines, prof) -> dict:
    """Retype lines for slide rendering. Returns metadata {title, subtitle}."""
    meta = {"title": "", "subtitle": ""}
    by_page: dict[int, list] = {}
    for l in lines:
        if l.kind != "furniture":
            by_page.setdefault(l.page, []).append(l)
    # reference size = median of the largest line on content slides (>=4
    # lines). The profile's body size is unreliable on sparse slides, where
    # the title style can dominate character count.
    title_sizes = [max(l.size for l in pl) for pl in by_page.values() if len(pl) >= 4]
    ref = statistics.median(title_sizes) if title_sizes else prof.body_size * 1.5
    body = prof.body_size
    for page in sorted(by_page):
        pl = sorted(by_page[page], key=lambda l: (l.y0, l.x0))
        if not pl:
            continue
        big = max(pl, key=lambda l: l.size)
        display = [l for l in pl if l.size >= big.size - 1.5]
        # ---- title slide / section divider: few lines, all display-sized
        if len(pl) <= 3 and len(display) >= 1 and big.size >= ref * 0.9:
            if page == 0 and not meta["title"]:
                meta["title"] = big.text.strip()
                big.kind, big.level = "deck_title", 1
                for l in pl:
                    if l is not big:
                        l.kind = "deck_subtitle"
                        meta["subtitle"] = (meta["subtitle"] + " " + l.text.strip()).strip()
            elif END_RE.match(big.text):
                for l in pl:
                    l.kind = "deck_end"
            else:
                big.kind, big.level = "heading", 1
                big.in_toc = True
                for l in pl:
                    if l is not big:
                        l.kind = "deck_subtitle"
            continue
        # ---- content slide
        big.kind, big.level = "heading", 2
        big.in_toc = True
        for l in pl:
            if l is big:
                continue
            t = l.text.strip()
            if DK_BULLET_RE.match(t) or len(t.split()) <= 14:
                l.kind = "list_item"
                l.text = DK_BULLET_RE.sub("", t) if DK_BULLET_RE.match(t) else t
                # indentation = nesting: lines further right than the slide's
                # modal left edge are sub-bullets
                l.level = 1
            else:
                l.kind = "body"
        lefts = [l.x0 for l in pl if l.kind == "list_item"]
        if lefts:
            base = statistics.mode(round(x) for x in lefts)
            for l in pl:
                if l.kind == "list_item" and l.x0 > base + 12:
                    l.level = 2
        pl[-1].slide_end = True
    return meta


# =============================================================================
# documents.py  —  documents.py — standard documents: PRDs, specs, memos, reports.
# =============================================================================
"""
documents.py — standard documents: PRDs, specs, memos, reports.

Portrait, short, no chapters, no front/back matter. The first page carries
a title block (largest lines) and often a metadata table (Author, Status,
Version, Date, Reviewers) that belongs in YAML, not in the body. Sections
are numbered "1. Overview" or plain bold headings; the outline tree handles
both.
"""



FIELD_RE = re.compile(r"^\s*(Author|Authors|Owner|Status|Version|Date|Last updated|Updated|To|From|Subject|Re|CC|"
                      r"Reviewers?|Approvers?|Team|Stakeholders|Document type|Doc type|"
                      r"Created|Due|Priority|Ticket|Project|Feature)\b\s*:?\s*(.*)$", re.I)
KV_LINE_RE = re.compile(r"^\s*([A-Z][A-Za-z ]{1,20})\s*:\s*(\S.*)$")


def label_document_front(lines, prof) -> dict:
    """Title block + metadata fields from page 1, above the first heading."""
    meta = {"title": "", "subtitle": "", "fields": {}}
    p1 = [l for l in lines if l.page == 0 and l.kind not in ("furniture", "consumed")]
    if not p1:
        return meta
    # the block is the top of page 1: up to the first heading, table, list or
    # long paragraph, and never more than a dozen lines -- a page whose only
    # heading is the title must not have its whole table swallowed as metadata
    top = max(x.size for x in p1)
    stop = len(p1)
    for i, l in enumerate(p1):
        if i == 0:
            continue
        if (l.kind == "heading" and l.size < top - 0.5) or l.kind in ("table_cell", "list_item", "caption") \
                or len(l.text.split()) > 18:
            stop = i
            break
    block = p1[:min(stop, 12)]
    if not block:
        return meta
    top = max(l.size for l in block)
    title_lines = [l for l in block if l.size >= top - 0.6]
    meta["title"] = " ".join(l.text.strip() for l in title_lines)
    for l in title_lines:
        l.kind = "doc_title"
    rest = [l for l in block if l.kind != "doc_title"]
    # metadata table: cells come through as alternating "Key" / "Value" lines
    # on the same baseline, or "Key: value" lines
    i = 0
    while i < len(rest):
        l = rest[i]
        t = l.text.strip()
        m = KV_LINE_RE.match(t) or FIELD_RE.match(t)
        if m and m.group(2).strip():
            meta["fields"][m.group(1).strip().title()] = m.group(2).strip()
            l.kind = "doc_meta"
        elif FIELD_RE.match(t) and i + 1 < len(rest) and abs(rest[i + 1].y0 - l.y0) < 3:
            meta["fields"][t.rstrip(":").title()] = rest[i + 1].text.strip()
            l.kind = rest[i + 1].kind = "doc_meta"
            i += 1
        elif i == 0 and len(t) < 90 and not t.endswith("."):
            meta["subtitle"] = t
            l.kind = "doc_subtitle"
        i += 1
    return meta


# =============================================================================
# structured.py  —  structured.py — EPUB, DOCX, and (via LibreOffice) DOC / ODT / RTF input.
# =============================================================================
"""
structured.py — EPUB, DOCX, and (via LibreOffice) DOC / ODT / RTF input.

These formats carry real structure -- heading levels, list nesting, tables,
footnotes, metadata -- so nothing needs to be inferred from geometry. The
reader turns the native markup into typed Blocks, the outline tree still
validates heading numbering and TOC membership, and the renderer emits the
same YAML / title block / grouped contents / body layout as the PDF path.

  EPUB   container.xml -> OPF (metadata, manifest, spine) -> each XHTML
         spine item parsed to blocks; nav.xhtml (EPUB 3) or toc.ncx (EPUB 2)
         supplies contents truth and section titles for files with no heading
  DOCX   word/document.xml paragraphs and tables; styles.xml for heading
         levels (outlineLvl, or Heading N names); footnotes.xml; core props
  DOC / ODT / RTF   converted to DOCX with `soffice --headless`, then as DOCX

Only the standard library is used: zipfile, xml.etree, html.parser.
"""




@dataclass
class Block:
    kind: str                      # heading|para|list_item|quote|code|table|caption|footnote|image|hr|title|meta
    text: str = ""
    level: int = 0                 # heading level / list nesting
    rows: list = field(default_factory=list)      # table rows (list of cell lists)
    note_id: str = ""              # footnote id
    src: str = ""                  # image path inside the container
    ordered: bool = False
    size: float = 0.0              # CSS font size (pt) when known
    bold: bool = False


GENERATOR_NAMES = re.compile(r"^(python-docx|libreoffice|openoffice|microsoft (office )?(word|user)|"
                             r"unknown( title| author)?|untitled|calibre|pandoc|author|user|admin|owner)$", re.I)


def css_style_map(css: str) -> dict[str, dict]:
    """.paraN { font-size: 18pt; font-weight: bold } -> {'paraN': {size, bold}}.
    Sizes in pt, px (x0.75), em/% (relative to 12pt)."""
    out: dict[str, dict] = {}
    for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        selectors, body = m.group(1), m.group(2)
        size = None; bold = None
        ms = re.search(r"font-size\s*:\s*([\d.]+)\s*(pt|px|em|%|rem)?", body)
        if ms:
            v = float(ms.group(1)); u = ms.group(2) or "pt"
            size = v if u == "pt" else v * 0.75 if u == "px" else v * 12 if u in ("em", "rem") else v * 0.12
        mw = re.search(r"font-weight\s*:\s*(bold|[6-9]00)", body)
        if mw: bold = True
        if re.search(r"font-weight\s*:\s*(normal|400)", body): bold = False
        for sel in selectors.split(","):
            for cls in re.findall(r"\.([A-Za-z0-9_\-]+)", sel):
                d = out.setdefault(cls, {})
                if size is not None: d["size"] = size
                if bold is not None: d["bold"] = bold
    return out


def promote_css_headings(blocks: list, style_map: dict) -> int:
    """An EPUB with no <h*> tags still ranks its headings by CSS size. Body
    size is the modal paragraph size by character count; paragraphs a tier
    larger, or bold and short, are headings, levelled by size rank."""
    all_paras = [b for b in blocks if b.kind == "para"]
    paras = [b for b in all_paras if b.size]
    if len(all_paras) < 5 or not paras:
        return 0
    from collections import Counter
    weight = Counter()
    for b in paras:
        weight[round(b.size, 1)] += len(b.text)
    # when most body paragraphs carry no size (the stylesheet only sets
    # margins on them), the body is the browser default, not the modal
    # size of the few decorated paragraphs
    body = weight.most_common(1)[0][0] if len(paras) >= 0.5 * len(all_paras) else 12.0
    cands = [b for b in paras if b.size >= body * 1.15 or (b.bold and b.size >= body - 0.1 and len(b.text) < 90
                                                            and not b.text.rstrip().endswith((".", ",", ";")))]
    cands = [b for b in cands if len(b.text) < 160 and not b.text.rstrip().endswith((".", ",", ";"))]
    if not cands:
        return 0
    sizes = sorted({round(b.size, 1) for b in cands}, reverse=True)
    rank = {sz: i + 1 for i, sz in enumerate(sizes)}
    for b in cands:
        b.kind = "heading"; b.level = min(6, rank[round(b.size, 1)] + (1 if b.size < body * 1.15 else 0))
    return len(cands)


STRUCTURED_EXT = {".epub", ".docx", ".doc", ".odt", ".rtf"}


# ===========================================================================
# HTML (XHTML) -> blocks
# ===========================================================================
HTML_BLOCK_TAGS = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote", "pre",
              "figcaption", "caption", "div", "section", "article", "aside", "header",
              "footer", "table", "tr", "td", "th", "ul", "ol", "figure", "hr", "br", "nav",
              "dt", "dd"}


class _HtmlBlocks(HTMLParser):
    """Linear pass over an XHTML chapter, emitting Blocks. Inline emphasis is
    kept as Markdown; footnote references become [^id] markers."""

    def __init__(self, style_map: dict | None = None):
        super().__init__(convert_charrefs=True)
        self.style_map = style_map or {}
        self.blocks: list[Block] = []
        self.stack: list[str] = []
        self.buf: list[str] = []
        self.cur: Block | None = None
        self.list_depth = 0
        self.list_ordered: list[bool] = []
        self.in_table = 0
        self.rows: list[list[str]] = []
        self.cell: list[str] | None = None
        self.in_pre = 0
        self.note: list[str] = []           # nested footnote aside ids
        self.skip = 0                       # inside <nav>/<script>/<style>
        self.title = ""
        self.in_title = False
        self.suppress = 0                   # inside a noteref / backlink anchor

    # ---- helpers
    def _apply_style(self, cls: str):
        for c in cls.split():
            st = self.style_map.get(c)
            if not st or self.cur is None:
                continue
            if "size" in st and not self.cur.size:
                self.cur.size = st["size"]
            if st.get("bold"):
                self.cur.bold = True

    def _flush(self):
        if self.cur is not None:
            t = "".join(self.buf)
            t = t if self.in_pre else re.sub(r"[ \t\r\n]+", " ", t).strip()
            if self.cur.kind == "footnote":
                t = re.sub(r"^\s*(\d{1,3})?\s*[.):]?\s*", "", t).replace("\u21a9\ufe0e", "").replace("\u21a9", "").strip()
            if t or self.cur.kind == "hr":
                self.cur.text = t
                self.blocks.append(self.cur)
        self.cur, self.buf = None, []

    def _start(self, kind, **kw):
        self._flush()
        self.cur = Block(kind, **kw)

    # ---- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        types = (a.get("epub:type") or a.get("role") or "").lower()
        cls = (a.get("class") or "").lower()
        if tag in ("script", "style", "nav") or "toc" in types:
            self.skip += 1
            return
        if self.skip:
            return
        if tag == "title":
            self.in_title = True; return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._start("heading", level=int(tag[1]))
        elif tag == "p":
            if self.note and self.cur is not None and self.cur.kind == "footnote":
                self.buf.append(" ")                # paragraph inside a footnote li: same note
            else:
                kind = "footnote" if self.note else ("caption" if "caption" in cls else "para")
                self._start(kind, note_id=self.note[-1] if self.note else "")
                self._apply_style(cls)
        elif tag == "span" and self.cur is not None and cls:
            self._apply_style(cls)                  # size often lives on the span
        elif tag in ("ul", "ol"):
            self._flush(); self.list_depth += 1; self.list_ordered.append(tag == "ol")
        elif tag == "li" and self.note:
            # pandoc / EPUB 3: <section role="doc-endnotes"><ol><li id="fn1"><p>...
            if a.get("id"):
                self.note[-1] = a.get("id")
            self._start("footnote", note_id=self.note[-1])
        elif tag == "li":
            self._start("list_item", level=self.list_depth, ordered=bool(self.list_ordered and self.list_ordered[-1]))
        elif tag == "blockquote":
            self._flush(); self.stack.append("quote")
        elif tag == "pre":
            self._start("code"); self.in_pre += 1
        elif tag in ("figcaption", "caption"):
            self._start("caption")
        elif tag == "table":
            self._flush(); self.in_table += 1; self.rows = []
        elif tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self.cell = []
        elif tag == "img":
            self._flush()
            self.blocks.append(Block("image", text=a.get("alt", ""), src=a.get("src", "")))
        elif tag == "hr":
            if not self.note:
                self._start("hr")
        elif tag == "br":
            self.buf.append(" ")
        elif tag == "aside" and ("footnote" in types or "footnote" in cls or "note" in types):
            self._flush(); self.note.append(a.get("id", ""))
        elif tag in ("div", "section", "article", "header", "footer", "dt", "dd"):
            self._flush()
            if "footnote" in cls or "footnote" in types or "endnote" in types:
                self.note.append(a.get("id", ""))
        elif tag == "a" and ("backlink" in types or "footnote-back" in cls or "back" in cls):
            self.suppress += 1; self.stack.append("backlink")
        elif tag == "a" and ("noteref" in types or "noteref" in cls or "footnote-ref" in cls or "footnote" in cls):
            href = a.get("href", "")
            nid = href.split("#")[-1] if "#" in href else href
            self.buf.append(f"[^{nid}]")
            self.suppress += 1; self.stack.append("noteref")
        elif tag in ("em", "i"):
            self.buf.append("*"); self.stack.append("em")
        elif tag in ("strong", "b"):
            self.buf.append("**"); self.stack.append("strong")
        elif tag == "code" and not self.in_pre:
            self.buf.append("`"); self.stack.append("code")
        elif tag == "sup":
            self.stack.append("sup")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav"):
            self.skip = max(0, self.skip - 1); return
        if self.skip:
            if tag in ("section", "div", "article"):
                pass
            return
        if tag == "title":
            self.in_title = False; return
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6", "li", "figcaption", "caption", "hr"):
            self._flush()
        elif tag == "p" and not (self.note and self.cur is not None and self.cur.kind == "footnote"):
            self._flush()
        elif tag in ("ul", "ol"):
            self._flush(); self.list_depth = max(0, self.list_depth - 1)
            if self.list_ordered: self.list_ordered.pop()
        elif tag == "blockquote":
            self._flush()
            if "quote" in self.stack: self.stack.remove("quote")
        elif tag == "pre":
            self._flush(); self.in_pre = max(0, self.in_pre - 1)
        elif tag in ("td", "th"):
            if self.cell is not None and self.rows:
                self.rows[-1].append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "table":
            self.in_table = max(0, self.in_table - 1)
            rows = [r for r in self.rows if any(c.strip() for c in r)]
            if rows:
                self.blocks.append(Block("table", rows=rows))
            self.rows = []
        elif tag == "aside" and self.note:
            self._flush(); self.note.pop()
        elif tag in ("div", "section", "article", "header", "footer", "dt", "dd"):
            self._flush()
            if self.note and tag == "div":
                pass
        elif tag == "a" and self.stack and self.stack[-1] in ("noteref", "backlink"):
            self.stack.pop(); self.suppress = max(0, self.suppress - 1)
        elif tag in ("em", "i") and "em" in self.stack:
            self.buf.append("*"); self.stack.remove("em")
        elif tag in ("strong", "b") and "strong" in self.stack:
            self.buf.append("**"); self.stack.remove("strong")
        elif tag == "code" and "code" in self.stack:
            self.buf.append("`"); self.stack.remove("code")
        elif tag == "sup" and "sup" in self.stack:
            self.stack.remove("sup")

    def handle_data(self, data):
        if self.skip or self.suppress:
            return
        if self.in_title:
            self.title += data; return
        if self.cell is not None:
            self.cell.append(data); return
        if self.cur is None:
            if data.strip():
                # text outside any block element (bare text in a div)
                kind = "footnote" if self.note else ("quote" if "quote" in self.stack else "para")
                self.cur = Block(kind, note_id=self.note[-1] if self.note else "")
                self.buf.append(data)
            return
        if "sup" in self.stack and re.fullmatch(r"\s*\d{1,3}\s*", data) and not self.note:
            self.buf.append(f"[^{data.strip()}]"); return
        if "quote" in self.stack and self.cur.kind == "para":
            self.cur.kind = "quote"
        self.buf.append(data)

    def close(self):
        super().close()
        self._flush()


def html_to_blocks(markup: str, style_map: dict | None = None) -> tuple[str, list[Block]]:
    p = _HtmlBlocks(style_map)
    p.feed(markup)
    p.close()
    return p.title.strip(), p.blocks


# ===========================================================================
# EPUB
# ===========================================================================
EPUB_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
      "opf": "http://www.idpf.org/2007/opf",
      "dc": "http://purl.org/dc/elements/1.1/",
      "ncx": "http://www.daisy.org/z3986/2005/ncx/"}


def read_epub(path: Path) -> tuple[dict, list[Block]]:
    z = zipfile.ZipFile(path)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//c:rootfile", EPUB_NS).get("full-path")
    opf_dir = str(Path(opf_path).parent)
    opf = ET.fromstring(z.read(opf_path))

    # ---- metadata
    def dc(tag):
        return [(e.text or "").strip() for e in opf.findall(f".//dc:{tag}", EPUB_NS) if (e.text or "").strip()]
    title = (dc("title") or [""])[0]
    meta = {"title": "" if GENERATOR_NAMES.match(title) else title, "authors": [], "publisher": (dc("publisher") or [None])[0],
            "date": (dc("date") or [None])[0], "language": (dc("language") or [None])[0],
            "isbn": [], "identifiers": dc("identifier"), "toc": [], "format": "epub"}
    for e in opf.findall(".//dc:creator", EPUB_NS):
        role = e.get("{%s}role" % EPUB_NS["opf"]) or ""
        if (e.text or "").strip() and role in ("", "aut") and not GENERATOR_NAMES.match(e.text.strip()):
            meta["authors"].append(e.text.strip())
    for ident in meta["identifiers"]:
        digits = re.sub(r"[^0-9X]", "", ident.upper())
        if re.search(r"isbn", ident, re.I) or (len(digits) in (10, 13) and digits.startswith(("97", "0", "1"))):
            if len(digits) in (10, 13):
                meta["isbn"].append(digits)
    if meta["date"]:
        m = re.search(r"\d{4}", meta["date"]); meta["year"] = int(m.group(0)) if m else None

    # ---- manifest + spine
    items = {}
    nav_href = None
    for it in opf.findall(".//opf:manifest/opf:item", EPUB_NS):
        items[it.get("id")] = (it.get("href"), it.get("media-type") or "")
        if "nav" in (it.get("properties") or "").split():
            nav_href = it.get("href")
    spine = [items[ref.get("idref")][0] for ref in opf.findall(".//opf:spine/opf:itemref", EPUB_NS)
             if ref.get("idref") in items]
    spine_el = opf.find(".//opf:spine", EPUB_NS)
    ncx_id = spine_el.get("toc") if spine_el is not None else None

    def zpath(href):
        h = unquote(href.split("#")[0])
        return str(Path(opf_dir) / h) if opf_dir not in (".", "") else h

    style_map: dict = {}
    for _id, (href, mt) in items.items():
        if mt == "text/css" and zpath(href) in z.namelist():
            style_map.update(css_style_map(z.read(zpath(href)).decode("utf-8", "replace")))

    # ---- contents truth: nav.xhtml (EPUB 3) or toc.ncx (EPUB 2)
    toc: list[tuple[int, str, str]] = []          # (depth, title, href)
    if nav_href and zpath(nav_href) in z.namelist():
        toc = _parse_nav(z.read(zpath(nav_href)).decode("utf-8", "replace"))
    elif ncx_id in items and zpath(items[ncx_id][0]) in z.namelist():
        ncx = ET.fromstring(z.read(zpath(items[ncx_id][0])))
        def walk(np, depth):
            for pt in np.findall("ncx:navPoint", EPUB_NS):
                label = "".join(pt.find("ncx:navLabel/ncx:text", EPUB_NS).itertext()).strip()
                src = pt.find("ncx:content", EPUB_NS).get("src", "")
                toc.append((depth, label, src)); walk(pt, depth + 1)
        walk(ncx.find("ncx:navMap", EPUB_NS), 1)
    meta["toc"] = toc
    title_by_file: dict[str, tuple[int, str]] = {}
    for depth, label, href in toc:
        f = unquote(href.split("#")[0])
        title_by_file.setdefault(f, (depth, label))

    # ---- chapters in spine order
    blocks: list[Block] = []
    for href in spine:
        zp = zpath(href)
        if zp not in z.namelist():
            continue
        title, chapter = html_to_blocks(z.read(zp).decode("utf-8", "replace"), style_map)
        idx = spine.index(href) + 1
        for b in chapter:
            if b.note_id:
                b.note_id = f"{idx}-{b.note_id}"
            if "[^" in b.text:
                b.text = re.sub(r"\[\^([^\]]+)\]", lambda m: f"[^{idx}-{m.group(1)}]", b.text)
        # a spine item with no heading of its own takes its title from the contents
        if not any(b.kind == "heading" for b in chapter[:6]):
            hit = title_by_file.get(unquote(href.split("#")[0]))
            if hit and any(b.kind in ("para", "list_item", "table") for b in chapter) \
                    and not re.fullmatch(r"(section|chapter|part)\s*\d+", hit[1].strip(), re.I):
                depth, label = hit
                chapter.insert(0, Block("heading", text=label, level=min(depth, 6)))
        for b in chapter:
            if b.kind == "image" and b.src:
                b.src = zpath(urljoin(href, b.src))
        blocks.extend(chapter)
    meta["css_promoted"] = promote_css_headings(blocks, style_map)
    # a nav-synthesised heading directly followed by the same text as a
    # (now promoted) paragraph is one heading
    dedup: list[Block] = []
    for b in blocks:
        if dedup and b.kind == "heading" and dedup[-1].kind == "heading" \
                and re.sub(r"\WML", "", b.text.lower()) == re.sub(r"\WML", "", dedup[-1].text.lower()):
            dedup[-1].size = max(dedup[-1].size, b.size); continue
        dedup.append(b)
    blocks = dedup
    meta["zip"] = z
    return meta, blocks


def _parse_nav(markup: str) -> list[tuple[int, str, str]]:
    """The EPUB 3 nav document: <nav epub:type="toc"><ol><li><a href>."""
    out: list[tuple[int, str, str]] = []

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.in_toc = 0; self.depth = 0; self.href = None; self.text = []
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "nav" and "toc" in (a.get("epub:type") or ""):
                self.in_toc = 1
            if not self.in_toc: return
            if tag == "ol": self.depth += 1
            if tag == "a": self.href = a.get("href", ""); self.text = []
        def handle_endtag(self, tag):
            if not self.in_toc: return
            if tag == "ol": self.depth -= 1
            if tag == "a" and self.href is not None:
                out.append((max(1, self.depth), "".join(self.text).strip(), self.href)); self.href = None
            if tag == "nav": self.in_toc = 0
        def handle_data(self, data):
            if self.in_toc and self.href is not None: self.text.append(data)
    p = P(); p.feed(markup); p.close()
    return out


# ===========================================================================
# DOCX
# ===========================================================================
WML = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
DCP = {"dc": "http://purl.org/dc/elements/1.1/", "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
       "dcterms": "http://purl.org/dc/terms/"}


def w(tag): return "{%s}%s" % (WML, tag)


def read_docx(path: Path) -> tuple[dict, list[Block]]:
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    meta = {"title": "", "authors": [], "publisher": None, "date": None, "year": None,
            "isbn": [], "language": None, "toc": [], "format": "docx"}
    if "docProps/core.xml" in names:
        core = ET.fromstring(z.read("docProps/core.xml"))
        t = core.find("dc:title", DCP); meta["title"] = (t.text or "").strip() if t is not None and t.text else ""
        c = core.find("dc:creator", DCP)
        creator = (c.text or "").strip() if c is not None else ""
        if creator and not GENERATOR_NAMES.match(creator):
            meta["authors"] = [x.strip() for x in re.split(r";|,", creator) if x.strip()]
        d = core.find("dcterms:created", DCP)
        # a tool's template date (python-docx: 2013) is not the document's date
        if d is not None and d.text and not GENERATOR_NAMES.match(creator):
            meta["date"] = d.text.strip(); m = re.search(r"\d{4}", d.text); meta["year"] = int(m.group(0)) if m else None
        if GENERATOR_NAMES.match(meta["title"]): meta["title"] = ""

    # ---- styles: id -> (name, outline level)
    styles: dict[str, tuple[str, int | None, str | None]] = {}
    if "word/styles.xml" in names:
        st = ET.fromstring(z.read("word/styles.xml"))
        for s in st.findall(w("style")):
            sid = s.get(w("styleId")) or ""
            name_el = s.find(w("name")); name = name_el.get(w("val")) if name_el is not None else sid
            ol = s.find(w("pPr") + "/" + w("outlineLvl"))
            based = s.find(w("basedOn"))
            styles[sid] = (name or sid, int(ol.get(w("val"))) if ol is not None else None,
                           based.get(w("val")) if based is not None else None)

    def heading_level(style_id: str) -> int:
        seen = set()
        sid = style_id
        while sid and sid not in seen:
            seen.add(sid)
            name, ol, based = styles.get(sid, (sid, None, None))
            if ol is not None:
                return ol + 1
            m = re.match(r"^(?:heading|überschrift|titre|título)\s*(\d)", (name or "").lower())
            if m:
                return int(m.group(1))
            sid = based or ""
        m = re.match(r"^Heading(\d)$", style_id or "")
        return int(m.group(1)) if m else 0

    def style_name(style_id: str) -> str:
        return (styles.get(style_id, (style_id, None, None))[0] or style_id or "").lower()

    # ---- footnotes / endnotes
    notes: dict[str, str] = {}
    for part in ("word/footnotes.xml", "word/endnotes.xml"):
        if part in names:
            fn = ET.fromstring(z.read(part))
            tag = "footnote" if "footnotes" in part else "endnote"
            for n in fn.findall(w(tag)):
                nid = n.get(w("id"))
                if nid in ("-1", "0"):
                    continue
                notes[nid] = " ".join(_para_text(p, None)[0] for p in n.findall(".//" + w("p"))).strip()

    # ---- relationships (images)
    rels: dict[str, str] = {}
    if "word/_rels/document.xml.rels" in names:
        rel = ET.fromstring(z.read("word/_rels/document.xml.rels"))
        for r in rel:
            rels[r.get("Id")] = "word/" + r.get("Target") if not r.get("Target", "").startswith("/") else r.get("Target").lstrip("/")

    # ---- body
    doc = ET.fromstring(z.read("word/document.xml"))
    body = doc.find(w("body"))
    blocks: list[Block] = []
    used_notes: list[str] = []

    def walk(container):
        for el in container:
            if el.tag == w("p"):
                text, refs, images = _para_text(el, rels)
                used_notes.extend(refs)
                ppr = el.find(w("pPr"))
                sid = ""
                num = None
                if ppr is not None:
                    ps = ppr.find(w("pStyle")); sid = ps.get(w("val")) if ps is not None else ""
                    npr = ppr.find(w("numPr"))
                    if npr is not None:
                        il = npr.find(w("ilvl")); num = int(il.get(w("val"))) if il is not None else 0
                name = style_name(sid)
                for img in images:
                    blocks.append(Block("image", text=img[1], src=img[0]))
                if not text.strip():
                    continue
                lvl = heading_level(sid)
                if name in ("title",):
                    blocks.append(Block("title", text=text))
                elif name in ("subtitle",):
                    blocks.append(Block("meta", text=text))
                elif lvl:
                    blocks.append(Block("heading", text=text, level=min(lvl, 6)))
                elif num is not None or "list" in name:
                    blocks.append(Block("list_item", text=text, level=(num or 0) + 1,
                                        ordered="number" in name))
                elif "quote" in name:
                    blocks.append(Block("quote", text=text))
                elif "caption" in name:
                    blocks.append(Block("caption", text=text))
                elif "code" in name or "preformatted" in name or "source" in name:
                    blocks.append(Block("code", text=text))
                else:
                    # a short all-bold paragraph with no style is a run-in heading
                    runs = el.findall(".//" + w("r"))
                    bold = runs and all(r.find(w("rPr") + "/" + w("b")) is not None for r in runs
                                        if (r.find(w("t")) is not None and (r.find(w("t")).text or "").strip()))
                    if bold and len(text) < 80 and not text.endswith((".", ":")):
                        blocks.append(Block("heading", text=text, level=0))     # level from outline
                    else:
                        blocks.append(Block("para", text=text))
            elif el.tag == w("tbl"):
                rows = []
                for tr in el.findall(w("tr")):
                    cells = []
                    for tc in tr.findall(w("tc")):
                        cells.append(" ".join(_para_text(p, None)[0] for p in tc.findall(w("p"))).strip())
                    if any(cells):
                        rows.append(cells)
                if rows:
                    blocks.append(Block("table", rows=rows))
            elif el.tag in (w("sdt"),):
                content = el.find(w("sdtContent"))
                if content is not None:
                    # a Word-generated table of contents is regenerated by us
                    txt = "".join(content.itertext())
                    if re.search(r"\bcontents\b", txt[:200], re.I) and len(content.findall(".//" + w("hyperlink"))) >= 3:
                        continue
                    walk(content)
            elif el.tag == w("sectPr"):
                continue
    walk(body)
    for nid in used_notes:
        if nid in notes:
            blocks.append(Block("footnote", text=notes[nid], note_id=nid))
    meta["zip"] = z
    return meta, blocks


def _para_text(p, rels) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Text of a w:p with inline emphasis, footnote refs and images."""
    out: list[str] = []; refs: list[str] = []; images: list[tuple[str, str]] = []
    for r in p.iter():
        if r.tag == w("t"):
            out.append(r.text or "")
        elif r.tag == w("tab"):
            out.append("\t")
        elif r.tag in (w("br"), w("cr")):
            out.append(" ")
        elif r.tag == w("footnoteReference") or r.tag == w("endnoteReference"):
            nid = r.get(w("id")); refs.append(nid); out.append(f"[^{nid}]")
        elif r.tag == "{%s}blip" % A_NS and rels is not None:
            rid = r.get("{%s}embed" % R_NS)
            if rid in rels:
                images.append((rels[rid], ""))
        elif r.tag == "{%s}docPr" % WP_NS and images:
            images[-1] = (images[-1][0], r.get("descr") or r.get("name") or "")
    text = "".join(out)
    return re.sub(r"[ \t]+", " ", text).strip(), refs, images


# ===========================================================================
# DOC / ODT / RTF via LibreOffice
# ===========================================================================
def convert_with_soffice(path: Path, soffice: str | None = None) -> Path:
    exe = soffice or shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise RuntimeError(f"{path.name}: converting {path.suffix} needs LibreOffice (soffice) on PATH")
    out_dir = Path(tempfile.mkdtemp(prefix="pdf2md_"))
    cmd = [exe, "--headless", "--norestore", "--convert-to", "docx", "--outdir", str(out_dir), str(path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=180,
                       env={"HOME": str(out_dir), "PATH": "/usr/bin:/bin:/usr/local/bin"})
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{path.name}: LibreOffice conversion timed out")
    out = out_dir / (path.stem + ".docx")
    if not out.exists():
        raise RuntimeError(f"{path.name}: LibreOffice produced no DOCX")
    return out


# =============================================================================
# pdf2md.py  —  core pipeline: extract, profile, classify, tables, figures, assemble, CLI
# =============================================================================


# ===========================================================================
# GLYPH REPAIR
# ===========================================================================
# LaTeX PDFs built with Computer Modern have no proper ToUnicode CMap for many
# glyph slots. PyMuPDF falls back to the raw slot index, which lands on
# arbitrary Unicode arrows/symbols. These are NOT OCR errors -- the text layer
# is "correct", it is the encoding that lies. Mapping is font-family aware
# because the same slot means different things in a text font vs a math font.

TEXT_FONT_RE = re.compile(r"CM(R|TI|BX|BXTI|SL|SS|TT|CSC)|Helvetica|Times|Arial", re.I)
MATH_FONT_RE = re.compile(r"CM(MI|SY|EX|MIB|BSY)|MSAM|MSBM|EUSM|EUFM", re.I)

# Ligature slots — safe everywhere.
LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl",
    "\ufb03": "ffi", "\ufb04": "ffl",
}

# Slot collisions that resolve differently by font class.
# Verified against Sutton & Barto RL 2e; extend via --glyph-map.
GLYPH_BY_CLASS = {
    "text": {
        "\u21b5": "ff",   # ↵  di↵erent -> different   (CM 'ff' ligature slot)
        "\u21e4": "*",
        "\u21e5": "x",
    },
    "math": {
        "\u21e1": "\u03c0",  # ⇡ -> π   (policy)
        "\u2713": "\u03b8",  # ✓ -> θ   (threshold)
        "\u21e4": "*",       # ⇤ -> *   (optimal, as in v_*)
        "\u21e5": "\u00d7",  # ⇥ -> ×
        "\u21dd": "\u2192",  # ⇝ -> →
        "\u21b5": "\u03b1",  # ↵ -> α   (same slot that is 'ff' in a text font)
    },
}

# Accent glyphs emitted BEFORE their base letter by TeX. Recombine as
# base + combining mark so "¯R" becomes "R̄" and "ˆv" becomes "v̂".
PREFIX_ACCENTS = {"\u00af": "\u0304", "\u02c6": "\u0302", "\u02dc": "\u0303",
                  "\u02d9": "\u0307", "\u02ca": "\u0301", "\u02cb": "\u0300"}
PREFIX_ACCENT_RE = re.compile(
    "([" + "".join(PREFIX_ACCENTS) + r"])\s*([A-Za-z])"
)

# Multi-char sequence repairs (applied after per-char mapping).
SEQUENCE_FIXES = [
    (re.compile(r"c\u20dd|\(c\)(?=\s*\d{4})"), "\u00a9"),   # c⃝2018 / (c) 2018 -> ©
    (re.compile(r"7!"), "\u21a6"),          # 7! -> ↦  (mapsto)
    (re.compile(r"(?<=\w)\u00ad(?=\w)"), ""),  # soft hyphen inside a word
]

CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")
ODD_SPACE_RE = re.compile(r"[\u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]")
ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200d\ufeff]")


def font_class(font_name: str) -> str:
    base = font_name.split("+")[-1]
    if MATH_FONT_RE.search(base):
        return "math"
    if TEXT_FONT_RE.search(base):
        return "text"
    return "text"


# CMEX10 is TeX's "extension" font: it holds ONLY big delimiters, radical
# extenders and fraction rules -- pure layout furniture with no textual meaning.
# Its slots decode to arbitrary ASCII ('#', '@', '('), which then get read as
# Markdown syntax and silently turn a body line into an <h1>. Drop it.
LAYOUT_ONLY_FONT_RE = re.compile(r"CMEX", re.I)

# Characters that change block meaning when they lead a line.
MD_LEAD_RE = re.compile(r"^(\s*)([#>\-+*|=]|\d+[.)]\s|`{3})")


def escape_md(text: str) -> str:
    """Neutralise Markdown block syntax accidentally produced by decoding
    artefacts, so body text can never masquerade as structure."""
    return MD_LEAD_RE.sub(lambda m: m.group(1) + "\\" + m.group(2), text)


def repair_span(text: str, font_name: str) -> str:
    """Per-span repair. Font-aware, so it must run before lines are joined."""
    if LAYOUT_ONLY_FONT_RE.search(font_name):
        return " "
    cls = font_class(font_name)
    table = GLYPH_BY_CLASS.get(cls, {})
    out = []
    for ch in text:
        if ch in LIGATURES:
            out.append(LIGATURES[ch])
        elif ch in table:
            out.append(table[ch])
        else:
            out.append(ch)
    s = "".join(out)
    s = CONTROL_RE.sub("", s)
    s = ZERO_WIDTH_RE.sub("", s)
    s = ODD_SPACE_RE.sub(" ", s)
    return s


def repair_line(text: str) -> str:
    """Line-level repair, after spans are joined."""
    def _accent(m):
        return m.group(2) + PREFIX_ACCENTS[m.group(1)]
    text = PREFIX_ACCENT_RE.sub(_accent, text)
    for pat, rep in SEQUENCE_FIXES:
        text = pat.sub(rep, text)
    return unicodedata.normalize("NFC", text)


# ===========================================================================
# DATA MODEL
# ===========================================================================

@dataclass
class Line:
    page: int                    # 0-based page index
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float                  # max span size
    fonts: tuple                 # distinct font basenames
    is_bold: bool
    is_italic: bool
    is_mono: bool
    math_ratio: float            # fraction of chars in math fonts
    isolated: bool = True        # unusual whitespace above this line
    region: dict | None = field(default=None, repr=False, compare=False)
    regime: str = "body"
    fn_num: str | None = None
    toc_group: str = "body"
    table: dict | None = field(default=None, repr=False, compare=False)
    col: int = -1
    col_left: float = 0.0
    in_toc: bool = False
    slide_end: bool = False
    style: tuple = ()            # (dominant_font, rounded_size)
    kind: str = "body"           # body|heading|caption|furniture|code|footnote
    level: int = 0               # heading level when kind == heading

    @property
    def indent(self) -> float:
        return self.x0


@dataclass
class Profile:
    body_size: float = 10.0
    body_font: str = ""
    page_w: float = 0.0
    page_h: float = 0.0
    header_band: float = 0.0     # y below this = header furniture
    footer_band: float = 0.0     # y above this = footer furniture
    heading_styles: dict = field(default_factory=dict)   # style -> level
    caption_styles: set = field(default_factory=set)
    running_heads: set = field(default_factory=set)
    text_families: set = field(default_factory=set)
    ocr_layer: bool = False
    demoted_dense: int = 0
    figure_regions: list = field(default_factory=list)
    tables: list = field(default_factory=list)
    census: Counter = field(default_factory=Counter)
    outline: dict = field(default_factory=dict)
    acronyms: set = field(default_factory=set)
    canonical_title: str = ""
    page_count: int = 0
    author_override: list = field(default_factory=list)
    doc_type: str = "book"
    doc_evidence: list = field(default_factory=list)
    doc_scores: dict = field(default_factory=dict)
    deck_meta: dict = field(default_factory=dict)
    document_meta: dict = field(default_factory=dict)
    paper_meta: dict = field(default_factory=dict)
    promoted: int = 0
    doc: object = field(default=None, repr=False)
    rotated_text: list = field(default_factory=list)
    source_name: str = ""
    fn_anchors: int = 0
    index_entries: int = 0
    line_pitch: float = 12.0
    toc_pages: set = field(default_factory=set)
    toc_truth: dict = field(default_factory=dict)
    body_left: float = 0.0


# ===========================================================================
# STAGE 1 — EXTRACT
# ===========================================================================

ROTATED_TEXT: list[str] = []


def extract_lines(doc, page_range) -> list[Line]:
    ROTATED_TEXT.clear()
    lines: list[Line] = []
    for pno in page_range:
        page = doc[pno]
        d = page.get_text("dict")
        for block in d["blocks"]:
            if block["type"] != 0:
                continue
            for ln in block["lines"]:
                # rotated text (the sideways arXiv identifier on page 1,
                # vertical figure axis labels) is never prose
                dx, dy = ln.get("dir", (1, 0))
                if abs(dy) > 0.3:
                    ROTATED_TEXT.append("".join(s["text"] for s in ln["spans"]))
                    continue
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                pieces, fonts, math_chars, total_chars = [], [], 0, 0
                prev_x1 = None
                for s in spans:
                    base = s["font"].split("+")[-1]
                    fonts.append(base)
                    txt = repair_span(s["text"], s["font"])
                    # TeX writes each font run as its own span with no space
                    # character between them, so a naive "".join() welds words
                    # together ("First-visitMCprediction"). Reinstate the space
                    # from the geometric gap between span boxes.
                    if prev_x1 is not None:
                        gap = s["bbox"][0] - prev_x1
                        if gap > s["size"] * 0.18 and pieces and \
                                not pieces[-1].endswith(" ") and \
                                not txt.startswith(" "):
                            pieces.append(" ")
                    prev_x1 = s["bbox"][2]
                    pieces.append(txt)
                    n = len(txt.strip())
                    total_chars += n
                    if font_class(base) == "math":
                        math_chars += n
                text = repair_line("".join(pieces))
                if not text.strip():
                    continue
                # dominant font = font covering the most characters
                weight = Counter()
                for s in spans:
                    weight[s["font"].split("+")[-1]] += len(s["text"].strip())
                dom = weight.most_common(1)[0][0]
                size = max(s["size"] for s in spans)
                x0, y0, x1, y1 = ln["bbox"]
                lines.append(Line(
                    page=pno, text=text, x0=x0, y0=y0, x1=x1, y1=y1,
                    size=size, fonts=tuple(sorted(set(fonts))),
                    is_bold=bool(re.search(r"(BX|Bold|Medi|Demi|Semi[Bb]old|Heavy|Black|"
                                           r"CMMIB|CMBSY|-B\b|,B\b|Bd\b|\bBold)", dom)),
                    is_italic=bool(re.search(r"(TI|Italic|Oblique)", dom)),
                    is_mono=bool(re.search(r"(CMTT|Mono|Courier)", dom)),
                    math_ratio=(math_chars / total_chars) if total_chars else 0.0,
                    style=(dom, round(size, 1)),
                ))
    # Strict reading order. LaTeX emits a section number and its title as two
    # separate blocks on the same baseline, and block order between them is not
    # guaranteed -- sorting by (page, y-band, x) makes the merge step reliable.
    lines.sort(key=lambda l: (l.page, round(l.y0 / 3.0), l.x0))
    page_w = doc[page_range[0]].rect.width if len(page_range) else 0
    if page_w:
        PP.reorder_columns(lines, page_w)
    return lines


def font_family(name: str) -> str:
    """Collapse 'CMBX12' / 'CMR10' -> 'CM', 'Times-Italic' -> 'Times'."""
    base = name.split("+")[-1]
    if base.upper().startswith("CM"):
        return "CM"
    for fam in ("Helvetica", "Times", "Arial", "Courier", "Symbol",
                "GillSans", "MSAM", "MSBM", "EUSM"):
        if base.lower().startswith(fam.lower()):
            return fam
    return re.split(r"[-,\d]", base)[0] or base


# ===========================================================================
# STAGE 2 — GLOBAL PROFILE
#   Every decision here uses the WHOLE document. This is the core fix:
#   per-page heuristics cannot tell a running head from a real heading,
#   but "appears at y=39 on 400 pages" is unambiguous.
# ===========================================================================

NUM_HEADING_RE = re.compile(r"^\d{1,2}(\.\d{1,2}){0,3}\.?$")
CAPTION_RE = re.compile(
    r"^(Figure|Fig\.|Table|Algorithm|Listing|Exercise|Example|Box|Equation)\s+"
    r"[\dIVX]+(\.[\dIVX]+)*[.:]?\s+[A-Z(\"\u201c]")


GLYPHLESS_RE = re.compile(r"GlyphLess|Invisible|NoGlyph", re.I)


def is_ocr_layer(lines: list[Line]) -> bool:
    """True when the text layer was stamped on by an OCR tool (OCRmyPDF,
    Acrobat, ABBYY) rather than authored. Such layers use ONE synthetic font
    for the whole book, so every font-based signal -- family, boldness, style
    identity -- is gone. Size survives, because OCR scales the invisible glyphs
    to the scanned image, but only as a noisy analog measurement."""
    fonts = Counter()
    for ln in lines:
        fonts[ln.style[0]] += len(ln.text)
    if not fonts:
        return False
    top, n = fonts.most_common(1)[0]
    return bool(GLYPHLESS_RE.search(top)) or n / sum(fonts.values()) > 0.97


BODY_HEADING_RATIO = 1.15   # empirical valley: >15% larger than body median


def find_size_tiers(ratios: list[float]) -> list[float]:
    """Find the natural cut points in a line-size distribution.

    Two different problems, two different methods:

    * body vs. heading -- OCR jitter smears body sizes upward and fills the
      valley, so there is no reliable gap to find. Anchor this cut at a fixed
      ratio above the body median instead.
    * heading vs. chapter-title -- these DO separate cleanly, so find the gap.

    Counts are per *line*, not per character. Weighting by characters buries
    headings (short by definition) under body mass and finds nothing.
    """
    hist = Counter(round(r / 0.05) * 0.05 for r in ratios)
    occupied = sorted(b for b, n in hist.items() if n >= 2 and b > 1.25)
    cuts = [BODY_HEADING_RATIO]
    prev = None
    for b in occupied:
        if prev is not None and b - prev > 0.12:   # >=2 empty bins == a valley
            cuts.append((prev + b) / 2)
        prev = b
    return cuts


def band_ocr_styles(lines: list[Line]) -> None:
    """Collapse a continuous OCR size smear back into discrete tiers.

    An authored PDF has a handful of exact sizes; an OCR layer has a smear
    (12.7, 12.8 ... 14.8 all body text) that shatters into ~30 bogus 'heading
    levels' if each 0.1pt step is its own style.
    """
    weighted = []
    for ln in lines:
        weighted += [ln.size] * max(1, len(ln.text))
    med = statistics.median(weighted) if weighted else 10.0
    cuts = find_size_tiers([ln.size / med for ln in lines]) if med else []
    for ln in lines:
        r = ln.size / med if med else 1.0
        tier = sum(1 for c in cuts if r >= c)
        # Synthetic but monotonic in tier, so build_profile's size-ranking
        # still orders the levels correctly.
        ln.style = ("OCR", round(med * (1 + 0.5 * tier), 1))
        ln.is_bold = tier > 0            # no bold flag survives OCR


def build_profile(lines: list[Line], doc, page_range) -> Profile:
    p = Profile()
    p.page_w = doc[page_range[0]].rect.width
    p.page_h = doc[page_range[0]].rect.height

    # An OCR text layer carries no font identity, so switch the style key from
    # (font, exact size) to a quantised relative-size band before profiling.
    p.ocr_layer = is_ocr_layer(lines)
    if p.ocr_layer:
        band_ocr_styles(lines)

    # --- body style: modal (font, size) weighted by character count ---
    char_by_style = Counter()
    for ln in lines:
        char_by_style[ln.style] += len(ln.text)
    if char_by_style:
        (p.body_font, p.body_size) = char_by_style.most_common(1)[0][0]

    # --- body left margin: modal x0 of body-styled lines ---
    body_x = [round(l.x0) for l in lines if l.style == (p.body_font, p.body_size)]
    p.body_left = statistics.mode(body_x) if body_x else 0.0

    # --- dominant font families -------------------------------------------
    # A book typeset in Computer Modern will also contain Helvetica/Times/Symbol
    # text *baked into vector figures*. Those are large and bold and look
    # exactly like headings to a naive size test. Keep only families that carry
    # a real share of the document's characters.
    fam_chars = Counter()
    for ln in lines:
        fam_chars[font_family(ln.style[0])] += len(ln.text)
    total_chars = sum(fam_chars.values()) or 1
    p.text_families = {f for f, n in fam_chars.items() if n / total_chars >= 0.02}
    p.text_families.add(font_family(p.body_font))

    by_page = defaultdict(list)
    for ln in lines:
        by_page[ln.page].append(ln)
    npages = max(1, len(by_page))

    # --- margin bands ------------------------------------------------------
    # Fixed generous fractions rather than "median of last line", which drifts
    # onto real body text on dense pages. The repetition test below does the
    # actual discrimination; the band only limits where we look.
    p.header_band = p.page_h * 0.10
    p.footer_band = p.page_h * 0.94

    # --- running heads/feet ------------------------------------------------
    # Normalise digits so "Chapter 1: Introduction 17" and "... 18" collide.
    # No minimum character length: that guard is what lets short running heads
    # like "Chapter 1: Introduction" (23 chars) survive a naive cleaner.
    band_text = Counter()
    for ln in lines:
        if not (ln.y0 <= p.header_band or ln.y1 >= p.footer_band):
            continue
        norm = re.sub(r"\d+", "#", ln.text.strip())
        # never let an equation number "(4.7)" become a running-head pattern
        if not norm or re.fullmatch(r"\(#(\.#)*\)", norm):
            continue
        band_text[norm] += 1
    thresh = max(3, int(npages * 0.02))
    p.running_heads = {t for t, c in band_text.items() if c >= thresh}

    # --- heading styles ----------------------------------------------------
    style_lines = defaultdict(list)
    for ln in lines:
        style_lines[ln.style].append(ln)

    heading_candidates = []
    for style, ls in style_lines.items():
        font, size = style
        if font_family(font) not in p.text_families:
            continue                                    # figure text
        body_ls = [l for l in ls
                   if not (l.y0 <= p.header_band or l.y1 >= p.footer_band)]
        if len(body_ls) < (2 if p.ocr_layer else 3):
            continue                                    # one-off, not a style
        bold = sum(1 for l in body_ls if l.is_bold) / len(body_ls) > 0.5
        bigger = size > p.body_size + 0.6
        if not (bold or bigger):
            continue
        if statistics.median([l.math_ratio for l in body_ls]) > 0.4:
            continue                                    # display math
        cap_hits = sum(1 for l in body_ls if CAPTION_RE.match(l.text))
        if cap_hits / len(body_ls) > 0.25:
            p.caption_styles.add(style)
            continue
        if statistics.median([len(l.text) for l in body_ls]) > 90:
            continue                                    # bold emphasis run
        if len(body_ls) > max(40, npages * 1.5):
            continue                                    # too frequent (floor for papers)
        heading_candidates.append((size, style))

    # Rank by size descending, clustering near-equal sizes into one level.
    heading_candidates.sort(key=lambda t: -t[0])
    last_size, level = None, 0
    for size, style in heading_candidates:
        if last_size is None or (last_size - size) > 0.4:
            level += 1
            last_size = size
        p.heading_styles[style] = min(level, 6)
    return p


# ===========================================================================
# STAGE 3 — CLASSIFY
# ===========================================================================

FOOTNOTE_START_RE = re.compile(r"^\s*(\d{1,3}|[*\u2020\u2021])\s+[A-Z(]")
PAGE_NUM_ONLY_RE = re.compile(r"^\s*(\d{1,4}|[ivxlcdmIVXLCDM]{1,7})\s*$")
ROMAN_ONLY_RE = re.compile(r"^[ivxlcdm]+$", re.I)
MATH_TOKEN_RE = re.compile(
    r"[=<>\u2264\u2265\u00b1\u00b7\u00d7\u2192\u21a6\u2208\u2211\u220f"
    r"\u221a\u2202\u2207\u03c0\u03b8\u03b3\u03b1\u03b2\u03bb\u03bc|^_]")


def heading_text_ok(text: str) -> bool:
    """A style being bold+large is necessary but not sufficient. In a maths
    book, bold variable labels inside equations and figures share the heading
    style exactly ('xt', 'w3', 'w>x'). Gate on the *shape of the text*: real
    headings are made of real words."""
    t = text.strip()
    if NUM_HEADING_RE.match(t):
        return True                                   # bare "2.1", merged later
    if re.match(r"^(Chapter|Part|Appendix)\b", t, re.I):
        return True
    if len(t) < 3 or ROMAN_ONLY_RE.match(t):
        return False
    # A list bullet or a full sentence is never a heading, however it is sized.
    # OCR size jitter lifts random body lines into the heading band, so these
    # shape tests are the only thing standing between you and 40 fake headings.
    if t[0] in "\u2022\u25aa\u25cf\u00b7\u2023-\u2013\u2014":
        return False
    if t.endswith((".", ",", ";")) and len(t.split()) > 6:
        return False
    if len(t) > 80:
        return False
    if MATH_TOKEN_RE.search(t):
        return False
    # Display equations are set large and bold too. Prose uses a narrow
    # punctuation vocabulary; formulas use parentheses, sub/superscripts and
    # operators. Anything outside the prose set counts against the line.
    allowed = sum(1 for c in t if c.isalnum() or c in " -:,'.&/*?!")
    if allowed / len(t) < 0.90:
        return False
    words = t.split()
    real = [w for w in words if len(re.sub(r"[^A-Za-z]", "", w)) >= 3]
    if not real or len(real) / len(words) < 0.5:
        return False
    # a one-word heading is Capitalised or ALL CAPS; "wDX" / "xtx" are math
    if len(words) == 1 and not re.fullmatch(r"[A-Z][a-z'’\-]+|[A-Z][A-Z'’\-]+", real[0]):
        return False
    return True


LEADER_RE = re.compile(r"(?:\.\s?){4,}")
TOC_ENTRY_RE = re.compile(
    r"^\s*(?P<num>\d{1,2}(?:\.\d{1,2})*)?\s*"
    r"(?P<title>.*?)\s*(?:\.\s?){3,}\s*(?P<page>\d{1,4})?\s*$")


def find_toc_pages(lines: list[Line], prof: Profile) -> set[int]:
    """A printed contents page is dense with dot-leader lines. Detecting it
    matters twice over: the raw text is unusable as prose, AND -- when the PDF
    carries no outline, as here -- it is the only authored statement of the
    document's true hierarchy."""
    by_page = defaultdict(lambda: [0, 0])
    for ln in lines:
        rec = by_page[ln.page]
        rec[1] += 1
        if LEADER_RE.search(ln.text):
            rec[0] += 1
    # ratio catches dense contents pages; the absolute count catches the
    # first one, where a large heading and un-leadered part titles dilute it
    return {p for p, (lead, tot) in by_page.items()
            if tot >= 4 and (lead / tot >= 0.25 or lead >= 6)}


def parse_printed_toc(lines: list[Line], toc_pages: set[int]) -> dict[str, int]:
    """Return {normalised title -> level} harvested from the contents pages.
    Level comes from the depth of the section number ('4.1' -> 2), which is
    authored fact rather than a guess from font size."""
    truth: dict[str, int] = {}
    buf = ""
    for ln in sorted((l for l in lines if l.page in toc_pages),
                     key=lambda l: (l.page, l.y0, l.x0)):
        buf = (buf + " " + ln.text).strip() if buf else ln.text.strip()
        if not LEADER_RE.search(buf):
            if len(buf) > 200:
                buf = ""
            continue
        m = TOC_ENTRY_RE.match(buf)
        buf = ""
        if not m:
            continue
        title = re.sub(r"\s+", " ", (m.group("title") or "")).strip(" .")
        if not title or len(title) < 3:
            continue
        num = m.group("num")
        lvl = (num.count(".") + 1) if num else 1
        truth[norm_title(title)] = min(lvl, 6)
    return truth


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]", "", t.lower())


def mark_isolation(lines: list[Line], prof: Profile) -> None:
    """Flag lines that have unusual whitespace above them.

    In an OCR layer, size is a noisy analog measurement, so a normal body line
    occasionally measures large enough to look like a heading. Typography gives
    an independent signal that jitter cannot fake: a real heading has air above
    it, while a mis-measured body line sits at normal line pitch inside a
    paragraph. Requiring both size AND isolation removes that whole error class.
    """
    pitches = []
    prev = None
    for ln in lines:
        if prev is not None and ln.page == prev.page:
            gap = ln.y0 - prev.y0
            if 0 < gap < prof.page_h * 0.2:
                pitches.append(gap)
        prev = ln
    pitch = statistics.median(pitches) if pitches else 12.0
    prof.line_pitch = pitch

    prev = None
    for ln in lines:
        if prev is None or ln.page != prev.page or ln.col != prev.col:
            ln.isolated = True            # first line on a page or column
        else:
            ln.isolated = (ln.y0 - prev.y0) > pitch * 1.35
        prev = ln


BACK_MATTER_RE = re.compile(
    r"^(Index|Subject Index|Author Index|Name Index|References|Bibliography|"
    r"Glossary|Notes|Endnotes|Acknowledg(e)?ments|About the Authors?)$", re.I)
INDEX_ENTRY_RE = re.compile(r"^[a-z(].{2,}|^.{2,60},\s+[A-Z(]")



def demote_dense_headings(lines: list[Line], window: int = 15, limit: int = 4) -> int:
    """Headings are sparse: a chapter opener has two or three, an ordinary
    page has at most a couple. Any region with more than `limit` heading
    candidates in `window` lines is an index, glossary, bibliography or list
    -- short isolated lines that share every *local* property of a heading
    and differ only in how many of them there are. Demote the whole cluster.
    A Chapter/Part line is exempt: it is never the thing that is dense."""
    # Bare section numbers ("5.7") are cross-reference labels that get dropped
    # later, and run-in labels ("Example 6.1") are dense by design; neither is
    # evidence of an index. Only real heading text counts toward density.
    def labelled(l):
        t = OL.tokenize(l.text)
        return t.num is not None and t.label is not None \
            and t.label.lower() not in OL.SPINE_LABELS
    idx = [i for i, l in enumerate(lines) if l.kind == "heading"
           and not NUM_HEADING_RE.match(l.text.strip())
           and not labelled(l)]
    demoted = 0
    for k, i in enumerate(idx):
        t = lines[i].text.strip()
        if re.match(r"^(Part|Chapter|Appendix)\b", t, re.I):
            continue
        # the line directly under a Chapter heading is its title
        if i > 0 and lines[i - 1].kind == "heading" and \
                re.match(r"^(Part|Chapter)\s+[\dIVX]+$", lines[i - 1].text.strip(), re.I):
            continue
        near = sum(1 for j in idx if abs(j - i) <= window)
        if near > limit:
            lines[i].kind = "body"
            lines[i].level = 0
            demoted += 1
    return demoted


def _index_has_hanging_indent(lines: list[Line], prof: Profile) -> bool:
    """True if the index marks wrapped entries with a second x-column."""
    xs, started = [], False
    for ln in lines:
        t = ln.text.strip()
        if ln.kind == "heading" and re.search(r"Index$", t, re.I):
            started = True
            continue
        if started and ln.kind == "body" and len(t) < 120:
            xs.append(round(ln.x0))
    if len(xs) < 30:
        return False
    base = min(xs)
    indented = sum(1 for x in xs if x > base + 8)
    return 0.15 < indented / len(xs) < 0.85


def mark_index_regime(lines: list[Line], prof: Profile) -> int:
    """After a back-matter heading such as 'Index', short lines are entries,
    not paragraphs. Two index typographies exist: dense scholarly indexes
    mark a wrapped entry by hanging indent; reflowed ebooks by extra space
    above each new entry. Decide per index which signal is present."""
    in_index, n = False, 0
    hanging = _index_has_hanging_indent(lines, prof)
    base_x = None
    for ln in lines:
        t = ln.text.strip()
        if ln.kind == "heading" and BACK_MATTER_RE.match(t):
            ln.level = 1
            in_index = bool(re.search(r"Index$", t, re.I))
            base_x = None
            continue
        if ln.kind == "heading" and re.match(
                r"^(Part|Chapter|Appendix|About the Authors?|Colophon|"
                r"Acknowledg(e)?ments|Afterword|Epilogue)\b", t, re.I):
            in_index = False
            ln.level = 1
            continue
        if not in_index:
            continue
        if ln.kind == "heading":                 # OCR-jittered entry
            ln.kind, ln.level = "index_entry", 0
            n += 1
        elif ln.kind == "body" and len(t) < 120:
            if re.fullmatch(r"[A-Z]", t):
                ln.kind = "index_letter"        # alphabetic divider
                continue
            if base_x is None:
                base_x = ln.x0
            base_x = min(base_x, ln.x0)
            is_entry = (ln.x0 <= base_x + 7) if hanging else ln.isolated
            ln.kind = "index_entry" if is_entry else "index_cont"
            n += ln.kind == "index_entry"
    return n


FIGURE_TEXT_RATIO = 0.72   # lines this far below body size are diagram labels


def mark_figure_text(lines: list[Line], prof: Profile) -> list[dict]:
    """Detect text that lives *inside* figures and group it into regions.

    Diagram labels come through an OCR layer as dozens of tiny scattered
    lines -- 2-5pt against a 13pt body, at random x positions, with near-zero
    line pitch. Read as prose they are a word salad ("CONFIRMED CONFIRMED
    DISCONFIRMED OTHERS OTHERS"). No text-only method can rebuild the diagram;
    what CAN be done reliably is to recognise the region, keep the labels out
    of the paragraph flow, and hand the pixels to something that can see.

    Returns a list of regions: {page, bbox, lines, caption}.
    """
    regions: list[dict] = []
    cur: dict | None = None
    for i, ln in enumerate(lines):
        tiny = ln.size < prof.body_size * FIGURE_TEXT_RATIO
        off_margin = ln.x0 > prof.body_left + 12
        if tiny and ln.kind in ("body", "heading", "caption", "code") and off_margin:
            ln.kind = "figure_text"
            if cur and cur["page"] == ln.page and ln.y0 - cur["y1"] < prof.line_pitch * 2:
                cur["lines"].append(ln)
                cur["x0"] = min(cur["x0"], ln.x0); cur["x1"] = max(cur["x1"], ln.x1)
                cur["y0"] = min(cur["y0"], ln.y0); cur["y1"] = max(cur["y1"], ln.y1)
            else:
                cur = {"page": ln.page, "x0": ln.x0, "y0": ln.y0, "x1": ln.x1,
                       "y1": ln.y1, "lines": [ln], "caption": None, "first": i}
                regions.append(cur)
        elif cur is not None and ln.page == cur["page"] and ln.kind == "caption" \
                and ln.y0 >= cur["y1"] and ln.y0 - cur["y1"] < prof.line_pitch * 3:
            cur["caption"] = ln            # caption directly below the region
            cur = None
        elif cur is not None and (ln.page != cur["page"] or ln.kind == "body"):
            cur = None
    # Only keep regions with enough labels to be a diagram, not a stray glyph.
    keep = [r for r in regions if len(r["lines"]) >= 3]
    for r in regions:
        if r not in keep:
            for ln in r["lines"]:
                ln.kind = "body"
    for r in keep:
        for ln in r["lines"]:
            ln.region = r
    return keep


JUNK_LABEL_RE = re.compile(r"^[^A-Za-z]*$|^.{1,2}$")


def region_labels(region: dict) -> list[str]:
    """Deduplicated labels in reading order, with obvious OCR debris removed.
    Kept in the output so the diagram's vocabulary stays searchable."""
    seen, out = set(), []
    for ln in region["lines"]:
        t = re.sub(r"\s+", " ", ln.text).strip()
        if JUNK_LABEL_RE.match(t):
            continue
        vowels = sum(c in "aeiouAEIOU" for c in t)
        if len(t) > 5 and vowels / len(t) < 0.12:     # "NAPARLS", "INDESIRARI"
            continue
        k = t.lower()
        if k not in seen:
            seen.add(k); out.append(t)
    return out


def render_region(doc, region: dict, out_dir: Path, dpi: int = 200,
                  pad: float = 18.0) -> Path:
    """Crop the figure from the page raster. For an OCR'd scan the whole page
    is one image, so this is the only way to get the diagram itself out."""
    page = doc[region["page"]]
    clip = pymupdf.Rect(region["x0"] - pad, region["y0"] - pad,
                        region["x1"] + pad, region["y1"] + pad) & page.rect
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"fig-p{region['page'] + 1:03d}-{int(region['y0']):03d}.png"
    page.get_pixmap(dpi=dpi, clip=clip).save(str(path))
    return path


def describe_region_vlm(image_path: Path, caption: str, model: str,
                        host: str = "http://localhost:11434") -> str | None:
    """Ask a local vision model to transcribe the diagram as structure.

    Returns Markdown (nested list / Mermaid) or None on any failure. Kept
    deliberately isolated so a missing Ollama never breaks a conversion.
    """
    try:
        import base64, json as _json, urllib.request
        img = base64.b64encode(image_path.read_bytes()).decode()
        prompt = (
            "This is a diagram from a book. "
            f"Caption: {caption or '(none)'}\n"
            "Transcribe its structure faithfully as Markdown. If it is a tree "
            "or hierarchy, use a nested bullet list. If it is a flowchart, use a "
            "Mermaid code block. If it is a table, use a Markdown table. "
            "Reproduce label text exactly; do not summarise or interpret.")
        body = _json.dumps({"model": model, "prompt": prompt, "images": [img],
                            "stream": False, "options": {"temperature": 0}}).encode()
        req = urllib.request.Request(f"{host}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            return _json.loads(r.read())["response"].strip() or None
    except Exception:
        return None


TAIL_OK_RE = re.compile(r"[.!?\u2026\"\u201d')\]]\s*$")


def fold_caption_wraps(lines: list[Line], prof: Profile) -> None:
    """A caption that wraps leaves its tail as a body line at normal pitch
    directly beneath it. Fold up to three such lines back into the caption."""
    i = 0
    while i < len(lines) - 1:
        a = lines[i]
        if a.kind != "caption":
            i += 1
            continue
        j = i + 1
        while j < len(lines) and j - i <= 3:
            b = lines[j]
            close = (b.page == a.page and not b.isolated
                     and 0 < b.y0 - a.y1 < prof.line_pitch)
            if b.kind == "body" and close and \
                    (b.text[:1].islower() or not TAIL_OK_RE.search(a.text)):
                a.text = a.text.rstrip() + " " + b.text.strip()
                a.y1 = b.y1
                b.kind = "consumed"
                j += 1
            else:
                break
        i = j


# ===========================================================================
# TABLES — rows of short cells aligned to shared columns
# ===========================================================================
def mark_tables(lines: list[Line], prof: Profile) -> list[dict]:
    """Detect tables from geometry alone: >=3 rows at regular pitch whose
    short cells sit on >=3 shared x-columns. Cells become kind='table_cell'
    carrying (table, row, col); the assembler renders a Markdown table at the
    first cell. Cells that fall on no column -- labels of a diagram printed
    beside the table -- are left alone, and a row made only of such cells is
    skipped rather than treated as the end of the table.

    Why geometry and not text: a table's cells read as one-word paragraphs
    ('high', 'search', 'high'), exactly the pattern a reflow pass then either
    strands as fragments or wrongly glues into a sentence. Only the x/y grid
    distinguishes them from prose."""
    tables: list[dict] = []
    by_page: dict[int, list[Line]] = {}
    # Diagram labels also line up in columns, but they are typeset far
    # smaller than any real table (0.3x body vs >=0.6x). Size is the tell.
    # key by (page, column): on a two-column page the other column's prose
    # shares baselines with the table and would break every run
    for l in lines:
        if l.kind in ("body", "code", "figure_text") and l.size >= prof.body_size * 0.55:
            by_page.setdefault((l.page, l.col), []).append(l)
    for (page, _col), pl in by_page.items():
        pl.sort(key=lambda l: (l.y0, l.x0))
        rows: list[list[Line]] = []
        for l in pl:
            if rows and abs(l.y0 - statistics.median(r.y0 for r in rows[-1])) <= max(3.5, l.size * 0.45):
                rows[-1].append(l)
            else:
                rows.append([l])
        rows = [sorted(r, key=lambda l: l.x0) for r in rows]
        is_cell = lambda l: 0 < len(l.text.split()) <= 6
        # a row with any long line is prose: it terminates a table run
        breaker = [any(not is_cell(l) for l in r) for r in rows]
        i = 0
        while i < len(rows):
            if breaker[i] or len(rows[i]) < 2:
                i += 1; continue
            j = i
            while j + 1 < len(rows) and not breaker[j + 1]:
                gap = statistics.median(r.y0 for r in rows[j + 1]) - statistics.median(r.y0 for r in rows[j])
                if gap > max(r.size for r in rows[j]) * 2.5:
                    break
                j += 1
            run = rows[i:j + 1]
            i = j + 1
            if sum(1 for r in run if len(r) >= 2) < 3:
                continue
            # ---- columns: a column is a cluster of LEFT edges (left-aligned
            # text) or of RIGHT edges (right-aligned labels, numbers). Cluster
            # both, keep well-populated ones, drop right-edge columns that
            # merely restate a left-edge column.
            size = statistics.median(l.size for r in run for l in r)
            tol = max(6.0, size * 1.2)
            multi = [r for r in run if len(r) >= 2]

            def clusters_of(edge):
                xs = sorted(getattr(l, edge) for r in run for l in r)
                out: list[list[float]] = []
                for x in xs:
                    if out and x - out[-1][-1] <= tol:
                        out[-1].append(x)
                    else:
                        out.append([x])
                res = []
                for c in out:
                    cx = statistics.median(c)
                    hits = sum(1 for r in multi if any(abs(getattr(l, edge) - cx) <= tol for l in r))
                    if hits >= max(2, 0.4 * len(multi)):
                        res.append((edge, cx))
                return res

            # greedy by coverage: a right-aligned label column is ONE column on
            # its right edge, not two or three fragments on its left edges
            cands = []
            for e, cx in clusters_of("x0") + clusters_of("x1"):
                mem = [l for r in run for l in r if abs(getattr(l, e) - cx) <= tol]
                cands.append((len(mem), e, cx, mem))
            cands.sort(key=lambda c: -c[0])
            assigned: set[int] = set()
            cols = []
            for nmem, e, cx, mem in cands:
                if sum(1 for l in mem if id(l) in assigned) >= 0.5 * len(mem):
                    continue
                cols.append((e, cx))
                assigned.update(id(l) for l in mem)
            cols.sort(key=lambda c: c[1] if c[0] == "x0" else c[1] - size * 3)
            if len(cols) < 3:
                continue

            def col_of(l):
                best, bd = None, None
                for k, (e, cx) in enumerate(cols):
                    dd = abs((l.x0 if e == "x0" else l.x1) - cx)
                    if dd <= tol and (bd is None or dd < bd):
                        best, bd = k, dd
                return best

            # ---- assign cells; rows with no member cell are skipped, not split on
            grid: list[dict[int, str]] = []
            members: list[Line] = []
            for r in run:
                cells: dict[int, str] = {}
                for l in r:
                    k = col_of(l)
                    if k is not None:
                        cells[k] = (cells[k] + " " + l.text.strip()) if k in cells else l.text.strip()
                        members.append(l)
                if cells:
                    grid.append(cells)
            while grid and len(grid[0]) < 2:
                grid.pop(0)
            while grid and len(grid[-1]) < 2:
                grid.pop()
            if len(grid) < 3 or sum(1 for c in grid if len(c) >= 2) < 0.7 * len(grid):
                continue
            # Aligned fragments of a multi-line equation or a pseudocode box
            # pass every geometric test. They give themselves away by content:
            # operators and equation numbers in the cells, box borders, sparse
            # fill, cells that are bare symbols.
            cells = [c for r in grid for c in r.values()]
            fill = len(cells) / (len(grid) * len(cols))
            mathy = sum(1 for c in cells if MATH_TOKEN_RE.search(c)
                        or re.fullmatch(r"\(\d+\.\d+\)", c.strip())
                        or c.strip().startswith(("|", "if ", "If ", "Loop", "Initialize")))
            wordy = sum(1 for c in cells if re.search(r"[A-Za-z]{3,}", c))
            header = list(grid[0].values())
            header_wordy = sum(1 for c in header if re.search(r"[A-Za-z]{2,}", c)) / max(1, len(header))
            # a results table is mostly numbers; its header row is words
            wordy_ok = wordy / len(cells) >= 0.3 or (header_wordy >= 0.5 and wordy / len(cells) >= 0.15)
            if fill < 0.7 or mathy / len(cells) > 0.25 or not wordy_ok:
                continue
            t = {"page": page, "cols": len(cols), "grid": grid, "first": None}
            keep = {id(l) for r in run for l in r if col_of(l) is not None}
            first = None
            for l in pl:
                if id(l) in keep:
                    l.kind = "table_cell"; l.table = t
                    first = first or l
            t["first"] = first
            tables.append(t)
    return tables


PANEL_RE = re.compile(r"^\(?[a-h]\)\s+\S")


def mark_panel_labels(lines: list[Line]) -> None:
    """Figure sub-panel labels ('a) Narrow generalization' / 'b) Broad
    generalization') share one baseline but are interleaved in reading order
    with the panels' own short titles. Cluster the labels per page by
    baseline, join each cluster into one italic line, and drop the short
    fragments that sat between them."""
    by_page: dict[int, list[Line]] = {}
    for l in lines:
        if l.kind == "body" and PANEL_RE.match(l.text.strip()):
            by_page.setdefault(l.page, []).append(l)
    for page, labels in by_page.items():
        labels.sort(key=lambda l: (l.y0, l.x0))
        groups: list[list[Line]] = []
        for l in labels:
            tol = max(6, l.size * 0.8)
            if groups and abs(l.y0 - groups[-1][0].y0) <= tol:
                groups[-1].append(l)
            else:
                groups.append([l])
        for g in groups:
            if len(g) < 2:
                continue
            g.sort(key=lambda l: l.x0)
            head = g[0]
            tol = max(6, head.size * 0.8)
            head.text = " \u00b7 ".join(l.text.strip() for l in g)
            head.kind = "panel_labels"
            ids = {id(l) for l in g}
            for l in lines:
                if l.page == page and id(l) not in ids and l.kind == "body" \
                        and abs(l.y0 - head.y0) <= tol and len(l.text.split()) <= 3:
                    l.kind = "consumed"
            for l in g[1:]:
                l.kind = "consumed"


def render_table(t: dict) -> list[str]:
    n = t["cols"]
    def row(cells):
        return "| " + " | ".join(cells.get(k, "").replace("|", "\\|") for k in range(n)) + " |"
    out = [row(t["grid"][0]), "|" + "---|" * n]
    out += [row(c) for c in t["grid"][1:]]
    return out


def classify(lines: list[Line], prof: Profile) -> None:
    mark_isolation(lines, prof)
    toc_pages = find_toc_pages(lines, prof)
    truth = parse_printed_toc(lines, toc_pages)
    prof.toc_pages = toc_pages
    prof.toc_truth = truth

    prev_head: Line | None = None
    for ln in lines:
        # The printed contents pages are replaced by a generated TOC, so their
        # raw dot-leader text is dropped rather than reflowed into prose.
        if ln.page in toc_pages:
            ln.kind = "furniture"
            continue

        # A wrapped heading's second line sits at normal line pitch, so the
        # isolation test alone would demote it to body and orphan the first
        # line. Continuation is evidence too: same style, same page, directly
        # below an accepted heading.
        continues = (prev_head is not None and ln.page == prev_head.page
                     and ln.style == prev_head.style
                     and 0 < ln.y0 - prev_head.y0 < prof.line_pitch * 1.6)

        norm = re.sub(r"\d+", "#", ln.text.strip())

        # --- furniture: margin band + (repeated OR bare folio) ---
        in_header = ln.y0 <= prof.header_band
        in_footer = ln.y1 >= prof.footer_band
        if in_header or in_footer:
            if norm in prof.running_heads or PAGE_NUM_ONLY_RE.match(ln.text):
                ln.kind = "furniture"
                continue

        if ln.style in prof.caption_styles or CAPTION_RE.match(ln.text):
            ln.kind = "caption"
            continue

        # the title beside an already-accepted section number ("6.3" | "Optimality
        # of TD(0)", "12.2" | "TD(λ)") only needs to be mostly letters: the
        # number has already established that this baseline is a heading
        titled = (prev_head is not None and prev_head.page == ln.page
                  and NUM_HEADING_RE.match(prev_head.text.strip())
                  and abs(ln.y0 - prev_head.y0) < 3 and ln.x0 > prev_head.x1 - 2)
        letters = sum(c.isalpha() for c in ln.text)
        lenient_ok = titled and letters >= 2 and letters / max(1, len(ln.text.strip())) >= 0.5
        if ln.style in prof.heading_styles and (heading_text_ok(ln.text) or lenient_ok) \
                and (ln.math_ratio < 0.35 or lenient_ok) \
                and (ln.isolated or continues or not prof.ocr_layer):
            ln.kind = "heading"
            ln.level = prof.heading_styles[ln.style]
            # The authored contents page beats font-size ranking wherever the
            # two disagree -- size is a proxy, the TOC is the actual hierarchy.
            key = norm_title(re.sub(r"^\d+(\.\d+)*\s*", "", ln.text.strip()))
            if key in prof.toc_truth:
                ln.level = prof.toc_truth[key]
            # Parts and chapters always outrank whatever the ranking said.
            if re.match(r"^(Part|Chapter)\b", ln.text.strip(), re.I):
                ln.level = 1
            prev_head = ln
            continue
        prev_head = None

        if ln.is_mono:
            ln.kind = "code"
            continue

        if ln.size < prof.body_size - 0.8 and FOOTNOTE_START_RE.match(ln.text):
            ln.kind = "footnote"
            continue

        ln.kind = "body"

    fold_caption_wraps(lines, prof)
    if prof.doc_type == "book":
        prof.demoted_dense = demote_dense_headings(lines)
        prof.index_entries = mark_index_regime(lines, prof)
    prof.tables = mark_tables(lines, prof)          # tables claim cells first
    prof.figure_regions = mark_figure_text(lines, prof)
    mark_panel_labels(lines)
    if prof.doc_type == "paper":
        prof.paper_meta = PP.label_paper_front(lines, prof)
        prof.promoted = PP.promote_paper_headings(lines, prof)
    elif prof.doc_type == "deck":
        prof.deck_meta = DK.label_deck(lines, prof)
    elif prof.doc_type == "document":
        prof.document_meta = DD.label_document_front(lines, prof)
    if prof.doc_type != "deck":
        R.assign_regimes(lines, prof)
    else:
        prof.census = Counter(l.kind for l in lines)
    prof.acronyms = R.learn_acronyms(lines)
    # canonical title = the longest title-page title; all other copies of it
    # anywhere in the front matter are duplicates
    pages = {}
    for l in lines:
        if l.kind == "title_line":
            pages.setdefault(l.page, []).append(l.text.strip())
    META_LINE = re.compile(r"(edition|press|publish|university|©|\bby\b|volume)", re.I)
    def _title_of(v):
        keep = []
        for t in v:
            if META_LINE.search(t):
                break
            keep.append(t)
        return " ".join(keep)
    best = max((_title_of(v) for v in pages.values()), key=len, default="")
    if prof.doc_type == "paper" and prof.paper_meta.get("title"):
        best = prof.paper_meta["title"]
    elif prof.doc_type == "deck" and prof.deck_meta.get("title"):
        best = prof.deck_meta["title"]
    elif prof.doc_type == "document" and prof.document_meta.get("title"):
        best = prof.document_meta["title"]
    prof.canonical_title = R.smart_case(best, prof.acronyms)
    # demote the title lines we cut (edition, publisher) to metadata
    for pl in pages.values():
        for l in lines:
            if l.kind == "title_line" and META_LINE.search(l.text):
                l.kind = "title_meta"
    key = re.sub(r"[^a-z0-9]", "", prof.canonical_title.lower())
    if prof.doc_type == "book":
        R.label_front_matter(lines, prof, key)
        R.tighten_copyright(lines)
    prof.fn_anchors = R.link_footnote_anchors(lines)
    prof.census = Counter(l.kind for l in lines)


# ===========================================================================
# STAGE 4 — ASSEMBLE
# ===========================================================================

def merge_split_headings(lines: list[Line]) -> list[Line]:
    """LaTeX emits '2.1' and 'A k-armed Bandit Problem' as two lines at the
    same y. Join them. Also joins 'Chapter 2' + 'Multi-armed Bandits'."""
    out: list[Line] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        if cur.kind == "heading" and i + 1 < len(lines):
            nxt = lines[i + 1]
            same_row = (nxt.page == cur.page and abs(nxt.y0 - cur.y0) < 3.0
                        and nxt.kind == "heading")
            if same_row and NUM_HEADING_RE.match(cur.text.strip()):
                cur.text = f"{cur.text.strip().rstrip('.')} {nxt.text.strip()}"
                cur.level = min(cur.level, nxt.level)
                out.append(cur); i += 2; continue
            if same_row and nxt.style == cur.style and nxt.x0 > cur.x1 - 2:
                # a labelled heading set as two spans: "Example 6.2" | "Random Walk"
                cur.text = f"{cur.text.strip()} {nxt.text.strip()}"
                cur.x1 = nxt.x1
                lines[i + 1] = cur
                i += 1
                continue
            # "Chapter 2" on its own line, title on the next line below
            stacked = (nxt.page == cur.page and 0 < nxt.y0 - cur.y0 < 70
                       and nxt.kind == "heading")
            if stacked and re.match(r"^(Chapter|Part|Appendix)\s+[\dIVX]+$",
                                    cur.text.strip(), re.I):
                cur.text = f"{cur.text.strip()}: {nxt.text.strip()}"
                cur.level = 1
                cur.y1 = nxt.y1
                cur.style = nxt.style      # so wrapped title lines match
                out.append(cur); i += 2
                # fall through to the wrap-absorption loop below
                i = _absorb_wraps(lines, i, out[-1])
                continue
        out.append(cur); i += 1
        i = _absorb_wraps(lines, i, out[-1]) if out[-1].kind == "heading" else i
    # A heading that is still nothing but its own number never found a title.
    # Emitting "### 4.2" as a heading is worse than dropping it.
    out = [l for l in out
           if not (l.kind == "heading" and NUM_HEADING_RE.match(l.text.strip()))]
    if _PROF.doc_type != "deck":
        _PROF.outline = OL.apply_outline(out, _PROF)
    return assign_toc_groups(out)


NUMBERED_HEAD_RE = re.compile(r"^(\d{1,2}(?:\.\d{1,2})*)\.?\s+\S")


def assign_toc_groups(lines: list[Line]) -> list[Line]:
    """Levels and TOC membership come from the outline tree; this only tags
    front / body / back for the contents grouping."""
    first_ch = next((i for i, l in enumerate(lines) if l.kind == "heading"
                     and re.match(r"^(Part|Chapter)\b", l.text.strip(), re.I)), len(lines))
    group = "front"
    for i, l in enumerate(lines):
        if l.kind != "heading":
            continue
        t = l.text.strip()
        if l.level == 1:
            if BACK_MATTER_RE.match(t) or re.match(r"^Appendix", t, re.I):
                group = "back"
            elif re.match(r"^(Part|Chapter)\b", t, re.I) or i >= first_ch:
                group = "body"
            else:
                group = "front"
        l.toc_group = group
    return lines


_PITCH = 12.0   # set from Profile.line_pitch before assembly
_PROF = None    # set before assembly


DANGLING_RE = re.compile(r"\b(of|the|a|an|and|or|for|to|in|on|with|from|at|by)$", re.I)


def _absorb_wraps(lines: list[Line], i: int, head: Line) -> int:
    """A long title wraps onto a second line at the same style ('Chapter 3:
    Finite Markov Decision' / 'Processes'). Pull it back in. A heading that
    ends on a dangling function word ('...a Theory of the') and is followed by
    a same-style heading at the top of the NEXT page wrapped across a page
    break; take that too."""
    while i < len(lines):
        nxt = lines[i]
        same_page = (nxt.page == head.page
                     and -2 < nxt.y0 - head.y1 < max(nxt.size * 0.9, _PITCH * 0.9))
        cross_page = (nxt.page == head.page + 1
                      and DANGLING_RE.search(head.text.strip())
                      and nxt.y0 < _PITCH * 4)
        if (nxt.kind == "heading" and nxt.style == head.style
                and (same_page or cross_page)
                and not NUM_HEADING_RE.match(nxt.text.strip())):
            joiner = "" if head.text.rstrip().endswith("-") else " "
            head.text = head.text.rstrip() + joiner + nxt.text.strip()
            head.y1 = nxt.y1
            head.page = nxt.page
            i += 1
            continue
        break
    return i


DEHYPH_RE = re.compile(r"([A-Za-z]{2,})-$")
SENT_END = tuple(".!?:;\u201d\u2019\")]}")


def looks_continued(prev: str, nxt: str) -> bool:
    prev, nxt = prev.rstrip(), nxt.lstrip()
    if not prev or not nxt:
        return False
    if prev.endswith("-"):
        return True
    if prev[-1] in SENT_END:
        return False
    # a new sentence starting with a capital after a full stop is NOT continued;
    # but a lowercase or math start almost always is.
    return nxt[0].islower() or nxt[0].isdigit() or not nxt[0].isalpha()


def reflow(paragraph_lines: list[str]) -> str:
    buf = ""
    for raw in paragraph_lines:
        cur = raw.strip()
        if not buf:
            buf = cur
            continue
        m = DEHYPH_RE.search(buf)
        if m and cur and cur[0].islower():
            buf = buf[: m.start()] + m.group(1) + cur      # de-hyphenate
        else:
            buf = buf + " " + cur
    return re.sub(r"\s+", " ", buf).strip()


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-")


def assemble(lines: list[Line], prof: Profile, *, make_toc=True,
             math_delims=False, doc=None, figure_dir: Path | None = None,
             figure_vlm: str | None = None) -> str:
    global _PITCH, _PROF
    _PITCH = prof.line_pitch
    _PROF = prof
    lines = [l for l in lines if l.kind != "furniture"]
    lines = merge_split_headings(lines)

    out: list[str] = []
    toc: list[tuple[int, str]] = []
    para: list[str] = []
    code: list[str] = []
    prev: Line | None = None

    def flush_para():
        nonlocal para
        if para:
            out.append(reflow(para))
            out.append("")
            para = []

    def flush_code():
        nonlocal code
        if code:
            out.append("```")
            out.extend(code)
            out.append("```")
            out.append("")
            code = []

    title_buf: list = []
    pub_buf: list = []
    fn_buf: list = []
    seen_titles: set = set()
    front_labels: set = set()
    idx_of = {id(l): i for i, l in enumerate(lines)}

    def lines_iter_peek(l):
        i = idx_of.get(id(l))
        return lines[i + 1] if i is not None and i + 1 < len(lines) else None

    meta_seen: set = set()
    all_meta = [R.smart_case(l.text.strip(), prof.acronyms)
                for l in lines if l.kind == "title_meta"]

    def _flush_title():
        nonlocal title_buf
        meta = all_meta if "TITLE" not in seen_titles else []
        title_buf = []
        if "TITLE" not in seen_titles:
            seen_titles.add("TITLE")
            out.append(f"# {prof.canonical_title}")
        # affiliations and repeated names add nothing; keep short new lines
        fresh = []
        for m in meta:
            m = re.sub(r"^[\s\-–—·•]+|[\s\-–—·•]+$", "", m)     # decorative dashes
            k = re.sub(r"[^a-z]", "", m.lower())
            if not k or len(m) >= 70 or re.search(r"University|Institute|College|Press$", m):
                continue
            if fresh and re.match(r"^(and|&)\s", m, re.I):        # "And Allan Collins"
                fresh[-1] = fresh[-1] + " " + m[0].lower() + m[1:]
                meta_seen.add(k); continue
            if any(k in x or x in k for x in meta_seen):           # subsumed
                continue
            meta_seen.add(k); fresh.append(m)
        for m in fresh[:6]:
            out.append(f"*{m}*  ")
        if fresh or "TITLE" in seen_titles:
            out.append("")

    def _flush_pub():
        nonlocal pub_buf
        items, seenp = [], set()
        for t in pub_buf:
            t = R.smart_case(t, prof.acronyms)
            k = re.sub(r"[^a-z0-9]", "", t.lower())
            if len(t) > 3 and k not in seenp:
                seenp.add(k); items.append(t)
        pub_buf = []
        if not items:
            return
        out.append("## Publication details")
        out.append("")
        out.extend(f"- {t}" for t in items[:12])
        out.append("")

    def flush_footnotes():
        nonlocal fn_buf
        if fn_buf:
            out.append("")
            out.extend(f"[^{n}]: {t}" for n, t in fn_buf)
            out.append("")
            fn_buf = []

    for ln in lines:
        raw = ln.text.strip()
        if ln.kind not in ("code", "figure_text"):
            raw = R.smart_case(raw, prof.acronyms)
        text = escape_md(raw) if ln.kind != "heading" else raw
        if math_delims and ln.math_ratio > 0.6 and ln.kind == "body":
            text = f"$${text}$$"

        if ln.kind == "heading" and ln.regime == "front" and ln.level >= 3:
            # a series page or jacket line, not a section: bold, not a heading
            flush_para(); flush_code()
            out.append(f"**{text}**"); out.append("")
            prev = ln
            continue
        if ln.kind == "heading":
            flush_para(); flush_code(); flush_footnotes()
            if pub_buf: _flush_pub()
            lvl = max(1, min(6, ln.level))
            if prof.doc_type == "deck" and out:
                out.append("---"); out.append("")
            if ln.regime == "list_of":
                text = {"figures": "List of Figures", "tables": "List of Tables"}.get(
                    text.lower(), text)
            out.append(f"{'#' * lvl} {text}")
            out.append("")
            if ln.regime not in ("title", "front", "praise", "copyright", "list_of") \
                    and ln.in_toc:
                toc.append((lvl, text, ln.toc_group))
            prev = ln
            continue

        if ln.kind == "code":
            flush_para()
            code.append(text)
            prev = ln
            continue
        flush_code()

        if ln.kind == "caption":
            flush_para()
            out.append(f"> **{text}**" if not text.startswith(">") else text)
            out.append("")
            prev = ln
            continue

        if ln.kind == "runin":
            flush_para(); flush_code()
            out.append(f"**{text}**"); out.append("")
            prev = ln
            continue
        if ln.kind == "panel_labels":
            flush_para(); flush_code()
            out.append(f"*{text}*"); out.append("")
            prev = ln
            continue
        if ln.kind == "table_cell":
            t = ln.table
            if t is not None and t["first"] is ln:
                flush_para(); flush_code()
                out.extend(render_table(t)); out.append("")
            prev = ln
            continue

        if ln.kind == "figure_text":
            r = ln.region
            if r is None or r["lines"][0] is not ln:
                prev = ln
                continue                      # remainder of an emitted region
            flush_para(); flush_code()
            cap = r["caption"].text.strip() if r["caption"] else ""
            labels = region_labels(r)
            block = [f"> **{cap}**" if cap else "> **Figure**"]
            img = None
            if figure_dir is not None and doc is not None:
                img = render_region(doc, r, figure_dir)
                block.insert(0, f"![{cap or 'figure'}]({figure_dir.name}/{img.name})")
                block.insert(1, "")
            desc = describe_region_vlm(img, cap, figure_vlm) if (img and figure_vlm) else None
            if desc:
                block.append(">")
                block.extend("> " + l for l in desc.splitlines())
            else:
                block.append(f"> *diagram, page {r['page'] + 1} — {len(labels)} labels*")
                if labels:
                    block.append("> Labels: " + " · ".join(labels))
            out.extend(block); out.append("")
            if r["caption"] is not None:
                r["caption"].kind = "consumed"   # already printed above
            prev = ln
            continue
        if ln.kind == "consumed":
            prev = ln
            continue

        # ---------------------------------------------------- regime kinds
        k = ln.kind
        if k in ("title_line", "title_meta", "paper_title", "paper_author",
                 "paper_email", "paper_affil", "deck_title", "deck_subtitle",
                 "deck_end", "doc_title", "doc_subtitle", "doc_meta"):
            prev = ln                                   # rendered in the head
            continue

        if k == "copyright_keep":
            flush_para(); flush_code()
            pub_buf.append(text)
            prev = ln
            continue
        if k == "copyright_drop":
            prev = ln
            continue
        if pub_buf:
            _flush_pub()

        if k in ("front_blurb", "front_bio"):
            label = "About this book" if k == "front_blurb" else "About the authors"
            if label not in front_labels:
                flush_para(); flush_code()
                front_labels.add(label)
                out.append(f"## {label}"); out.append("")
            # paragraph logic identical to body
            new_para = False
            if prev is not None and prev.kind == k:
                gap = ln.y0 - prev.y1 if ln.page == prev.page else 0
                if ln.page != prev.page:
                    new_para = not looks_continued(prev.text, ln.text)
                elif ln.isolated and not looks_continued(prev.text, ln.text):
                    new_para = True
                elif k == "front_bio" and R.BIO_START_RE.match(raw):
                    new_para = True
            elif prev is not None and prev.kind != k:
                flush_para()
            if new_para:
                flush_para()
            para.append(text)
            prev = ln
            continue

        if k == "dedication":
            flush_para(); flush_code()
            out.append(f"> *{text}*")
            if not (lines_iter_peek(ln) and lines_iter_peek(ln).kind == "dedication"):
                out.append("")
            prev = ln
            continue

        if k == "quote":
            flush_para(); flush_code()
            out.append(f"> {text}")
            prev = ln
            continue
        if k == "attribution":
            flush_para(); flush_code()
            attribution = re.sub(r'^\s*[—–-]+\s*', '', text)  # hoisted: py<3.12 f-string
            out.append(f"> — {attribution}")
            out.append("")
            prev = ln
            continue

        if k in ("list_item", "ref_entry", "lof_entry", "note_entry"):
            flush_para(); flush_code()
            if k == "list_item":
                raw = ln.text.strip()                 # un-escaped
                m = R.LIST_NUM_RE.match(raw)
                text = raw if m else escape_md(
                    re.sub(r"^\s*[•▪●◦‣⁃∙·\u2022\u25aa\u25cf\u25e6\-*]\s+", "", raw))
                if prof.doc_type == "deck" and ln.level >= 2:
                    text = "  " + text                # nested bullet
                out.append(f"{'' if m else ('  - ' if text.startswith('  ') else '- ')}{text.lstrip()}")
            elif k == "note_entry":
                out.append(re.sub(r"^\s*(\d{1,3})[.)]?\s+", r"\1. ", text))
            else:
                out.append(f"- {text}")
            prev = ln
            continue
        if k in ("list_cont", "ref_cont", "lof_cont", "note_cont"):
            if out and out[-1] and not out[-1].startswith("#"):
                out[-1] = out[-1].rstrip() + " " + text
            else:
                out.append(f"- {text}")
            prev = ln
            continue

        if k == "gloss_term":
            flush_para(); flush_code()
            out.append(f"- **{text}**")
            prev = ln
            continue
        if k == "gloss_def":
            if out and out[-1].startswith("- **"):
                sep = ": " if out[-1].endswith("**") else " "
                out[-1] = out[-1] + sep + text
            else:
                para.append(text)
            prev = ln
            continue

        if k == "footnote":
            flush_para(); flush_code()
            body = re.sub(r"^\s*(\d{1,3}|[*†‡§¶])\s*", "", text)
            fn_buf.append([ln.fn_num or "*", body])
            prev = ln
            continue
        if k == "footnote_cont":
            if fn_buf:
                fn_buf[-1][1] = fn_buf[-1][1].rstrip() + " " + text
            prev = ln
            continue

        if ln.kind == "index_entry":
            flush_para(); flush_code()
            out.append(f"- {text}")
            prev = ln
            continue
        if ln.kind == "index_letter":
            flush_para(); flush_code()
            if out and out[-1] != "": out.append("")
            out.append(f"**{text}**"); out.append("")
            prev = ln
            continue
        if ln.kind == "index_cont":
            if out and out[-1].startswith("- "):
                out[-1] = out[-1] + " " + text
            else:
                out.append(f"- {text}")
            prev = ln
            continue

        if ln.kind == "footnote":
            flush_para()
            out.append(f"[^fn]: {text}")
            out.append("")
            prev = ln
            continue

        if prev is not None and prev.kind in ("index_entry", "index_cont", "index_letter") and out and out[-1] != "":
            out.append("")
        if prof.doc_type == "deck" and getattr(ln, "slide_end", False):
            pass
        # --- body ---
        # New paragraph when: indentation jumps (LaTeX first-line indent),
        # a page/column break lands on a sentence boundary, or vertical gap.
        new_para = False
        if prev is not None and prev.kind == "body":
            gap = ln.y0 - prev.y1 if ln.page == prev.page else 0
            margin = ln.col_left if ln.col_left else prof.body_left
            indented = ln.x0 > margin + 4
            if ln.page != prev.page or ln.col != prev.col:
                new_para = not looks_continued(prev.text, ln.text)
            elif ln.isolated and not looks_continued(prev.text, ln.text):
                # Isolation is y0-to-y0 against measured pitch: stable even
                # when OCR bounding-box heights jitter. And a gap alone never
                # outranks a sentence that is plainly still running on.
                new_para = True
            elif indented and not looks_continued(prev.text, ln.text):
                new_para = True
        if new_para:
            flush_para()
        para.append(text)
        prev = ln

    flush_para(); flush_code(); flush_footnotes()
    if pub_buf: _flush_pub()

    body_md = "\n".join(out)
    body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip() + "\n"

    head = build_head(prof, lines, toc if make_toc else [], all_meta)
    return head + "\n---\n\n" + body_md


def build_head(prof: Profile, lines: list[Line], toc: list, all_meta: list) -> str:
    """YAML front matter + title block + grouped, rank-indented contents."""
    if prof.doc_type == "paper":
        return build_paper_head(prof, toc)
    if prof.doc_type in ("deck", "document"):
        return build_simple_head(prof, toc)
    # ---- metadata harvested from title pages and the copyright page
    meta = []
    seen = set()
    for m in all_meta:
        m = re.sub(r"^[\s\-–—·•]+|[\s\-–—·•]+$", "", m)
        k = re.sub(r"[^a-z]", "", m.lower())
        if not k or len(m) >= 70 or re.search(r"University|Institute|College", m):
            continue
        if meta and re.match(r"^(and|&)\s", m, re.I):
            meta[-1] += " " + m[0].lower() + m[1:]; seen.add(k); continue
        if any(k in x or x in k for x in seen):
            continue
        seen.add(k); meta.append(m)
    edition = next((m for m in meta if re.search(r"\bedition\b", m, re.I)), None)
    publisher = next((m for m in meta if re.search(r"\b(Press|Publishing|Media|Books|Wiley|Springer|Elsevier)\b", m)), None)
    authors = [m for m in meta if m not in (edition, publisher)
               and not re.search(r"[®™©]|\d", m) and len(m.split()) <= 8]
    cp = [l.text for l in lines if l.kind == "copyright_keep"]
    cp_all = [l.text for l in lines if l.regime == "copyright"]
    isbns = sorted({re.sub(r"[^0-9X]", "", x) for t in cp_all
                    for x in R.BARE_ISBN_RE.findall(t)
                    if 10 <= len(re.sub(r"[^0-9X]", "", x)) <= 13})
    years = [int(y) for t in cp for y in re.findall(r"©\s*(?:[^0-9]{0,60})?(\d{4})", t)]
    year = max(years) if years else None
    if publisher is None:
        publisher = next((re.sub(r"^.*?©\s*", "", t).strip(" .")
                          for t in cp if re.search(r"Press|Publish|Media|Books", t) and "©" in t), None)
    if publisher is None:
        publisher = next((re.sub(r"^\s*Published by\s+", "", t) for t in cp_all
                          if re.match(r"^\s*Published by\s", t, re.I)), None)
    if publisher:
        publisher = re.sub(r"\s*\b(19|20)\d{2}\b.*$", "", publisher)
        publisher = re.sub(r",?\s*\d+\s+[A-Z].*$", "", publisher)      # street address
        publisher = publisher.strip(" ,.")
    if edition is None:
        edition = next((m.group(0) for t in cp_all
                        for m in [re.search(r"\b(First|Second|Third|Fourth)\s+Edition\b", t)] if m), None)
    NAME_RE = re.compile(r"^(?:[A-Z][a-z'’\-]+|[A-Z]\.)(?:\s+(?:[A-Z][a-z'’\-]+|[A-Z]\.|(?:de|van|von|der|da|di|la|le)))"
                         r"{1,3}$")
    NOT_PERSON = re.compile(r"(Press|University|Inc\b|Ltd\b|LLC|Publish|Media|Books|Corporation|"
                            r"Edition|Cambridge|London|New York|Massachusetts|England|Oxford)", re.I)

    def names_in(text):
        text = re.sub(r"^.*?©\s*(?:\d{4})?\s*", "", text)          # drop up to year
        text = re.sub(r"\s*\b(19|20)\d{2}\b.*$", "", text)         # drop trailing year
        # cut at a sentence boundary -- a period after a real word, not an initial
        text = re.split(r"(?<=[a-z][a-z])\.\s+[A-Z]|(?<=[a-z][a-z])\.$", text)[0]
        text = re.sub(r"\bAll rights reserved\b.*$", "", text, flags=re.I)
        out = []
        for part in re.split(r"\s*,\s*(?:and\s+|&\s*)?|\s+(?:and|&)\s+", text):
            part = part.strip(" .")
            if NAME_RE.match(part) and not NOT_PERSON.search(part) and part not in out:
                out.append(part)
        return out
    from_copyright = [n for t in cp if "©" in t for n in names_in(t)]
    if getattr(prof, "author_override", None):
        authors = list(prof.author_override)
    elif from_copyright:
        authors = from_copyright
    else:
        authors = [n for a in authors for n in names_in(a) if not NOT_PERSON.search(n)]
    if edition:
        edition = R.smart_case(edition, prof.acronyms)

    # ---- subtitle: title-page lines that are none of author/edition/publisher/place
    PLACE_RE = re.compile(r"^(?:[A-Z][a-z]+\s?){1,3},\s*[A-Z][A-Za-z ]+$|"
                          r"\b(Cambridge|London|New York|Boston|Sebastopol|Oxford|"
                          r"Massachusetts|England|California|Illinois)\b")
    sub_parts = []
    for m in meta:
        if m == edition or m == publisher or re.search(r"[®™©]", m) or PLACE_RE.search(m):
            continue
        # a subtitle that wraps ("...Build and Grow" / "Successful Organizations")
        # leaves a second line that can pass as a two-word name; a real author
        # line never continues an unfinished phrase, so keep continuations.
        continues_subtitle = bool(sub_parts) and not re.search(r"[.!?:]$", sub_parts[-1])
        if any(a in m for a in authors) or (NAME_RE.match(m) and not continues_subtitle):
            continue
        if re.search(r"\bedition\b|\bpress\b|university", m, re.I):
            continue
        sub_parts.append(m)
    subtitle = " ".join(sub_parts).strip() or None
    if subtitle and re.sub(r"[^a-z]", "", subtitle.lower()) in \
            re.sub(r"[^a-z]", "", prof.canonical_title.lower()):
        subtitle = None                                   # just the title repeated
    if edition:
        edition = " ".join(w.capitalize() if w.islower() else w for w in edition.split())

    y = ["---", f"title: {_yq(prof.canonical_title)}"]
    if subtitle:  y.append(f"subtitle: {_yq(subtitle)}")
    if edition:   y.append(f"edition: {_yq(edition)}")
    if authors:   y.append("authors:"); y += [f"  - {_yq(a)}" for a in authors[:6]]
    if publisher: y.append(f"publisher: {_yq(publisher)}")
    if year:      y.append(f"year: {year}")
    if isbns:     y.append("isbn:"); y += [f"  - {i}" for i in isbns]
    y += [f"pages: {prof.page_count}", f"source: {_yq(prof.source_name)}",
          "generator: pdf2md", "---", ""]

    # ---- fixed layout: title / subtitle / authors / edition · publisher, year
    head = y + [f"# {prof.canonical_title}"]
    if subtitle:
        head.append(f"*{subtitle}*  ")
    if authors:
        joined = authors[0] if len(authors) == 1 else \
            ", ".join(authors[:-1]) + " and " + authors[-1]
        head.append(f"**{joined}**  ")
    imprint = " · ".join(x for x in (edition, (f"{publisher}, {year}" if publisher and year
                                              else publisher or (str(year) if year else None)))
                         if x)
    if imprint:
        head.append(f"*{imprint}*  ")
    head.append("")

    if toc:
        # indent by rank among the levels actually used, so H1->H3 books don't
        # show an empty tier; group front/back matter under their own nodes
        used = sorted({lvl for lvl, _, _ in toc})
        rank = {lvl: i for i, lvl in enumerate(used)}
        head += ["## Contents", ""]
        cur_group = None
        slug_count: Counter = Counter()
        for lvl, t, grp in toc:
            base = slugify(t)
            n = slug_count[base]; slug_count[base] += 1
            anchor = base if n == 0 else f"{base}-{n}"
            if grp != cur_group:
                cur_group = grp
                if grp == "front": head.append("- **Front matter**")
                if grp == "back":  head.append("- **Back matter**")
            indent = rank[lvl] + (1 if grp in ("front", "back") else 0)
            head.append("  " * indent + f"- [{t}](#{anchor})")
        head.append("")
    return "\n".join(head)


def build_simple_head(prof: Profile, toc: list) -> str:
    """Decks and standard documents: title, optional subtitle, metadata
    fields, then a flat contents (slide titles / sections)."""
    m = prof.deck_meta if prof.doc_type == "deck" else prof.document_meta
    title = prof.canonical_title or m.get("title", "") or prof.source_name
    fields = dict(m.get("fields", {}))
    y = ["---", f"title: {_yq(title)}", f"type: {prof.doc_type}"]
    if m.get("subtitle"): y.append(f"subtitle: {_yq(m['subtitle'])}")
    authors = list(getattr(prof, "author_override", []) or [])
    for k in ("Author", "Authors", "Owner"):
        if k in fields and not authors:
            authors = [a.strip() for a in re.split(r",|\band\b|&", fields.pop(k)) if a.strip()]
    if authors: y.append("authors:"); y += [f"  - {_yq(a)}" for a in authors]
    for k, v in fields.items():
        y.append(f"{k.lower().replace(' ', '_')}: {_yq(v)}")
    if prof.doc_type == "deck": y.append(f"slides: {prof.page_count}")
    y += [f"pages: {prof.page_count}", f"source: {_yq(prof.source_name)}", "generator: pdf2md", "---", ""]
    head = y + [f"# {title}"]
    if m.get("subtitle"): head.append(f"*{m['subtitle']}*  ")
    if authors: head.append("**" + ", ".join(authors) + "**  ")
    if fields: head.append("  ".join(f"{k}: {v}" for k, v in fields.items()) + "  ")
    head.append("")
    if toc:
        used = sorted({lvl for lvl, _, _ in toc}); rank = {lvl: i for i, lvl in enumerate(used)}
        head += ["## Contents", ""]
        slug_count: Counter = Counter()
        for lvl, t, _ in toc:
            base = slugify(t); n = slug_count[base]; slug_count[base] += 1
            head.append("  " * rank[lvl] + f"- [{t}](#{base if n == 0 else f'{base}-{n}'})")
        head.append("")
    return "\n".join(head)


def build_paper_head(prof: Profile, toc: list) -> str:
    pm = prof.paper_meta or {}
    title = prof.canonical_title if prof.canonical_title and not prof.canonical_title.startswith("#") else pm.get("title", "")
    if getattr(prof, "author_override", None):
        authors = list(prof.author_override)
    else:
        authors = pm.get("authors", [])
    y = ["---", f"title: {_yq(title)}", "type: paper"]
    if authors:  y.append("authors:"); y += [f"  - {_yq(a)}" for a in authors[:40]]
    if pm.get("affiliations"): y.append("affiliations:"); y += [f"  - {_yq(a)}" for a in pm['affiliations'][:8]]
    if pm.get("arxiv"): y.append(f"arxiv: {pm['arxiv']}")
    if pm.get("venue"): y.append(f"venue: {_yq(pm['venue'])}")
    y += [f"pages: {prof.page_count}", f"source: {_yq(prof.source_name)}", "generator: pdf2md", "---", ""]
    head = y + [f"# {title}"]
    if authors:
        head.append("**" + ", ".join(authors) + "**  ")
    for a in pm.get("affiliations", [])[:4]:
        head.append(f"*{a}*  ")
    head.append("")
    if toc:
        used = sorted({lvl for lvl, _, _ in toc}); rank = {lvl: i for i, lvl in enumerate(used)}
        head += ["## Contents", ""]
        slug_count: Counter = Counter()
        for lvl, t, _ in toc:
            base = slugify(t); n = slug_count[base]; slug_count[base] += 1
            head.append("  " * rank[lvl] + f"- [{t}](#{base if n == 0 else f'{base}-{n}'})")
        head.append("")
    return "\n".join(head)


def _yq(s: str) -> str:
    s = s.replace('"', '\\"')
    return f'"{s}"' if re.search(r"[:#\[\]{}&*!|>'%@`,]", s) or not s else s


# ===========================================================================
# REPORTS
# ===========================================================================

def print_profile(prof: Profile, lines: list[Line]) -> None:
    kinds = Counter(l.kind for l in lines)
    print("── Detected profile ──────────────────────────────")
    print(f"  page size          {prof.page_w:.0f} x {prof.page_h:.0f}")
    print(f"  document type      {prof.doc_type}  scores={ {k: round(v) for k, v in sorted(prof.doc_scores.items(), key=lambda kv: -kv[1])} }")
    for e in prof.doc_evidence:
        print(f"      {e}")
    print(f"  text layer         {'OCR-stamped (glyphless)' if prof.ocr_layer else 'authored'}")
    print(f"  body style         {prof.body_font} @ {prof.body_size}pt")
    print(f"  body left margin   x={prof.body_left:.0f}")
    print(f"  header band        y < {prof.header_band:.1f}")
    print(f"  footer band        y > {prof.footer_band:.1f}")
    print(f"  running heads      {len(prof.running_heads)} patterns")
    for t in sorted(prof.running_heads)[:6]:
        print(f"      · {t[:64]!r}")
    print(f"  heading styles     {len(prof.heading_styles)}")
    for style, lvl in sorted(prof.heading_styles.items(), key=lambda kv: kv[1]):
        n = sum(1 for l in lines if l.style == style)
        print(f"      H{lvl}  {style[0]:<12} {style[1]:>5}pt   ({n} lines)")
    print(f"  caption styles     {sorted(prof.caption_styles)}")
    print(f"  dense demotions    {prof.demoted_dense}")
    print(f"  index entries      {prof.index_entries}")
    print(f"  figure regions     {len(prof.figure_regions)}")
    print(f"  tables             {len(prof.tables)}")
    print(f"  footnote anchors   {prof.fn_anchors}")
    if prof.outline:
        print(f"  outline            {prof.outline}")
    print("── Content census (block kinds) ────────────────────")
    for k, v in sorted(prof.census.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<16} {v}")
    print("── Classification ────────────────────────────────")
    for k, v in kinds.most_common():
        print(f"  {k:<12} {v}")


def glyph_report(lines: list[Line], limit=40) -> None:
    info = defaultdict(lambda: [0, []])
    for ln in lines:
        for ch in ln.text:
            if ord(ch) > 0x2000 and not unicodedata.category(ch).startswith("M"):
                rec = info[ch]
                rec[0] += 1
                if len(rec[1]) < 2:
                    i = ln.text.find(ch)
                    rec[1].append(ln.text[max(0, i - 30):i + 30])
    print("── Unmapped high glyphs (extend GLYPH_BY_CLASS) ──")
    for ch, (n, ctx) in sorted(info.items(), key=lambda kv: -kv[1][0])[:limit]:
        try:
            name = unicodedata.name(ch)
        except ValueError:
            name = "?"
        print(f"  {ch!r} U+{ord(ch):04X} x{n}  {name}")
        for c in ctx:
            print(f"        ...{c}...")


def drop_outer_matter(lines: list[Line]) -> None:
    """--body-only: keep only the chapters. Front matter is everything before
    the first Part/Chapter/Preface heading; back matter starts at References,
    Notes, Glossary, Index or a colophon. The regime engine already labels
    all of this, so it is a filter, not a new detector."""
    first = next((i for i, l in enumerate(lines) if l.kind == "heading"
                  and re.match(r"^(Part|Chapter|Preface|Foreword|Introduction)\b",
                               l.text.strip(), re.I)), 0)
    for l in lines[:first]:
        l.kind = "consumed"
    for l in lines:
        if l.regime in ("references", "glossary", "notes", "index") or \
                l.kind in ("copyright_keep", "copyright_drop", "title_line", "title_meta",
                           "dedication", "front_blurb", "front_bio", "quote", "attribution",
                           "lof_entry", "lof_cont"):
            l.kind = "consumed"


def write_artifacts(dir_: Path, doc, lines: list[Line], prof: Profile, pages) -> None:
    """Inspectable intermediates, the one thing the book-cleaner repo did that
    this converter did not: when output looks wrong you can see exactly what
    every stage decided instead of re-running with prints."""
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / "pages").mkdir(exist_ok=True)
    for p in pages:
        (dir_ / "pages" / f"{p + 1:03d}.txt").write_text(doc[p].get_text(), encoding="utf-8")
    profile = {
        "body_font": prof.body_font, "body_size": prof.body_size,
        "body_left": prof.body_left, "line_pitch": prof.line_pitch,
        "ocr_layer": prof.ocr_layer, "page_w": prof.page_w, "page_h": prof.page_h,
        "heading_styles": {f"{k[0]}@{k[1]}": v for k, v in prof.heading_styles.items()},
        "running_heads": sorted(prof.running_heads),
        "toc_pages": sorted(prof.toc_pages), "acronyms": sorted(prof.acronyms),
        "canonical_title": prof.canonical_title,
    }
    (dir_ / "profile.json").write_text(json.dumps(profile, indent=1, ensure_ascii=False))
    stats = {
        "pages": len(pages), "lines": len(lines),
        "kinds": dict(Counter(l.kind for l in lines)),
        "regimes": dict(Counter(l.regime for l in lines)),
        "headings_by_level": dict(Counter(l.level for l in lines if l.kind == "heading")),
        "figure_regions": len(prof.figure_regions), "tables": len(prof.tables),
        "footnote_anchors": prof.fn_anchors, "dense_demotions": prof.demoted_dense,
        "index_entries": prof.index_entries,
        "outline": prof.outline,
        "doc_type": prof.doc_type, "doc_scores": prof.doc_scores, "doc_evidence": prof.doc_evidence,
    }
    (dir_ / "stats.json").write_text(json.dumps(stats, indent=1))
    with (dir_ / "blocks.jsonl").open("w", encoding="utf-8") as f:
        for l in lines:
            f.write(json.dumps({"page": l.page + 1, "kind": l.kind, "regime": l.regime,
                                "level": l.level, "y": round(l.y0, 1), "x": round(l.x0, 1),
                                "size": round(l.size, 1), "style": list(l.style),
                                "text": l.text}, ensure_ascii=False) + "\n")


# ===========================================================================
# STRUCTURED INPUTS — EPUB / DOCX / DOC / ODT / RTF
#   The native markup already says what every block is; only the outline
#   tree, de-shouting, escaping and the head layout are shared with the PDF
#   path. Nothing here touches geometry.
# ===========================================================================

def classify_structured(meta: dict, blocks: list) -> tuple[str, list[str]]:
    words = sum(len(b.text.split()) for b in blocks if b.kind in ("para", "list_item", "quote"))
    heads = [b for b in blocks if b.kind == "heading"]
    chapters = sum(1 for b in heads if re.match(r"^(Chapter|Part)\s+[\dIVX]", b.text.strip(), re.I))
    abstract = any(b.kind == "heading" and re.match(r"^abstract\b", b.text.strip(), re.I) for b in blocks[:10])
    ev = [f"{meta.get('format')} container", f"{words} words", f"{len(heads)} headings"]
    if abstract:
        ev.append("Abstract heading near the top"); return "paper", ev
    if meta.get("format") == "epub":
        if chapters >= 2 or words >= 8000 or meta.get("isbn") or len(meta.get("toc") or []) >= 8:
            ev.append("EPUB with chapters / ISBN / book length"); return "book", ev
        ev.append("short EPUB"); return "document", ev
    if chapters >= 2 or words >= 30000:
        ev.append(f"{chapters} chapter headings" if chapters else "book-length"); return "book", ev
    return "document", ev


def _shim(text: str, level: int = 0, kind: str = "body") -> "Line":
    lvl = level or 3
    return Line(page=0, text=text, x0=0, y0=0, x1=0, y1=0, size=10.0, fonts=(),
                is_bold=kind == "heading", is_italic=False, is_mono=False, math_ratio=0.0,
                style=("H", float(lvl)) if kind == "heading" else ("P", 10.0), kind=kind, level=lvl)


def render_structured(meta: dict, blocks: list, prof: "Profile", *, make_toc=True,
                      figure_dir: Path | None = None) -> str:
    # ---- title: metadata, else a Title-styled block, else a lone level-1 heading
    title = (meta.get("title") or "").strip()
    title_blocks = [b for b in blocks if b.kind == "title"]
    if not title and title_blocks:
        title = title_blocks[0].text
    h1 = [b for b in blocks if b.kind == "heading" and b.level == 1]
    heads = [b for b in blocks if b.kind == "heading"]
    first = next((b for b in blocks if b.kind in ("heading", "para")), None)
    others = [b.size for b in blocks if b is not first and b.kind in ("heading", "para") and b.size]
    if not title and first is not None and first.size and (not others or first.size > max(others)):
        title = first.text; blocks = [b for b in blocks if b is not first]         # the largest line opens the document
    elif not title and heads and heads[0].level == 1 and len(h1) == 1:
        title = heads[0].text; blocks = [b for b in blocks if b is not heads[0]]   # the lone top heading is the title
    elif not title and len(h1) == 1 and prof.doc_type != "book":
        title = h1[0].text; blocks = [b for b in blocks if b is not h1[0]]
    # a generated title page echoes the metadata: drop those blocks
    key = lambda t: re.sub(r"\W", "", (t or "").lower())
    echoes = {key(title)} | {key(a) for a in (meta.get("authors") or [])} | {key(meta.get("publisher")), key(str(meta.get("year") or ""))}
    echoes.discard("")
    blocks = [b for b in blocks if not (b.kind in ("heading", "para") and key(b.text) in echoes)]
    if not title:
        title = Path(prof.source_name).stem.replace("_", " ").replace("-", " ")
    # a first table that is Key | Value pairs is document metadata (PRDs)
    fields: dict[str, str] = {}
    first_tbl = next((b for b in blocks if b.kind == "table"), None)
    if first_tbl is not None and all(len(r) == 2 for r in first_tbl.rows) and 2 <= len(first_tbl.rows) <= 10 \
            and all(DD.FIELD_RE.match(r[0] + ":") or len(r[0]) <= 16 for r in first_tbl.rows):
        for k, v in first_tbl.rows:
            fields[k.strip(" :").title()] = v.strip()
        blocks = [b for b in blocks if b is not first_tbl]
    authors = list(getattr(prof, "author_override", []) or []) or list(meta.get("authors") or [])
    for k in ("Author", "Authors", "Owner"):
        if k in fields and not authors:
            authors = [a.strip() for a in re.split(r",|\band\b|&", fields.pop(k)) if a.strip()]
    prof.canonical_title = title

    # ---- outline tree over the headings, exactly as for PDFs
    shims_all = [_shim(b.text, b.level, "heading" if b.kind == "heading" else "body") for b in blocks]
    prof.heading_styles = {("H", float(i)): i for i in range(1, 7)}
    prof.acronyms = R.learn_acronyms(shims_all)
    head_pairs = [(b, sh) for b, sh in zip(blocks, shims_all) if b.kind == "heading"]
    head_shims = [sh for _, sh in head_pairs]
    prof.outline = OL.apply_outline(head_shims, prof)
    assign_toc_groups(head_shims)
    for b, sh in head_pairs:
        b.level, b.kind = sh.level, sh.kind          # heading | runin | body
        b.in_toc = sh.in_toc; b.toc_group = sh.toc_group

    # ---- body
    out: list[str] = []
    toc: list[tuple[int, str, str]] = []
    pending_notes: list = []
    z = meta.get("zip")

    def flush_notes():
        if pending_notes:
            out.append("")
            for b in pending_notes:
                out.append(f"[^{b.note_id or 'n'}]: {escape_md(b.text)}")
            out.append(""); pending_notes.clear()

    sc = lambda t: R.smart_case(t, prof.acronyms)
    for b in blocks:
        k = b.kind
        if k in ("title", "meta"):
            continue
        if k == "heading":
            flush_notes()
            lvl = max(1, min(6, b.level))
            out.append(f"{'#' * lvl} {sc(b.text)}"); out.append("")
            if getattr(b, "in_toc", False):
                toc.append((lvl, sc(b.text), getattr(b, "toc_group", "body")))
        elif k == "runin":
            out.append(f"**{sc(b.text)}**"); out.append("")
        elif k == "para":
            out.append(escape_md(sc(b.text))); out.append("")
        elif k == "list_item":
            marker = "1." if b.ordered else "-"
            out.append("  " * max(0, b.level - 1) + f"{marker} {escape_md(sc(b.text))}")
        elif k == "quote":
            out.append("> " + escape_md(sc(b.text))); out.append("")
        elif k == "code":
            out.append("```"); out.append(b.text.rstrip()); out.append("```"); out.append("")
        elif k == "caption":
            out.append(f"> **{sc(b.text)}**"); out.append("")
        elif k == "table":
            rows = b.rows; n = max(len(r) for r in rows)
            def row(r): return "| " + " | ".join((c or "").replace("|", "\\|") for c in r + [""] * (n - len(r))) + " |"
            out.append(row(rows[0])); out.append("|" + "---|" * n); out.extend(row(r) for r in rows[1:]); out.append("")
        elif k == "image":
            target = b.src
            if figure_dir is not None and z is not None and b.src in z.namelist():
                figure_dir.mkdir(parents=True, exist_ok=True)
                dest = figure_dir / Path(b.src).name
                dest.write_bytes(z.read(b.src)); target = f"{figure_dir.name}/{dest.name}"
            out.append(f"![{b.text or Path(b.src).stem}]({target})"); out.append("")
        elif k == "footnote":
            pending_notes.append(b)
        elif k == "hr":
            out.append("---"); out.append("")
    flush_notes()
    # a list followed by non-list text needs a blank line
    body_md = re.sub(r"(\n(?: *- |1\. )[^\n]*)\n(?=[^\n\-\s1])", r"\1\n\n", "\n".join(out))
    body_md = re.sub(r"\n{3,}", "\n\n", body_md).strip() + "\n"

    # ---- head
    y = ["---", f"title: {_yq(title)}", f"type: {prof.doc_type}", f"format: {meta.get('format')}"]
    if authors: y.append("authors:"); y += [f"  - {_yq(a)}" for a in authors[:40]]
    if meta.get("publisher"): y.append(f"publisher: {_yq(meta['publisher'])}")
    if meta.get("year"): y.append(f"year: {meta['year']}")
    if meta.get("isbn"): y.append("isbn:"); y += [f"  - {i}" for i in meta["isbn"]]
    if meta.get("language"): y.append(f"language: {meta['language']}")
    for kf, v in fields.items():
        y.append(f"{kf.lower().replace(' ', '_')}: {_yq(v)}")
    y += [f"source: {_yq(prof.source_name)}", "generator: pdf2md", "---", ""]
    head = y + [f"# {title}"]
    if authors: head.append("**" + ", ".join(authors) + "**  ")
    imprint = " · ".join(x for x in ((meta.get("publisher") or None), (str(meta["year"]) if meta.get("year") else None)) if x)
    if imprint: head.append(f"*{imprint}*  ")
    if fields: head.append("  ".join(f"{k}: {v}" for k, v in fields.items()) + "  ")
    head.append("")
    if make_toc and toc:
        head += ["## Contents", ""]
        used = sorted({lvl for lvl, _, _ in toc}); rank = {lvl: i for i, lvl in enumerate(used)}
        cur_group = None; slug_count: Counter = Counter()
        for lvl, t, grp in toc:
            if grp != cur_group:
                cur_group = grp
                if grp == "front": head.append("- **Front matter**")
                if grp == "back":  head.append("- **Back matter**")
            base = slugify(t); nn = slug_count[base]; slug_count[base] += 1
            indent = rank[lvl] + (1 if grp in ("front", "back") else 0)
            head.append("  " * indent + f"- [{t}](#{base if nn == 0 else f'{base}-{nn}'})")
        head.append("")
    prof.census = Counter(b.kind for b in blocks)
    return "\n".join(head) + "\n---\n\n" + body_md


def main_structured(args, src: Path) -> None:
    ext = src.suffix.lower()
    work = src
    if ext in (".doc", ".odt", ".rtf"):
        try:
            work = ST.convert_with_soffice(src, getattr(args, "soffice", None))
        except RuntimeError as e:
            sys.exit(str(e))
        ext = ".docx"
    meta, blocks = (ST.read_epub(work) if ext == ".epub" else ST.read_docx(work))
    prof = Profile()
    prof.source_name, prof.page_count = src.name, 0
    kind, ev = classify_structured(meta, blocks)
    prof.doc_type = args.doc_type or kind
    prof.doc_evidence = ev
    if args.title: meta["title"] = args.title
    prof.author_override = args.author
    if args.profile:
        print("── Detected profile ──────────────────────────────")
        print(f"  source             {src.name} ({src.suffix.lower()})")
        print(f"  document type      {prof.doc_type}  ({'; '.join(ev)})")
        for k2, v in sorted(Counter(b.kind for b in blocks).items(), key=lambda kv: -kv[1]):
            print(f"  {k2:<16} {v}")
        if meta.get("toc"):
            print(f"  contents entries   {len(meta['toc'])}")
        return
    fig_dir = Path(args.figure_dir) if args.figure_dir else None
    md = render_structured(meta, blocks, prof, make_toc=not args.no_toc, figure_dir=fig_dir)
    if args.body_only:
        # drop everything before the first body-group heading and after back matter
        parts = md.split("\n---\n\n", 1)
        body = parts[1] if len(parts) > 1 else md
        m = re.search(r"^# (Chapter|Part|Introduction|1\b)", body, re.M)
        if m: body = body[m.start():]
        m2 = re.search(r"^# (References|Bibliography|Index|Glossary|Notes)\b", body, re.M)
        if m2: body = body[:m2.start()]
        md = parts[0] + "\n---\n\n" + body
    out = Path(args.output) if args.output else src.with_suffix(".md")
    out.write_text(md, encoding="utf-8")
    if args.artifacts:
        d = Path(args.artifacts); d.mkdir(parents=True, exist_ok=True)
        (d / "stats.json").write_text(json.dumps({
            "source": src.name, "format": meta.get("format"), "doc_type": prof.doc_type,
            "doc_evidence": ev, "kinds": dict(prof.census), "outline": prof.outline,
            "doc_scores": {prof.doc_type: 10.0}, "pages": 0, "tables": prof.census.get("table", 0),
            "figure_regions": prof.census.get("image", 0), "footnote_anchors": prof.census.get("footnote", 0),
            "index_entries": 0}, indent=1))
        with (d / "blocks.jsonl").open("w", encoding="utf-8") as f:
            for b in blocks:
                f.write(json.dumps({"kind": b.kind, "level": b.level, "text": b.text,
                                    "rows": b.rows or None, "src": b.src or None}, ensure_ascii=False) + "\n")
    print(f"type {prof.doc_type}  blocks {len(blocks)}  headings {prof.census.get('heading', 0)}  "
          f"tables {prof.census.get('table', 0)}  footnotes {prof.census.get('footnote', 0)}")
    print(f"md    -> {out}  ({len(md):,} chars)")


# ===========================================================================
# CLI
# ===========================================================================

def parse_pages(spec: str, n: int) -> range | list[int]:
    if not spec:
        return range(n)
    out = []
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a) - 1, min(int(b), n)))
        else:
            out.append(int(part) - 1)
    return [p for p in out if 0 <= p < n]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="PDF, EPUB, DOCX, DOC, ODT or RTF")
    ap.add_argument("-o", "--output")
    ap.add_argument("--pages", default="", help="e.g. 44-120 or 1,5,9 (1-based)")
    ap.add_argument("--profile", action="store_true", help="show detection, exit")
    ap.add_argument("--glyph-report", action="store_true")
    ap.add_argument("--emit-json", help="write typed blocks as JSON")
    ap.add_argument("--no-toc", action="store_true")
    ap.add_argument("--math-delims", action="store_true",
                    help="wrap math-heavy lines in $$...$$")
    ap.add_argument("--figure-dir", help="crop detected figures to PNGs here "
                    "and link them from the Markdown")
    ap.add_argument("--figure-vlm", metavar="MODEL",
                    help="Ollama vision model to transcribe each figure crop "
                         "(e.g. qwen2.5vl:7b); requires --figure-dir")
    ap.add_argument("--artifacts", metavar="DIR",
                    help="write inspectable intermediates: profile.json, stats.json, "
                         "blocks.jsonl (every typed line), and pages/NNN.txt")
    ap.add_argument("--title", help="override detected title")
    ap.add_argument("--author", action="append", default=[],
                    help="override detected author(s); repeatable")
    ap.add_argument("--soffice", help="path to LibreOffice's soffice for .doc/.odt/.rtf input")
    ap.add_argument("--doc-type", choices=["book", "paper", "deck", "document"],
                    help="override the detected document type")
    ap.add_argument("--body-only", action="store_true",
                    help="drop front matter (before the first chapter/preface) and "
                         "back matter (references, index, colophon)")
    args = ap.parse_args()
    run(args)


def run(args) -> None:
    """Body of ``main()`` after argument parsing (library entry point)."""
    src = Path(args.input)
    if not src.exists():
        sys.exit(f"not found: {src}")
    if src.suffix.lower() in ST.STRUCTURED_EXT:
        return main_structured(args, src)

    doc = pymupdf.open(src)
    pages = parse_pages(args.pages, doc.page_count)

    lines = extract_lines(doc, pages)
    if not lines:
        sys.exit(f"{src.name}: no text layer on any of {doc.page_count} pages — this is an image-only "
                 "scan. Run OCR first (Chandra 2, Marker, or ocrmypdf) and convert the result.")

    prof = build_profile(lines, doc, pages)
    prof.page_count, prof.source_name, prof.doc = doc.page_count, src.name, doc
    prof.rotated_text = list(ROTATED_TEXT)
    dt = DT.classify_document(doc, lines, prof)
    prof.doc_type = args.doc_type or dt.kind
    prof.doc_evidence, prof.doc_scores = dt.evidence, dt.scores
    classify(lines, prof)

    # <author>#<title>[#index].pdf naming convention (from book-cleaner-cli)
    # and explicit flags override whatever the book itself says.
    m = re.match(r"^(?P<author>[^#]+)#(?P<title>[^#]+?)(?:#\d+)?$", src.stem)
    if m and not args.title:
        prof.canonical_title = m.group("title").replace("_", " ").strip()
    if m and not args.author:
        args.author = [m.group("author").replace("_", " ").strip()]
    if args.title:
        prof.canonical_title = args.title
    prof.author_override = args.author

    if args.body_only:
        drop_outer_matter(lines)

    if args.glyph_report:
        glyph_report(lines)
        return
    if args.profile:
        print_profile(prof, lines)
        return

    if args.emit_json:
        payload = [asdict(l) for l in lines if l.kind != "furniture"]
        Path(args.emit_json).write_text(json.dumps(payload, indent=1))
        print(f"json  -> {args.emit_json}  ({len(payload)} blocks)")

    fig_dir = Path(args.figure_dir) if args.figure_dir else None
    md = assemble(lines, prof, make_toc=not args.no_toc,
                  math_delims=args.math_delims, doc=doc,
                  figure_dir=fig_dir, figure_vlm=args.figure_vlm)
    out = Path(args.output) if args.output else src.with_suffix(".md")
    out.write_text(md, encoding="utf-8")

    if args.artifacts:
        write_artifacts(Path(args.artifacts), doc, lines, prof, pages)

    kinds = Counter(l.kind for l in lines)
    print(f"figures {len(prof.figure_regions)}  ", end="")
    print(f"pages {len(pages)}  lines {len(lines)}  "
          f"headings {kinds['heading']}  furniture-dropped {kinds['furniture']}")
    print(f"md    -> {out}  ({len(md):,} chars)")



# ===========================================================================
# The core addressed its siblings by prefix; in the single-file build they
# share one namespace, so the prefixes alias the module itself.
# ===========================================================================
R = PP = OL = DT = DK = DD = ST = sys.modules[__name__]


if __name__ == "__main__":
    main()
