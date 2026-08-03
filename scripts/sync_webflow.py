#!/usr/bin/env python3
"""
Create or update a Webflow CMS "Newsletters" collection item so it points
at a cleaned newsletter file hosted on GitHub Pages.

Requires env vars:
  WEBFLOW_TOKEN          Site token with the CMS:write scope
  WEBFLOW_COLLECTION_ID  The Newsletters collection's ID
  PAGES_BASE_URL         e.g. https://yourorg.github.io/newsletters-repo

Usage:
  python sync_webflow.py \
    --brand aurora \
    --variant coupon \
    --locale it \
    --date 2026-07-07 \
    --hosted-path newsletters/aurora/coupon/2026-07-07-it.html

Slug and display name are derived automatically from brand/variant/locale/date,
so you don't need to type them separately each time. Re-running this for the
same combination is safe -- it looks up the existing item by slug and
updates it instead of creating a duplicate.
"""

import argparse
import os
import sys

import requests

API_BASE = "https://api.webflow.com/v2"


def get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "accept-version": "1.0.0",
    }


def find_existing_item(collection_id: str, token: str, slug: str):
    url = f"{API_BASE}/collections/{collection_id}/items"
    resp = requests.get(url, headers=get_headers(token), params={"limit": 100})
    resp.raise_for_status()
    for item in resp.json().get("items", []):
        if item.get("fieldData", {}).get("slug") == slug:
            return item
    return None


def build_slug(brand: str, variant: str, locale: str, date: str) -> str:
    parts = [brand, variant, date, locale]
    return "-".join(p.lower() for p in parts if p)


def build_name(brand: str, variant: str, locale: str, date: str) -> str:
    label = brand.capitalize()
    if variant and variant.lower() != "standard":
        label += f" ({variant.capitalize()})"
    return f"{label} newsletter - {date} - {locale.upper()}"


def upsert_item(args):
    token = os.environ["WEBFLOW_TOKEN"]
    collection_id = os.environ["WEBFLOW_COLLECTION_ID"]
    pages_base_url = os.environ["PAGES_BASE_URL"].rstrip("/")

    hosted_url = f"{pages_base_url}/{args.hosted_path.lstrip('/')}"
    slug = build_slug(args.brand, args.variant, args.locale, args.date)
    name = build_name(args.brand, args.variant, args.locale, args.date)

    field_data = {
        "name": name,
        "slug": slug,
        "brand": args.brand.capitalize(),
        "variant": args.variant.capitalize(),
        "locale": args.locale.upper(),
        "send-date": f"{args.date}T00:00:00.000Z",
        "hosted-html-url": hosted_url,
    }
    if args.summary:
        field_data["summary"] = args.summary

    payload = {"isArchived": False, "isDraft": False, "fieldData": field_data}

    existing = find_existing_item(collection_id, token, slug)

    if existing:
        item_id = existing["id"]
        url = f"{API_BASE}/collections/{collection_id}/items/{item_id}/live"
        resp = requests.patch(url, headers=get_headers(token), json=payload)
        action = "Updated"
    else:
        url = f"{API_BASE}/collections/{collection_id}/items/live"
        resp = requests.post(url, headers=get_headers(token), json=payload)
        action = "Created"

    if not resp.ok:
        print(f"Webflow API error {resp.status_code}: {resp.text}", file=sys.stderr)
        resp.raise_for_status()

    item = resp.json()
    item_id = existing["id"] if existing else item.get("id")

    print(f"{action} CMS item '{slug}' -> {hosted_url}")

    if args.noindex and item_id:
        set_sitemap_exclusion(token, collection_id, item_id)


def set_sitemap_exclusion(token: str, collection_id: str, item_id: str) -> None:
    """Best-effort call to Webflow's Sitemap API to exclude this item from search
    engines (used for the coupon variant). This is a genuinely separate API surface
    from the CMS Items endpoint above -- confirmed via Webflow's own MCP connector,
    which exposes update_item_sitemap_status/includeInSitemap for exactly this. The
    connector describes it as living under a "/beta" namespace but doesn't expose the
    literal REST path, so the URL below is inferred from Webflow's usual REST
    conventions and NOT confirmed against public docs.

    Deliberately non-fatal: if this guess is wrong, log it clearly and move on --
    the actual newsletter publish above must not fail over a nice-to-have SEO flag.
    """
    url = f"https://api.webflow.com/beta/collections/{collection_id}/items/{item_id}/sitemap"
    try:
        resp = requests.patch(
            url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"includeInSitemap": False},
            timeout=10,
        )
        if resp.ok:
            print(f"Excluded item {item_id} from sitemap (noindex)")
        else:
            print(
                f"NOINDEX WARNING: sitemap API call failed ({resp.status_code}: {resp.text}). "
                f"The newsletter itself published fine -- this item just wasn't excluded from "
                f"search engines. Toggle it manually in Webflow, or fix the endpoint path above.",
                file=sys.stderr,
            )
    except requests.RequestException as e:
        print(
            f"NOINDEX WARNING: sitemap API call raised {e}. "
            f"The newsletter itself published fine -- toggle indexing manually if needed.",
            file=sys.stderr,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", required=True, choices=["aurora", "youbravo"])
    parser.add_argument("--variant", required=True, help="e.g. standard, coupon")
    parser.add_argument("--locale", required=True, help="e.g. it, es")
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--hosted-path", required=True,
                         help="Path of the cleaned file relative to the Pages site root")
    parser.add_argument("--summary", default=None)
    parser.add_argument(
        "--noindex", action="store_true",
        help="Best-effort: exclude this item from search engines via Webflow's Sitemap "
             "API (see set_sitemap_exclusion() -- endpoint path is unconfirmed, fails "
             "safely if wrong).",
    )
    args = parser.parse_args()
    upsert_item(args)


if __name__ == "__main__":
    main()
