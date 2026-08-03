#!/usr/bin/env python3
"""
Parse a Braze native export filename and compute the destination path our
pipeline expects (raw-newsletters/<brand>/<variant>/<date>.html).

Aurora and Youbravo use genuinely different filename shapes (confirmed
against real exports, not guessed):

    Aurora (coupon):
    20260415_IT_PT_NEWSLETTER_AURORA_#1_Coupon_20260522112504.html

    Aurora (no coupon -- note the variant is two words):
    20260415_IT_PT_NEWSLETTER_AURORA_#1_No_Coupon_20260522113851.html

    Youbravo (different keyword, no underscore before #, no variant segment
    at all -- Youbravo has no coupon/standard split):
    20260519_IT_TP_BLAST_Youbravo#11_20260522111325.html

The two-letter code after the locale (PT / TP) looks like an audience
segment (Patient / Therapist Professional) rather than a second locale --
it's captured but not used, since brand alone already implies audience.

Usage:
    python normalize_filename.py "<braze_export_filename>"
    -> prints: raw-newsletters/aurora/coupon/2026-04-15.html
"""

import re
import sys

AURORA_RE = re.compile(
    r"^(?P<date>\d{8})_"
    r"(?P<locale>[A-Za-z]{2})_"
    r"(?P<audience>[A-Za-z]{2})_"
    r"NEWSLETTER_AURORA_#(?P<seq>\d+)_"
    r"(?P<variant>No_Coupon|NoCoupon|Coupon)_"
    r"(?P<export_ts>\d{14})"
    r"\.html$",
    re.IGNORECASE,
)

YOUBRAVO_RE = re.compile(
    r"^(?P<date>\d{8})_"
    r"(?P<locale>[A-Za-z]{2})_"
    r"(?P<audience>[A-Za-z]{2})_"
    r"BLAST_Youbravo#(?P<seq>\d+)_"
    r"(?P<export_ts>\d{14})"
    r"\.html$",
    re.IGNORECASE,
)


def _normalize_variant(raw: str) -> str:
    cleaned = raw.lower().replace("_", "")
    if cleaned == "coupon":
        return "coupon"
    if cleaned == "nocoupon":
        return "standard"
    raise ValueError(f"Unrecognized Aurora variant token '{raw}'")


def _ymd(date_raw: str) -> str:
    return f"{date_raw[0:4]}-{date_raw[4:6]}-{date_raw[6:8]}"


def parse_braze_filename(filename: str) -> dict:
    m = AURORA_RE.match(filename)
    if m:
        return {
            "brand": "aurora",
            "variant": _normalize_variant(m.group("variant")),
            "date": _ymd(m.group("date")),
            "locale": m.group("locale"),
            "audience": m.group("audience"),
            "seq": m.group("seq"),
            "export_timestamp": m.group("export_ts"),
        }

    m = YOUBRAVO_RE.match(filename)
    if m:
        return {
            "brand": "youbravo",
            "variant": "standard",
            "date": _ymd(m.group("date")),
            "locale": m.group("locale"),
            "audience": m.group("audience"),
            "seq": m.group("seq"),
            "export_timestamp": m.group("export_ts"),
        }

    raise ValueError(
        f"Filename '{filename}' doesn't match either known Braze export shape "
        f"(Aurora's *_NEWSLETTER_AURORA_#N_<variant>_* or Youbravo's "
        f"*_BLAST_Youbravo#N_*). If this is a legitimate new shape -- a third "
        f"brand, or these two have changed -- update AURORA_RE/YOUBRAVO_RE."
    )


def destination_path(filename: str) -> str:
    parsed = parse_braze_filename(filename)
    return f"raw-newsletters/{parsed['brand']}/{parsed['variant']}/{parsed['date']}.html"


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python normalize_filename.py <braze_export_filename>", file=sys.stderr)
        sys.exit(1)
    try:
        print(destination_path(sys.argv[1]))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
