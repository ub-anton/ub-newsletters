#!/usr/bin/env python3
"""
Clean a Braze newsletter HTML export for standalone web hosting.

What it does:
  1. Resolves a Braze Liquid {% if %}/{% else %}/{% endif %} locale branch
     down to a single static branch (Braze wraps each branch in its own
     "row" table, so we find row boundaries and keep only the branch we want).
  2. Strips Outlook/MSO-only markup: conditional comments, VML blocks,
     xmlns:v / xmlns:o namespaces, and mso-* inline CSS properties.
  3. Removes unresolved Braze content-block references
     ({{content_blocks.${...}}}), which Braze does not inline on export and
     which would otherwise render as literal text in the hosted archive.
  4. Strips Braze per-recipient tracking params (lid=...) from links,
     since a public web page has no subscriber context.
  5. Collapses excess whitespace between tags.

Usage:
  python clean_newsletter.py input.html output_stem
  python clean_newsletter.py input.html output_stem --locales it,es

  By default, writes only the Italian branch: output_stem-it.html
  (Unobravo currently publishes Italian only -- the Spanish branch still
  exists inside every Braze export because of how the template is built,
  it's just discarded unless you explicitly ask for it with --locales.)
  If the file has no Liquid conditional at all, writes output_stem.html
  with no locale suffix, regardless of --locales.
"""

import argparse
import re
import sys
from html import unescape


ROW_RE = re.compile(r'<table class="row row-\d+"')


def find_row_starts(html: str):
    return [m.start() for m in ROW_RE.finditer(html)]


def resolve_both_branches(html: str):
    """Split a single {% if %}/{% else %}/{% endif %} into two standalone HTML strings.

    Returns (if_html, else_html). If there's no Liquid conditional at all,
    returns (html, None) -- the caller treats that as "single output, no
    locale split".

    NOTE: this assumes the common Braze/Stripo export pattern where each
    Liquid marker sits alone inside its own row table, and the two
    branches are each one or more complete sibling row tables in between.
    Only handles ONE conditional per file; if Braze ever bakes in a second
    one (e.g. a coupon toggle alongside locale), extend this to loop
    until no "{% if" remains.
    """
    if "{% if" not in html:
        return html, None

    row_starts = find_row_starts(html)
    if not row_starts:
        raise ValueError("Found a Liquid conditional but no row tables to anchor on")

    def row_index_containing(needle: str) -> int:
        pos = html.index(needle)
        return max(i for i, start in enumerate(row_starts) if start <= pos)

    i_if = row_index_containing("{% if")
    i_else = row_index_containing("{% else %}")
    i_endif = row_index_containing("{% endif %}")

    if_branch = html[row_starts[i_if + 1]:row_starts[i_else]]
    else_branch = html[row_starts[i_else + 1]:row_starts[i_endif]]
    after_endif_row_end = (
        row_starts[i_endif + 1] if i_endif + 1 < len(row_starts) else len(html)
    )

    prefix = html[:row_starts[i_if]]
    suffix = html[after_endif_row_end:]
    return prefix + if_branch + suffix, prefix + else_branch + suffix


def strip_mso_and_vml(html: str) -> str:
    # Outlook-only conditional comment blocks, e.g. VML buttons
    html = re.sub(r"<!--\[if mso\]>.*?<!\[endif\]-->", "", html, flags=re.DOTALL)
    html = re.sub(r"<!--\[if mso[^\]]*\]>.*?<!\[endif\]-->", "", html, flags=re.DOTALL)
    # Unwrap (don't delete) the "not mso" wrapper comments -- keep the content
    html = re.sub(r"<!--\[if !mso\]><!-->", "", html)
    html = re.sub(r"<!--<!\[endif\]-->", "", html)
    # Office namespace declarations on <html>
    html = re.sub(r'\s*xmlns:v="urn:schemas-microsoft-com:vml"', "", html)
    html = re.sub(r'\s*xmlns:o="urn:schemas-microsoft-com:office:office"', "", html)

    def strip_mso_props(match: re.Match) -> str:
        props = [p.strip() for p in match.group(1).split(";") if p.strip()]
        kept = [p for p in props if not p.lower().startswith("mso-")]
        return 'style="' + ";".join(kept) + '"'

    html = re.sub(r'style="([^"]*)"', strip_mso_props, html)
    return html


def strip_tracking_params(html: str) -> str:
    return re.sub(r"[?&]lid=[a-z0-9]+", "", html, flags=re.IGNORECASE)


