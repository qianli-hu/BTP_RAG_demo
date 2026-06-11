#!/usr/bin/env python3
"""
Unified structure-aware chunker for the BTP_RAG corpus (ingest/DESIGN.md).

  PDF  (sap-ai-core, sap-ai-launchpad) : fitz prose + font-size headings + boilerplate strip
  HTML (sap-hana-vector)               : bs4 h1-h4 + native <table> -> Markdown

Emits the DESIGN §D6 record schema to ingest/out/chunks.jsonl, a committed
ingest/out/chunks_report.md, and refreshes measured chunk counts in corpus/MANIFEST.json.

Borderless PDF tables are handled by a separate Docling pass (chunk_tables.py) that
merges `table` chunks into chunks.jsonl — see DESIGN §D9/§10.

Run:  python3 ingest/chunk.py
"""
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
RAW  = ROOT / "corpus" / "raw"
OUT  = ROOT / "ingest" / "out"

# --- params (DESIGN §D8, locked) ---------------------------------------------
TARGET_TOK, MAX_TOK, MIN_TOK, OVERLAP_SENT = 300, 512, 25, 1
FRONTMATTER = {"Content"}                                     # PDF TOC / front-matter sections to skip
def toks(s): return max(1, len(s) // 4)                       # ~chars/4 budget heuristic
def sha256_16(s): return hashlib.sha256(s.encode()).hexdigest()[:16]
def sec_id(doc, path): return hashlib.sha1(f"{doc}::{path}".encode()).hexdigest()[:12]

def split_sentences(text):
    """Sentence split, then hard-split any sentence over MAX_TOK by word windows
    (handles flattened tables / code / lists that carry no sentence punctuation)."""
    out = []
    for s in re.split(r"(?<=[.!?])\s+", text):
        if toks(s) <= MAX_TOK:
            out.append(s); continue
        cur, ct = [], 0
        for w in s.split():
            if cur and ct + toks(w) > TARGET_TOK:
                out.append(" ".join(cur)); cur, ct = [], 0
            cur.append(w); ct += toks(w)
        if cur: out.append(" ".join(cur))
    return out

def pack(text):
    """Section text -> list of target-bounded bodies, 1-sentence overlap, hard max,
    sub-MIN_TOK pieces dropped."""
    bodies, cur, ct = [], [], 0
    for s in split_sentences(text):                  # each s <= MAX_TOK
        if cur and ct + toks(s) > TARGET_TOK:
            bodies.append(" ".join(cur))
            cur = cur[-OVERLAP_SENT:]; ct = sum(toks(x) for x in cur)
            if ct + toks(s) > MAX_TOK:                # overlap too big to keep s within MAX
                cur, ct = [], 0
        cur.append(s); ct += toks(s)
    if cur: bodies.append(" ".join(cur))
    return [b for b in bodies if toks(b) >= MIN_TOK]

# --- corpus registry ---------------------------------------------------------
DOCS = {
    "sap-ai-core":      {"fmt": "pdf",  "title": "SAP AI Core",
                         "file": "sap-ai-core.pdf", "version": "2026-06-04",
                         "url": "https://help.sap.com/docs/sap-ai-core"},
    "sap-ai-launchpad": {"fmt": "pdf",  "title": "SAP AI Launchpad",
                         "file": "sap-ai-launchpad.pdf", "version": "2026-06-04",
                         "url": "https://help.sap.com/docs/ai-launchpad"},
    "sap-hana-vector":  {"fmt": "html", "title": "SAP HANA Cloud Vector Engine Guide",
                         "dir": "sap-hana-vector", "version": "2026_1_QRC"},
}

# ============================================================ PDF (fitz prose)
def font_level(size):
    if size >= 17:   return 1
    if size >= 13.5: return 2
    if size >= 11.5: return 3
    return 0                                                  # body text

def chunk_pdf(doc_id):
    meta = DOCS[doc_id]
    doc = fitz.open(RAW / meta["file"])

    # 1. boilerplate = lines repeating on >25% of pages (headers/footers/page nos)
    freq, pages_lines = Counter(), []
    for pg in doc:
        lines = []
        for b in pg.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                txt = "".join(s["text"] for s in l["spans"]).strip()
                size = max((s["size"] for s in l["spans"]), default=0)
                if txt:
                    lines.append((txt, round(size, 1))); freq[txt] += 1
        pages_lines.append(lines)
    boiler = {t for t, c in freq.items()
              if c > doc.page_count * 0.25 or re.fullmatch(r"\d+|PUBLIC|© \d+ SAP SE.*", t)}
    is_toc = lambda t: bool(re.search(r"\.\s*\.\s*\.\s*\.", t)        # dot-leader TOC lines
                            or re.fullmatch(r"\d+(\.\d+)*", t))      # bare outline numbers

    # 2. heading-bounded sectioning -> token-bounded, sentence-overlapped prose chunks
    elements, path, buf, buf_page = [], {}, [], None

    def cur_paths():
        levels = sorted(path)
        full = " › ".join(path[k] for k in levels)
        parent = " › ".join(path[k] for k in levels[:-1]) if len(levels) > 1 else ""
        return full, parent

    def flush():
        nonlocal buf
        text = " ".join(buf).strip(); buf = []
        if toks(text) < MIN_TOK:
            return
        full, parent = cur_paths()
        if not full or full.split(" › ")[0] in FRONTMATTER:    # skip TOC / front-matter
            return
        for body in pack(text):
            elements.append({"element_type": "prose", "section_path": full,
                             "parent_path": parent, "page": buf_page, "text": body})

    for pno, lines in enumerate(pages_lines, 1):            # 1-based page numbers
        for txt, size in lines:
            if txt in boiler or is_toc(txt):
                continue
            lv = font_level(size)
            if lv:                                          # heading -> close section
                flush()
                path[lv] = txt
                for k in [k for k in path if k > lv]:
                    del path[k]
                buf_page = pno
            else:
                if buf_page is None: buf_page = pno
                buf.append(txt)
    flush()

    for e in elements:                                      # PDF: doc-level url + page
        e.update(doc=doc_id, source_url=meta["url"], topic_slug=None,
                 doc_version=meta["version"])
    return elements, {"pages": doc.page_count, "boiler": len(boiler)}

# ============================================================ HTML (bs4)
def html_table_md(tbl):
    rows = []
    for tr in tbl.find_all("tr"):
        cells = [c.get_text(" ", strip=True).replace("\n", " ")
                 for c in tr.find_all(["th", "td"])]
        if cells: rows.append(cells)
    if not rows: return ""
    w = max(len(r) for r in rows); rows = [r + [""] * (w - len(r)) for r in rows]
    out = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join(["---"] * w) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(out)

def chunk_html(doc_id):
    meta = DOCS[doc_id]
    d = RAW / meta["dir"]
    rows = [json.loads(l) for l in (d / "_pages.jsonl").read_text().splitlines()]
    elements = []

    for row in rows:
        html = (d / row["file"]).read_text()
        soup = BeautifulSoup(html, "lxml")
        for bad in soup(["script", "style", "nav", "header", "footer"]):
            bad.decompose()
        body = soup.body or soup
        title = row["title"]
        path = {1: title}                                   # page title = root breadcrumb
        buf = []

        def cur_paths():
            levels = sorted(path)
            full = " › ".join(path[k] for k in levels)
            parent = " › ".join(path[k] for k in levels[:-1]) if len(levels) > 1 else ""
            return full, parent

        def emit_prose():
            nonlocal buf
            text = " ".join(buf).strip(); buf = []
            if toks(text) < MIN_TOK:
                return
            full, parent = cur_paths()
            for body in pack(text):
                elements.append(_html_el("prose", full, parent, body, row, meta))

        for tag in body.find_all(["h1", "h2", "h3", "h4", "p", "pre", "li", "dt", "dd", "table"]):
            if tag.name != "table" and tag.find_parent("table"):
                continue                                    # cells handled by the table itself
            if tag.name in ("h1", "h2", "h3", "h4"):
                emit_prose()
                lv = int(tag.name[1])
                path[lv] = tag.get_text(" ", strip=True)
                for k in [k for k in path if k > lv and k != 1]:
                    del path[k]
            elif tag.name == "table":
                emit_prose()
                md = html_table_md(tag)
                if md:
                    full, parent = cur_paths()
                    elements.append(_html_el("table", full, parent, md, row, meta))
            else:
                t = tag.get_text(" ", strip=True)
                if t: buf.append(t)
        emit_prose()
    return elements, {"pages": len(rows), "boiler": 0}

def _html_el(etype, full, parent, text, row, meta):
    return {"element_type": etype, "section_path": full, "parent_path": parent,
            "page": None, "text": text, "doc": meta_key(meta), "source_url": row["source_url"],
            "topic_slug": row["slug"], "doc_version": meta["version"]}

def meta_key(meta):                                          # reverse-lookup doc id from meta
    return next(k for k, v in DOCS.items() if v is meta)

# ============================================================ finalize + write
def finalize(elements):
    """Assign section_id, positional ids, ordinals, prev/next, parent_section_id, embed_text.

    `parent_section_id` references the nearest ANCESTOR *section* (a section_id) that
    actually has chunks — heading-only containers produce no chunks, so we walk up past
    them. Small-to-big parent expansion = `SELECT ... WHERE section_id = parent_section_id`.
    `prev_id`/`next_id` reference neighbor *chunk ids* in reading order. (Two id spaces by
    design: a section can hold several chunks; pointing parent at one chunk would be arbitrary.)
    """
    content_paths = {(e["doc"], e["section_path"]) for e in elements}
    def nearest_parent(doc, parent_path):
        parts = parent_path.split(" › ") if parent_path else []
        while parts:
            cand = " › ".join(parts)
            if (doc, cand) in content_paths:
                return sec_id(doc, cand)
            parts = parts[:-1]
        return None

    by_section = Counter()
    for e in elements:
        doc, sp = e["doc"], e["section_path"]
        e["section_id"] = sec_id(doc, sp)
        ordn = by_section[(doc, e["section_id"])]; by_section[(doc, e["section_id"])] += 1
        e["id"] = f"{doc}#{e['section_id']}#{ordn:02d}"
        e["parent_section_id"] = nearest_parent(doc, e["parent_path"])
        e["token_count"] = toks(e["text"])
        e["content_sha256"] = sha256_16(e["text"])
        title = DOCS[doc]["title"]
        e["embed_text"] = f"{title} › {sp}\n\n{e['text']}" if sp else e["text"]

    # prev/next within each doc, in PAGE reading order (interleaves merged table chunks)
    by_doc = {}
    for i, e in enumerate(elements):
        e["_seq"] = i
        by_doc.setdefault(e["doc"], []).append(e)
    for seq in by_doc.values():
        seq.sort(key=lambda e: (e["page"] if e["page"] is not None else 0, e["_seq"]))
        for i, e in enumerate(seq):
            e["prev_id"] = seq[i - 1]["id"] if i > 0 else None
            e["next_id"] = seq[i + 1]["id"] if i < len(seq) - 1 else None

    fields = ["id", "doc", "element_type", "section_path", "section_id", "parent_section_id",
              "prev_id", "next_id", "page", "topic_slug", "source_url", "doc_version",
              "token_count", "text", "embed_text", "content_sha256"]
    return [{k: e.get(k) for k in fields} for e in elements]

def write_report(chunks, stats):
    OUT.mkdir(parents=True, exist_ok=True)
    by_doc = {}
    for c in chunks: by_doc.setdefault(c["doc"], []).append(c)
    lines = ["# Chunking report\n",
             f"Total chunks: **{len(chunks)}** across {len(by_doc)} docs "
             f"(target {TARGET_TOK} tok, max {MAX_TOK}, min {MIN_TOK}, overlap {OVERLAP_SENT} sent)\n",
             "| doc | chunks | prose | table | avg tok | max tok | pages |",
             "|---|--:|--:|--:|--:|--:|--:|"]
    for doc, cs in by_doc.items():
        tk = [c["token_count"] for c in cs]
        pr = sum(1 for c in cs if c["element_type"] == "prose")
        tb = sum(1 for c in cs if c["element_type"] == "table")
        lines.append(f"| {doc} | {len(cs)} | {pr} | {tb} | {sum(tk)//len(tk)} | "
                     f"{max(tk)} | {stats[doc]['pages']} |")
    # token histogram
    buckets = Counter(min(c["token_count"] // 100 * 100, 500) for c in chunks)
    lines += ["", "Token histogram (100-tok buckets):"]
    for b in sorted(buckets):
        lines.append(f"  {b:>3}-{b+99:<3}: {'#' * (buckets[b] * 60 // len(chunks))} {buckets[b]}")
    # samples
    lines += ["", "---", "## Samples (1 per doc)"]
    for doc, cs in by_doc.items():
        c = next((x for x in cs if x["element_type"] == "prose" and x["section_path"]), cs[0])
        lines += [f"\n**{doc}** · `{c['id']}` · {c['token_count']} tok · "
                  f"page {c['page']} · [src]({c['source_url']})",
                  f"`{c['section_path']}`", "", "> " + c["text"][:500].replace("\n", " ") +
                  ("…" if len(c["text"]) > 500 else "")]
    (OUT / "chunks_report.md").write_text("\n".join(lines) + "\n")

def update_manifest(chunks):
    mf = ROOT / "corpus" / "MANIFEST.json"
    m = json.loads(mf.read_text())
    counts = Counter(c["doc"] for c in chunks)
    for d in m["documents"]:
        if d["doc"] in counts:
            d["chunks_measured"] = counts[d["doc"]]
            d.pop("est_chunks_note", None)
    m["totals"]["chunks_measured"] = sum(counts.values())
    mf.write_text(json.dumps(m, indent=2) + "\n")

def load_pdf_tables():
    """Raw Docling table elements (ingest/chunk_tables.py output), if present.
    Merged BEFORE finalize() so tables get ids/prev/next/parent_section_id and flow
    into the report + manifest — no separate stale merge step."""
    p = OUT / "pdf_tables.jsonl"
    if not p.exists():
        return []
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    # drop low-value "What's New" changelog tables: huge, Q&A-irrelevant, and Docling
    # garbles their wide headers (word-splitting). Keeps the reference/metering tables.
    return [r for r in rows if "What's New" not in (r.get("section_path") or "")]

def attach_table_parents(prose_elements, tables):
    """Map each Docling table into the prose heading tree by page position: its parent is
    the deepest prose section on (or just before) the table's page. Gives tables a real
    `parent_section_id` for small-to-big expansion (Docling alone gives them no parent)."""
    by_doc = {}
    for e in prose_elements:
        if e.get("page") is not None and e.get("section_path"):
            by_doc.setdefault(e["doc"], []).append((e["page"], e["section_path"]))
    for v in by_doc.values():
        v.sort(key=lambda x: x[0])
    for t in tables:
        cands, pg = by_doc.get(t["doc"], []), t.get("page")
        prev = [sp for p, sp in cands if pg is not None and p <= pg]
        t["parent_path"] = prev[-1] if prev else (cands[0][1] if cands else "")

def main():
    elements, stats = [], {}
    for doc_id, meta in DOCS.items():
        els, st = (chunk_pdf if meta["fmt"] == "pdf" else chunk_html)(doc_id)
        elements += els; stats[doc_id] = st
        print(f"  {doc_id:18s} {len(els):4d} chunks  ({st['pages']} pages)")
    tables = load_pdf_tables()
    if tables:
        attach_table_parents(elements, tables)     # give tables a parent in the prose heading tree
        elements += tables
        print(f"  + {len(tables)} Docling PDF-table chunks (from pdf_tables.jsonl)")
    else:
        print("  (no pdf_tables.jsonl yet — run ingest/chunk_tables.py for borderless PDF tables)")
    chunks = finalize(elements)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "chunks.jsonl").open("w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    write_report(chunks, stats)
    update_manifest(chunks)
    print(f"\nTOTAL {len(chunks)} chunks -> ingest/out/chunks.jsonl")
    print("wrote ingest/out/chunks_report.md ; updated corpus/MANIFEST.json")

if __name__ == "__main__":
    main()
