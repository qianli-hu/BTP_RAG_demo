# BTP_RAG

A RAG knowledge assistant grounded in **SAP BTP documentation**, running on **SAP AI Core + Generative AI Hub + HANA Cloud**, with citations and an eval harness.

👉 **Start with [`HANDOFF.md`](HANDOFF.md)** — full context, architecture, design decisions, v1 scope, and open blockers.

## Quickstart (chunking prototype — already working)
```bash
python3 -m pip install -r requirements.txt
cd chunking
bash fetch_docs.sh          # pull the seed SAP AI Core PDF into docs/ (gitignored)
python3 chunk_demo.py       # fitz+pdfplumber baseline → chunks_preview.md
python3 chunk_docling.py    # Docling → clean borderless tables
```

## Status
- ✅ `chunking/` — parsing + chunking validated (prose ready; Docling fixes borderless tables; TOC→breadcrumb mapping verified).
- ⏳ `ingest/ retrieve/ eval/ app/` — TODO (see HANDOFF §5, §10).

## Setup
Copy `.env.example` → `.env` and fill in your SAP AI Core + HANA Cloud credentials. Never commit `.env` or `docs/`.
