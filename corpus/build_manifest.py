#!/usr/bin/env python3
"""
Build corpus/MANIFEST.json — the integrity + sizing record for the BTP_RAG corpus.

Scans corpus/raw/, and for every secured source records: format, bytes, sha256,
pages/topics, raw text size, approx tokens, and an estimated chunk count. The
manifest is committed (small, no © text); the raw files under corpus/raw/ are not.

Run:  python3 corpus/build_manifest.py
"""
import hashlib
import json
import re
from pathlib import Path

import fitz  # PyMuPDF

RAW = Path(__file__).resolve().parent / "raw"
OUT = Path(__file__).resolve().parent / "MANIFEST.json"

# Empirical avg from the validated prototype: 471 content chunks over the AI Core
# doc => ~146 tokens/chunk after boilerplate strip + heading-bounded sectioning.
TOK_PER_CHUNK = 146
def est_chunks(tokens): return round(tokens / TOK_PER_CHUNK)
def sha256(b): return hashlib.sha256(b).hexdigest()


def front_date(page0_text):
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", page0_text)
    return m.group(1) if m else None


def pdf_entry(doc_id, path, source_url):
    data = path.read_bytes()
    d = fitz.open(path)
    text = "".join(p.get_text() for p in d)
    tokens = len(text) // 4
    return {
        "doc": doc_id, "format": "pdf", "file": path.name,
        "source_url": source_url,
        "version": front_date(d[0].get_text()),
        "bytes": len(data), "sha256": sha256(data),
        "pages": d.page_count, "toc_entries": len(d.get_toc()),
        "raw_text_chars": len(text), "approx_tokens": tokens,
        "est_chunks": est_chunks(tokens),
    }


def html_entry(doc_id, dir_path):
    summary = json.loads((dir_path / "_deliverable.json").read_text())
    rows = [json.loads(l) for l in (dir_path / "_pages.jsonl").read_text().splitlines()]
    # content hash over the per-page hashes (order-stable) for integrity
    combined = "".join(r["sha256"] for r in rows).encode()
    return {
        "doc": doc_id, "format": "html",
        "source_url": (f"https://help.sap.com/docs/{summary['product_url']}/"
                       f"{summary['deliverable_url']}/introduction"),
        "version": summary["version"],
        "deliverable_id": summary["deliverable_id"],
        "deliverable_loio": summary["deliverable_loio"],
        "build": summary["build"],
        "pages": summary["pages"], "sha256": sha256(combined),
        "raw_text_chars": summary["total_text_chars"],
        "approx_tokens": summary["approx_tokens"],
        "est_chunks": est_chunks(summary["approx_tokens"]),
    }


def main():
    docs = []
    docs.append(pdf_entry(
        "sap-ai-core", RAW / "sap-ai-core.pdf",
        "https://help.sap.com/docs/sap-ai-core"))
    docs.append(html_entry("sap-hana-vector", RAW / "sap-hana-vector"))
    docs.append(pdf_entry(
        "sap-ai-launchpad", RAW / "sap-ai-launchpad.pdf",
        "https://help.sap.com/docs/ai-launchpad"))

    # the AI Core doc's chunk count is measured by the validated prototype
    for d in docs:
        if d["doc"] == "sap-ai-core":
            d["chunks_measured"] = 471
            d["est_chunks_note"] = "471 measured by chunking/ prototype; est shown for method parity"

    totals = {
        "docs": len(docs),
        "pages": sum(d["pages"] for d in docs),
        "approx_tokens": sum(d["approx_tokens"] for d in docs),
        "est_chunks": sum(d["est_chunks"] for d in docs),
        "raw_bytes": sum(d.get("bytes", 0) for d in docs),
    }
    manifest = {
        "corpus": "BTP_RAG", "scope": "focused-3-docs",
        "embedding_model": "text-embedding-3-small",
        "vector_dim": 1536,
        "store_table": "BTP_RAG.CHUNKS",
        "documents": docs, "totals": totals,
    }
    OUT.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"{'doc':<20}{'fmt':<6}{'pages':>6}{'tokens':>10}{'est_chunks':>12}")
    for d in docs:
        print(f"{d['doc']:<20}{d['format']:<6}{d['pages']:>6}"
              f"{d['approx_tokens']:>10,}{d['est_chunks']:>12,}")
    print(f"{'TOTAL':<20}{'':<6}{totals['pages']:>6}"
          f"{totals['approx_tokens']:>10,}{totals['est_chunks']:>12,}")
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
