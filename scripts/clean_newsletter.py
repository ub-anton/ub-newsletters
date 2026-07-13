#!/usr/bin/env python3
"""
Clean a Braze newsletter HTML export for standalone web hosting.

What it does:
  1. Resolves a Braze Liquid {% if %}/{% else %}/{% endif %} locale branch
     down to a single static branch (Braze wraps each branch in its own
     "row" table, so we find row boundaries and keep only the branch we want).
  2. Strips Outlook/MSO-only markup: conditional comments, VML blocks,
     xmlns:v / xmlns:o namespaces, and mso-* inline CSS properties.
  3. Strips Braze per-recipient tracking params (lid=...) from links,
     since a public web page has no subscriber context.
  4. Collapses excess whitespace between tags.

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


def clean_resolved(html: str) -> str:
    """Apply MSO/VML/tracking/whitespace cleanup to an already locale-resolved HTML string."""
    html = strip_mso_and_vml(html)
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
