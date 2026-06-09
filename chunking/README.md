# RAG chunking demo — SAP BTP docs

A quick, honest look at PDF chunking quality on a **real SAP document**
(the official *SAP AI Core* guide, 190 pp, from SAP Help Portal). Purpose: decide
what parser the production RAG pipeline (`SAP/prep/06-rag-design.md`) should use for
the SAP corpus.

## Files
- `chunk_demo.py` — baseline: **fitz + pdfplumber**. Heading-aware sectioning (font-size
  hierarchy), boilerplate stripping, sentence-overlap chunking, table→Markdown.
- `chunk_docling.py` — upgrade: **Docling** (ML layout models) for borderless tables.
- `fetch_docs.sh` — downloads the SAP PDF into `docs/` (gitignored — SAP © content).
- `requirements.txt` — deps.

## How to run
```bash
bash fetch_docs.sh          # pull the SAP AI Core PDF into docs/
python3 chunk_demo.py       # baseline -> chunks_preview.md
python3 chunk_docling.py    # docling  -> docling_out.md / docling_chunks.json
```

## Findings

**Baseline (fitz + pdfplumber) — prose: great; tables: broken.**
- ✅ 504 chunks / 190 pp, ~146 tok avg, 265 boilerplate lines stripped.
- ✅ Clean heading breadcrumbs, e.g. `What Is SAP AI Core? › 1.1.1 Metering and Pricing for Generative AI › Metering for Generative AI` — gold for citations/metadata.
- ✅ Sentence-boundary chunks with working overlap.
- ❌ **Tables → 0.** SAP Help tables are **borderless**, invisible to a line-based detector, and get flattened into scrambled prose.

**The failure, concretely** — a resource table on p.8 came out of pdfplumber as:
> "…shown in the table: Resource Type SAP AI Core Resources Unit of Measure (UoM) Provisioning of foundation models Access to (LLMs)… Tokens… Baseline Grounding Service Gigabyte day Inference Observability Storing inference records DataVolume…"

**Docling fixes it** — same table, ML layout reconstruction:

| Resource Type | SAP AI Core Resources | Unit of Measure (UoM) |
|---|---|---|
| Provisioning of foundation models | Access to (LLMs) and other generative AI capabilities | Tokens |
| Baseline / Grounding Service | … | Gigabyte day |
| Inference Observability | Storing inference records | DataVolume |

## Takeaway for the production pipeline
- **Prose + structure:** the fitz heading-aware approach is production-ready and fast.
- **Tables:** use **Docling** (or unstructured) for the SAP corpus — borderless tables
  require ML layout, not ruled-line detection. For the worst pages, fall back to VLM
  extraction.
- **Prefer HTML source** where SAP Help offers it — cleaner than any PDF parser.
- **OCR:** unneeded for digital SAP PDFs (they have a text layer). Docling bundles it
  for scanned docs if ever required.

> `docs/` (raw SAP PDFs) and the full text dumps are gitignored — SAP-copyright content.
> Only the code + a short preview live in the repo.
