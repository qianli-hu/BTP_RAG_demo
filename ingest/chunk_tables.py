#!/usr/bin/env python3
"""
Docling table pass — extract borderless tables from the PDF docs as clean Markdown,
tag each with its nearest heading + page, and write RAW table *elements* (pre-finalize)
to ingest/out/pdf_tables.jsonl.

Kept SEPARATE from chunks.jsonl so a long/slow run can never corrupt the validated
prose chunks. ingest/chunk.py auto-loads this file (if present) and runs the tables
through the SAME finalize() as prose — so they get ids / prev / next / parent_section_id
and flow into chunks_report.md + MANIFEST.json. No separate merge step.

Workflow:  python3 ingest/chunk_tables.py   (slow, once)  ->  python3 ingest/chunk.py
Slow: first run downloads Docling layout+table models (~hundreds MB). Run in background.
"""
import json
from pathlib import Path

from docling.document_converter import DocumentConverter

ROOT = Path(__file__).resolve().parent.parent
RAW  = ROOT / "corpus" / "raw"
OUT  = ROOT / "ingest" / "out"
TARGET = {  # doc_id -> (pdf, title, source_url, version)
    "sap-ai-core":      ("sap-ai-core.pdf", "SAP AI Core",
                         "https://help.sap.com/docs/sap-ai-core", "2026-06-04"),
    "sap-ai-launchpad": ("sap-ai-launchpad.pdf", "SAP AI Launchpad",
                         "https://help.sap.com/docs/ai-launchpad", "2026-06-04"),
}
def label_of(item):
    lab = getattr(item, "label", "")
    return str(getattr(lab, "value", lab)).lower()

def table_md(tbl, doc):
    for attempt in (lambda: tbl.export_to_markdown(doc), lambda: tbl.export_to_markdown()):
        try:
            md = attempt()
            if md and md.strip():
                return md.strip()
        except Exception:
            continue
    return ""

def page_of(item):
    try:
        return int(item.prov[0].page_no)
    except Exception:
        return None

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conv = DocumentConverter()
    rows = []
    for doc_id, (pdf, _title, url, ver) in TARGET.items():
        dd = conv.convert(str(RAW / pdf)).document
        heading, n = None, 0
        for item, _lvl in dd.iterate_items():
            lab = label_of(item)
            if "section_header" in lab or lab in ("title", "subtitle", "heading"):
                t = getattr(item, "text", None)
                if t: heading = t.strip()
            elif type(item).__name__ == "TableItem":
                if heading and "What's New" in heading:  # skip low-value changelog tables
                    continue
                md = table_md(item, dd)
                if not md or md.count("|") < 4:        # skip trivial/failed extractions
                    continue
                # RAW element (no ids/hashes) — finalize() in chunk.py assigns those
                rows.append({
                    "doc": doc_id, "element_type": "table",
                    "section_path": heading or "(table)", "parent_path": "",
                    "page": page_of(item), "topic_slug": None,
                    "source_url": url, "doc_version": ver, "text": md})
                n += 1
        print(f"  {doc_id:18s} {n} tables")
    with (OUT / "pdf_tables.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"wrote {len(rows)} raw table elements -> ingest/out/pdf_tables.jsonl "
          f"(now run: python3 ingest/chunk.py)")

if __name__ == "__main__":
    main()