RESIZE_REPORTER_SCRIPT = """
<script>
(function () {
  function sendHeight() {
    var h = document.body.scrollHeight;
    window.parent.postMessage({ type: "newsletter-resize", height: h }, "*");
  }
  window.addEventListener("load", sendHeight);
  window.addEventListener("resize", sendHeight);
  if (window.ResizeObserver) {
    new ResizeObserver(sendHeight).observe(document.body);
  } else {
    setInterval(sendHeight, 500);
  }
  sendHeight();
})();
</script>
"""


def add_resize_reporter(html: str) -> str:
    """Inject a small script that reports the iframe's real height to its
    parent via postMessage -- required because the newsletter is hosted on
    a different origin (GitHub Pages) than the Webflow page embedding it,
    so the parent page can't just reach in and read scrollHeight directly."""
    if "</body>" in html:
        return html.replace("</body>", RESIZE_REPORTER_SCRIPT + "</body>", 1)
    return html + RESIZE_REPORTER_SCRIPT


def minify_whitespace(html: str) -> str:
    html = re.sub(r">\s+<", "><", html)
    html = re.sub(r"[ \t]+", " ", html)
    return html


def remove_duplicate_logo(html: str) -> str:
    """Strip the Unobravo logo row (and the spacer row right after it) from
    the top of the newsletter, since the Webflow page embedding this already
    has its own header with the same logo -- leaving it in doubles it up.

    Identifies the logo row by content signature (an image_block linking to
    unobravo.com) rather than by row number, since which literal row-N class
    ends up first depends on which locale branch got kept. Only touches the
    very first row of the document and, if present, the row immediately
    after it -- won't remove anything further down even if similar-looking
    blocks appear later in the newsletter body.
    """
    row_starts = find_row_starts(html)
    if not row_starts:
        return html

    def row_span(i: int) -> str:
        end = row_starts[i + 1] if i + 1 < len(row_starts) else len(html)
        return html[row_starts[i]:end]

    first_row = row_span(0)
    is_logo_row = (
        "image_block" in first_row
        and "unobravo.com" in first_row
    )
    if not is_logo_row:
        return html

    rows_to_drop = 1
    if len(row_starts) > 1 and "spacer_block" in row_span(1) and "image_block" not in row_span(1):
        rows_to_drop = 2

    cutoff = row_starts[rows_to_drop] if len(row_starts) > rows_to_drop else len(html)
    return html[:row_starts[0]] + html[cutoff:]


CONTENT_BLOCK_RE = re.compile(
    r"\{\{\s*content_blocks\s*\.\s*(?:\$\{[^{}]*\}|[\w.\-]+)[^{}]*\}\}",
    re.IGNORECASE,
)
TABLE_TAG_RE = re.compile(r"<\s*(/?)\s*table\b", re.IGNORECASE)
# Deliberately more tolerant than ROW_RE above: Braze wraps some rows with extra
# classes ("row row-9 mobile_hide") and its export sometimes breaks the line
# between <table and class=, both of which ROW_RE misses. ROW_RE is left alone
# so the logo/locale logic keeps behaving exactly as it does today.
ROW_TABLE_RE = re.compile(r'<table\b[^>]*class="[^"]*\brow-\d+\b[^"]*"', re.IGNORECASE)
VISIBLE_TAG_RE = re.compile(
    r"<\s*(?:img|picture|svg|video|audio|iframe|embed|object|input|button)\b",
    re.IGNORECASE,
)
# Zero-width characters str.strip() does not remove (&nbsp; and hair spaces it does).
ZERO_WIDTH = str.maketrans({c: None for c in "\u200b\u200c\u200d\u2060\ufeff"})


def table_span_end(html: str, start: int) -> int:
    """Index just past the </table> that closes the <table> opening at `start`."""
    depth = 0
    for match in TABLE_TAG_RE.finditer(html, start):
        depth += -1 if match.group(1) else 1
        if depth == 0:
            close = html.find(">", match.end())
            return len(html) if close == -1 else close + 1
    return len(html)


def row_spans(html: str):
    """(start, end) of every top-level row table, in document order."""
    spans = []
    for match in ROW_TABLE_RE.finditer(html):
        start = match.start()
        if spans and start < spans[-1][1]:
            continue  # nested inside a row we already captured
        spans.append((start, table_span_end(html, start)))
    return spans


