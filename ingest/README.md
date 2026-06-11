# ingest/ — chunk → (embed) → load into HANA

Unified ingestion. See [`DESIGN.md`](../docs/archive/CHUNKING_DESIGN.md) for the full design + schema.

**Built + validated:**
- `chunk.py` — fitz prose + bs4 HTML (incl. HTML tables) → `out/chunks.jsonl` (+ committed
  `out/chunks_report.md`), tags `section_path`/`parent_section_id` from **font-size headings**
  (PDF) / `h1`–`h4` (HTML); auto-merges Docling tables if present. Refreshes `MANIFEST.json`.
- `chunk_tables.py` — Docling pass: borderless PDF tables → Markdown → `out/pdf_tables.jsonl`
  (slow; run once, then re-run `chunk.py`).
- `load_hana.py` — load chunks into HANA Cloud + `FULLTEXT INDEX` for sparse/keyword search.

**Flow:**
```
chunk_tables.py (slow, once) → chunk.py → out/chunks.jsonl
   → embed (provider adapter; v1 OpenAI text-embedding-3-small → REAL_VECTOR(1536))
   → load_hana.py → BTP_RAG.CHUNKS
```

**Next (TODO):** `embed.py` (OpenAI embeddings → `UPDATE EMBEDDING`); then vector +
hybrid-RRF retrieval lives in `../retrieve/`.
