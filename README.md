# Newsletter publishing pipeline

Drop a raw Braze export into the matching folder and push to `main` —
GitHub Actions cleans it, publishes it to GitHub Pages, and syncs it to
the Webflow "Newsletters" CMS Collection automatically.

## Where to drop files

```
raw-newsletters/aurora/coupon/2026-07-07.html      (Aurora, with coupon)
raw-newsletters/aurora/standard/2026-07-07.html    (Aurora, no coupon)
raw-newsletters/youbravo/standard/2026-07-07.html  (Youbravo)
```

Name the file by send date (`YYYY-MM-DD.html`). The folder it sits in is
what tells the pipeline the brand and variant -- no other config needed
per send.

## One-time setup still required after uploading this repo

See the setup checklist at the bottom of
`.github/workflows/publish-newsletter.yml`, and the full walkthrough
from the chat that generated this repo, covering:

1. Enabling GitHub Pages (Settings -> Pages -> Deploy from branch -> /docs)
2. Creating the Webflow "Newsletters" CMS Collection with the right fields
3. Adding repo secrets: `WEBFLOW_TOKEN`, `WEBFLOW_COLLECTION_ID`
4. Adding repo variable: `PAGES_BASE_URL`
5. Wiring up the noindex field (see the TODO in `scripts/sync_webflow.py`)