def row_is_visually_empty(row_html: str) -> bool:
    """True if a row renders nothing a reader can see: spacers, hairline dividers."""
    if VISIBLE_TAG_RE.search(row_html):
        return False
    text = re.sub(r"<!--.*?-->", " ", row_html, flags=re.DOTALL)
    text = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = unescape(re.sub(r"<[^>]*>", " ", text))
    return not text.translate(ZERO_WIDTH).strip()


def remove_content_block_rows(html: str, max_trailing_rows: int = 3) -> str:
    """Strip unresolved Braze content-block references, e.g.

        {{content_blocks.${TP_Footer_app_donwload_cream} | id: 'cb3'}}

    Braze template exports do not inline referenced content blocks, so the
    placeholder survives into the export and renders as literal text in the
    hosted archive (the shared footer blocks are the usual offenders).

    A row whose only content was the placeholder is dropped entirely -- leaving
    it behind would render an empty band. A row that has other content keeps
    everything else and loses just the placeholder. Any divider or spacer row
    left dangling at the very bottom afterwards (a hairline with nothing under
    it) is dropped too, up to `max_trailing_rows`.
    """
    if not CONTENT_BLOCK_RE.search(html):
        return html

    spans = row_spans(html)
    if not spans:
        return CONTENT_BLOCK_RE.sub("", html)

    # Slice the document into row tables and the bits between them. Only row
    # segments are ever dropped; everything else (document head, closing
    # wrapper tags) is passed through untouched.
    segments = [(html[:spans[0][0]], False)]
    for i, (start, end) in enumerate(spans):
        segments.append((html[start:end], True))
        next_start = spans[i + 1][0] if i + 1 < len(spans) else len(html)
        if next_start > end:
            segments.append((html[end:next_start], False))

    kept, dropped = [], 0
    for text, is_row in segments:
        if is_row and CONTENT_BLOCK_RE.search(text):
            stripped = CONTENT_BLOCK_RE.sub("", text)
            if row_is_visually_empty(stripped):
                dropped += 1
                continue
            text = stripped
        kept.append((text, is_row))

    for _ in range(max_trailing_rows if dropped else 0):
        last_row = None
        for i in range(len(kept) - 1, -1, -1):
            text, is_row = kept[i]
            if is_row:
                last_row = i
                break
            if not row_is_visually_empty(text):
                break  # real content sits below the last row, leave it alone
        if last_row is None or not row_is_visually_empty(kept[last_row][0]):
            break
        kept.pop(last_row)

    # Belt and braces: catch placeholders outside any row table (preheader text,
    # href attributes) that the row pass would not have seen.
    return CONTENT_BLOCK_RE.sub("", "".join(text for text, _ in kept))


def clean_resolved(html: str) -> str:
    """Apply MSO/VML/tracking/whitespace cleanup to an already locale-resolved HTML string."""
    html = remove_duplicate_logo(html)
    html = strip_mso_and_vml(html)
    html = remove_content_block_rows(html)
    html = strip_tracking_params(html)
    html = add_resize_reporter(html)
    html = minify_whitespace(html)
    return html


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Path to the raw Braze export")
    parser.add_argument(
        "output_stem",
        help="Output path without extension, e.g. docs/newsletters/aurora/standard/2026-07-07",
    )
    parser.add_argument(
        "--locales", default="it",
        help="Comma-separated list of locales to write (default: it). "
             "'it' maps to the {% else %} branch, 'es' maps to the {% if %} branch.",
    )
    args = parser.parse_args()
    requested = [loc.strip().lower() for loc in args.locales.split(",") if loc.strip()]

    with open(args.input, encoding="utf-8") as f:
        raw = f.read()

    if_html, else_html = resolve_both_branches(raw)

    if else_html is None:
        # No Liquid conditional found -- single output, no locale suffix
        out_path = f"{args.output_stem}.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(clean_resolved(if_html))
        print(f"No locale conditional found. Wrote {out_path}", file=sys.stderr)
        print(out_path)  # stdout: machine-readable list of files written
        return

    available = {"es": if_html, "it": else_html}
    for locale in requested:
        branch_html = available.get(locale)
        if branch_html is None:
            print(f"Skipping unknown locale '{locale}' (available: es, it)", file=sys.stderr)
            continue
        out_path = f"{args.output_stem}-{locale}.html"
        cleaned = clean_resolved(branch_html)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(cleaned)
        print(f"Wrote {len(cleaned):,} characters to {out_path}", file=sys.stderr)
        print(out_path)  # stdout: machine-readable list of files written


if __name__ == "__main__":
    main()
