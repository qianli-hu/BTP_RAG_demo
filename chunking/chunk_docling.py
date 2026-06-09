"""
Docling-based chunker for SAP BTP docs — fixes borderless-table extraction.
Unlike the pdfplumber baseline (chunk_demo.py), Docling uses ML layout models,
so SAP Help's borderless tables come out as clean Markdown.

Run:  python3 chunk_docling.py [path.pdf]
First run downloads Docling's layout + table models (~hundreds MB).
"""
import sys, json, hashlib
from docling.document_converter import DocumentConverter

PDF = sys.argv[1] if len(sys.argv) > 1 else "docs/sap-ai-core-p8.pdf"
def h(s): return hashlib.sha1(s.encode()).hexdigest()[:10]

conv = DocumentConverter()
doc = conv.convert(PDF).document

# Option A: whole-doc Markdown (tables preserved as Markdown) ----------------
md = doc.export_to_markdown()
open("docling_out.md", "w").write(md)

# Option B: structure-aware chunks via Docling's HybridChunker --------------
# (tokenization-aware, respects headings + keeps tables intact)
try:
    from docling.chunking import HybridChunker
    chunker = HybridChunker()           # uses a default tokenizer
    chunks = []
    for ch in chunker.chunk(doc):
        meta = ch.meta.export_json_dict() if hasattr(ch, "meta") else {}
        headings = meta.get("headings", [])
        chunks.append({
            "section_path": " › ".join(headings) if headings else "",
            "hash": h(ch.text),
            "text": ch.text,
        })
    json.dump(chunks, open("docling_chunks.json", "w"), indent=2, ensure_ascii=False)
    print(f"HybridChunker -> {len(chunks)} chunks (docling_chunks.json)")
    for c in chunks[:3]:
        print(f"\n[{c['hash']}] {c['section_path']}\n{c['text'][:300]}")
except Exception as e:
    print("HybridChunker unavailable, exported Markdown only:", e)

import re
tables = re.findall(r"(?:\|.*\n)+", md)
print(f"\nMarkdown tables extracted: {len(tables)}  (docling_out.md, {len(md)} chars)")
