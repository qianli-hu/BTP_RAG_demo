#!/usr/bin/env python3
"""
Crawl the SAP HANA Cloud — Vector Engine Guide (corpus doc 2) into corpus/raw/.

This guide is HTML-only (no combined PDF), served by the new SAP Help Portal SPA.
The page text is delivered by a JSON API that the SPA calls; we hit it directly:

  1. /http.svc/deliverableMetadata  -> deliverable id, buildNo, version,
                                       and tocReadableUrlMapping (every topic loio + slug)
  2. /http.svc/pagecontent          -> the HTML `body` for one topic loio

Everything is resolved dynamically from the human-readable slug, so this stays
correct even when SAP bumps the buildNo. Output:

  corpus/raw/sap-hana-vector/
    _deliverable.json     deliverable id/build/version/loio + run summary
    _pages.jsonl          one row per page: order, loio, slug, source_url,
                          title, sha256, html_bytes, text_chars, github_link
    NNNN-<slug>.html      the raw citable HTML body of each topic, in TOC order

Run:  python3 corpus/crawl_hana_vector.py
"""
import gzip
import hashlib
import json
import re
import ssl
import time
import urllib.parse
import urllib.request
from pathlib import Path

# macOS python.org builds don't trust the system keychain; use certifi if present,
# else fall back to an unverified context (these are public, read-only fetches).
try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    SSL_CTX = ssl._create_unverified_context()

# --- corpus doc 2 identity (human-readable; IDs resolved at runtime) ---------
PRODUCT_URL     = "hana-cloud-database"
DELIVERABLE_URL = "sap-hana-cloud-sap-hana-database-vector-engine-guide"
ENTRY_TOPIC     = "introduction"
LANG, STATE     = "en-US", "PRODUCTION"

HOST    = "https://help.sap.com"
OUT_DIR = Path(__file__).resolve().parent / "raw" / "sap-hana-vector"
UA      = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
           "AppleWebKit/537.36 Chrome/124 Safari/537.36")
DELAY_S = 0.3            # polite pause between page fetches
RETRIES = 3


def get_json(url):
    """GET a help.sap.com JSON endpoint (handles gzip + light retry).

    404 is returned as None without retry: some TOC nodes are section
    containers (map loios) with no backing content page.
    """
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30, context=SSL_CTX) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == RETRIES:
                raise
            time.sleep(1.5 * attempt)
        except Exception:
            if attempt == RETRIES:
                raise
            time.sleep(1.5 * attempt)


def html_to_text(html):
    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"&nbsp;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def first_heading(html, fallback):
    m = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", html, flags=re.S | re.I)
    return html_to_text(m.group(1)) if m else fallback


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. deliverable metadata -> id, build, version, full topic list ----------
    meta_url = f"{HOST}/http.svc/deliverableMetadata?" + urllib.parse.urlencode({
        "product_url": PRODUCT_URL, "deliverable_url": DELIVERABLE_URL,
        "topic_url": ENTRY_TOPIC, "version": "LATEST",
        "language": LANG, "state": STATE})
    meta = get_json(meta_url)
    if meta.get("status") != "OK":
        raise SystemExit(f"deliverableMetadata failed: {meta.get('status')} {meta.get('message')}")
    data = meta["data"]
    deli = data["deliverable"]
    deliverable_id, build, version = deli["id"], deli["buildNo"], deli["version"]
    toc = data["readableUrls"]["tocReadableUrlMapping"]
    print(f"deliverable id={deliverable_id} build={build} version={version} | {len(toc)} topics")

    # 2. fetch each topic's HTML body -----------------------------------------
    rows, total_text = [], 0
    for i, entry in enumerate(toc, 1):
        loio, slug = entry["loio"], entry.get("url") or entry["loio"]
        page_url = f"{HOST}/http.svc/pagecontent?" + urllib.parse.urlencode({
            "deliverableInfo": 1, "deliverable_id": deliverable_id,
            "file_path": f"{loio}.html", "buildNo": build,
            "language": LANG, "state": STATE})
        try:
            pj = get_json(page_url)
        except Exception as e:
            print(f"  !! error {slug}: {e}")
            continue
        if not pj or pj.get("status") != "OK":
            print(f"  -- skip {slug}: no content page (container/map node)")
            continue
        body = pj["data"].get("body") or ""
        title = first_heading(body, slug.replace("-", " ").title())
        text = html_to_text(body)
        total_text += len(text)

        fname = f"{i:04d}-{slug[:80]}.html"
        (OUT_DIR / fname).write_text(body, encoding="utf-8")
        source_url = f"{HOST}/docs/{PRODUCT_URL}/{DELIVERABLE_URL}/{slug}"
        rows.append({
            "order": i, "loio": loio, "slug": slug, "source_url": source_url,
            "title": title, "file": fname,
            "sha256": hashlib.sha256(body.encode()).hexdigest()[:16],
            "html_bytes": len(body.encode()), "text_chars": len(text),
            "github_link": pj["data"].get("githubLink")})
        print(f"  {i:3d}/{len(toc)}  {title[:60]:60s} {len(text):6d} chars")
        time.sleep(DELAY_S)

    # 3. write manifests ------------------------------------------------------
    with (OUT_DIR / "_pages.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    est_chunks = round(total_text / 4 / 220 * 1.3)   # ~220 tok target, overlap fudge
    summary = {
        "doc": "sap-hana-vector", "deliverable_id": deliverable_id,
        "deliverable_loio": data.get("deliverableLoio"), "build": build,
        "version": version, "product_url": PRODUCT_URL,
        "deliverable_url": DELIVERABLE_URL, "source_format": "html",
        "pages": len(rows), "total_text_chars": total_text,
        "approx_tokens": round(total_text / 4), "est_chunks": est_chunks}
    (OUT_DIR / "_deliverable.json").write_text(json.dumps(summary, indent=2))
    print(f"\nDONE  pages={len(rows)}  text={total_text:,} chars  "
          f"~{summary['approx_tokens']:,} tok  est_chunks~{est_chunks}")
    print(f"saved -> {OUT_DIR}")


if __name__ == "__main__":
    main()
