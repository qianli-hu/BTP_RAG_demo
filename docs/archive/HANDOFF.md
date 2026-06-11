> **ARCHIVED design history** — superseded by the [README](../../README.md). Kept for decision provenance.

# BTP_RAG — Handoff

**Status:** design adjusted for 10-hour v1; corpus secured, chunking prototyped. Ready for v1 build.
**Owner:** Qianli Hu (PhD math; strong ML/RAG background — multi-agent systems, LoRA fine-tuning, eval harnesses, prior hybrid retrieval with bge-m3 + BM25).
**Last updated:** 2026-06-09.

---

## 1. What we're building & why

A **RAG knowledge assistant** that answers questions grounded in **SAP BTP documentation**, with **citations**, a strict "not in knowledge base" path, and an **eval harness**.

**10-hour v1 reality:** use **SAP HANA Cloud** from the BTP Trial account as the real SAP vector/full-text database. Use a pluggable AI provider for embeddings, answer generation, and LLM-as-judge; default to OpenAI because ordinary BTP Trial does not reliably include SAP AI Core / Generative AI Hub entitlement. Keep the provider boundary clean so the same code can switch to SAP Generative AI Hub in a customer/commercial BTP account.

**Why it exists (the goal that constrains decisions):**
- It's a **portfolio artifact** for an SAP interview — *AI Customer Adoption Expert Advisor* (req 449530), interview ~2026-06-11. The v1 pitch line: *"I built a BTP-native RAG retrieval layer on SAP HANA Cloud Vector, exposed it through a FastAPI endpoint, and gated grounded answers with citations plus an LLM-as-judge eval harness. Model access is isolated behind a provider adapter, so the same pipeline can switch from OpenAI to SAP Generative AI Hub when AI Core entitlement is available."*
- Secondary: it must **demonstrate hands-on BTP/AI** and be **corpus-agnostic** (swap SAP docs → SEC 10-Ks for finance roles, etc.).
- Deliverables for the portfolio: **GitHub repo + a 3–5 min demo video + a one-page case study.**

This is real production-minded work, not a toy: grounding, citations, eval gates, and a "no answer found" path are required, not optional.

---

## 2. Target architecture

```
corpus (SAP Help docs; HTML-first, PDF fallback)
  → parse:  fitz (prose)  +  Docling (borderless tables → Markdown)
  → chunk:  structure-aware, sentence-overlap, boilerplate-stripped
  → tag:    section_path + parent_section_id from font-size headings (PDF) / h1–h4 (HTML), no LLM
  → embed:  provider adapter (v1: OpenAI text-embedding-3-small; v2: SAP Gen AI Hub)
  → store:  HANA Cloud  (REAL_VECTOR column + full-text/BM25 index)
  → retrieve: HYBRID  (vector + BM25, fused via Reciprocal Rank Fusion)
            → rerank: HANA cross-encoder (CROSS_ENCODE)   [v2]
  → generate: provider adapter (v1: OpenAI chat model; v2: SAP Gen AI Hub model)
            → answer + citations  (+ "not in knowledge base" path)
  → eval:   provider adapter LLM-as-judge over a gold Q&A set
  → serve:  FastAPI local (v1)  →  Cloud Foundry (v2)
```

---

## 3. Key design decisions (with rationale)

**Corpus.** SAP Help Portal docs; seed doc = the **SAP AI Core guide** (190 pp, on-theme). Prefer **HTML source** over PDF where available (cleaner structure). What justifies RAG is *total token volume + need for grounded/cited/fresh answers* — not raw doc count; a few thousand pages suffices.

**Parsing.** Proven empirically (see `chunking/`):
- **fitz (PyMuPDF)** for prose — fast, clean text layer, good reading order.
- **Docling** for tables — SAP Help tables are **borderless**, so line-based extractors (pdfplumber) return **0 tables** and flatten them into scrambled prose. Docling's ML layout reconstructs them as clean Markdown. Verified on the seed doc.
- pdfplumber only for *ruled* tables. VLM extraction as a last resort for the nastiest pages.
- OCR not needed (SAP PDFs have a text layer); Docling bundles it if ever required.

**Chunking + tagging (no LLM).**
- Structure-aware: chunk on heading boundaries, ~200–800 tok target, ~1-sentence overlap, strip repeating header/footer boilerplate.
- **`section_path` + `parent_section_id` come from font-size heading detection (PDF, implemented + validated) and HTML `h1`–`h4` tags (HTML source)**; PDF tables are mapped into the prose heading tree by page position. All deterministic — no model. *(Embedded-TOC `doc.get_toc()` is a validated cross-check kept as a v2 enhancement, not the v1 primary.)*
- `parent_section_id` is a **section id**, not a generated summary and not a single chunk id. Small-to-big retrieval expands it with `WHERE section_id = parent_section_id`, so the LLM sees original source chunks from the parent section only during query-time context assembly.
- "Embedded TOC" = the bookmark/outline tree stored *inside* the PDF; unrelated to vector embeddings. Tagging happens **before** any embedding.

**Metadata — three planes (don't conflate):**
1. **Embedded text** (retrieval key): the chunk + a *short* situating prefix only. Do NOT stuff the full overview/neighbors here — it dilutes the vector and kills discrimination.
2. **Stored metadata** (filter/cite/navigate): `doc, section_path, section_id, parent_section_id, prev_id, next_id, page, topic_slug, doc_version, source_url, content_sha256, element_type`.
3. **Assembled context** (what the LLM sees): retrieved chunk **+ optional neighbors/parent-section chunks** pulled at query time (small-to-big). `prev_id`/`next_id` are chunk ids; `parent_section_id` expands to all chunks in the nearest parent section. Store references, not duplicated text.

**Embeddings.** v1 uses OpenAI `text-embedding-3-small` with `REAL_VECTOR(1536)` because it is stable, cheap, and fast. The embedding provider is an adapter. A provider/model switch always requires a full corpus re-embed and query re-embed, even if the dimension stays the same; if the dimension changes, recreate/reload the HANA vector column/table as well.

**Retrieval.** Hybrid = HANA **vector** (`COSINE_SIMILARITY`) + HANA **BM25/full-text**, fused via **RRF** (ranks, not scores). SAP's `langchain-hana` exposes `HANABm25Retriever` / `HANAHybridRetriever`. v1 computes embeddings externally and performs search/ranking in HANA. v2 can add HANA's in-DB cross-encoder (`CROSS_ENCODE`, NLP service) so retrieve→fuse→rerank stays inside HANA.

**LLM.** v1 uses an OpenAI chat model for answer generation and LLM-as-judge. Use a stronger/different judge model than the answer model to reduce self-preference bias. SAP Generative AI Hub remains the target enterprise provider for v2 once entitlement is available. Do not bake model-specific calls into retrieval or app code; keep `LLMProvider` and `EmbeddingProvider` swappable.

**Data boundary.** v1 sends public SAP documentation chunks and user queries to OpenAI for embeddings/generation/judging. The SAP-native part is HANA Cloud storage, vector search, full-text/BM25, and ranking. In an enterprise/customer setting, switching the provider to SAP Generative AI Hub is the governance/data-residency upgrade.

**Hosting.** v1 local FastAPI (`POST /ask`) hitting HANA Cloud + OpenAI → optional Streamlit client → v2 Cloud Foundry (`cf push`, bind HANA, inject model-provider secrets via env vars) → optional Kyma + XSUAA auth.

**Rule-book / cross-reference handling** (relevant if corpus is rule-like): atomic clause-level chunks; capture clause numbering + cross-refs (`"see 7.3"`) via regex → a cross-reference **graph** (HANA Knowledge Graph) for hybrid vector+graph retrieval; carry `version`/`effective_date` so retrieval filters to the rule in force.

---

## 4. Current state (what's done)

In `chunking/` (prototyped & validated on the SAP AI Core doc):
- `chunk_demo.py` — fitz+pdfplumber baseline: heading-aware sectioning, boilerplate strip (265 lines), sentence-overlap chunks (504 chunks/190pp). **Prose: production-ready. Tables: fails on borderless.**
- `chunk_docling.py` — Docling: same borderless tables → clean Markdown. **Fix confirmed.**
- `chunks_preview.md` — sample output.
- `fetch_docs.sh` — pulls the seed PDF into `docs/` (gitignored, SAP ©).
- **TOC→breadcrumb mapping verified** (deterministic, cross-validates the font-size method) — needs to be wired into the chunker as the primary `section_path`/`parent_section_id` source (see §6).

---

## 5. v1 scope (the cut line)

**Build for v1:**
1. Ingestion: parse (fitz prose + Docling tables) → chunk → **tag section_path/parent_section_id from font-size headings (PDF) / HTML headings** (embedded-TOC cross-check is a v2 enhancement) → metadata.
2. Embed via OpenAI provider adapter → store in HANA (`REAL_VECTOR(1536)`).
3. Retrieval: HANA vector similarity + HANA full-text/BM25 if quick; fuse with RRF. Calibrate the refusal threshold on gold/adversarial eval, not by trusting a fixed default.
4. Generation: OpenAI provider adapter, **answer + citations**, strict "not in KB" path.
5. **Eval harness:** 30–50 gold Q&A, deterministic rule checks (retrieval hit-rate, citation validity, exact negative refusal), plus LLM-as-judge faithfulness/relevance.
6. Run locally with FastAPI (`POST /ask`); Streamlit is optional.

**Defer to v2:** SAP Gen AI Hub provider, HANA cross-encoder rerank, generated section summaries / KnowledgeBlocks, cross-reference graph, multimodal/image captioning, Cloud Foundry deploy, XSUAA auth. Local cross-encoder rerank is optional in v1 only if retrieval quality needs it.

---

## 6. Open items / do FIRST (blockers)

1. **HANA access:** use the existing BTP Trial account where SAP HANA Cloud / schemas / containers are visible. Create a running HANA instance and confirm Database Explorer SQL works.
2. **Model access:** v1 uses OpenAI API. Keep SAP AI Core / Gen AI Hub as a future provider path; do not block v1 on trial entitlement.
3. **Cost guardrails:** stop HANA when idle; set OpenAI budget/usage limits; never commit API keys or SAP passwords.
4. **Provider calibration:** use a stronger/different judge model than the answer model; tune `SIM_THRESHOLD` against positive and adversarial gold items.
5. **Pin SDK versions:** `openai`, `fastapi`, `uvicorn`, `hana-ml`/`hdbcli`, `langchain-hana`, `docling`.
6. **Wire TOC tagging into the chunker** (currently a separate validated snippet; make it the primary `section_path`/`parent_section_id` source with font-size fallback).

---

## 7. Secrets & hygiene
- **Never commit** service keys, HANA creds, OpenAI API keys, SAP trial passwords, or the SAP BTP user id. Use `.env` (gitignored); see `.env.example`.
- **`docs/` and `corpus/raw/` are gitignored** — SAP docs are copyrighted; `fetch_docs.sh` and `corpus/fetch.sh` reproduce them locally.
- Don't commit the raw corpus or full-text dumps; code + short samples only.

---

## 8. Repo layout
```
BTP_RAG/
├── HANDOFF.md            ← this file
├── README.md            ← quickstart
├── requirements.txt
├── .env.example         ← provider + HANA creds + model names (fill, don't commit .env)
├── .gitignore
├── chunking/            ← DONE: parsing + chunking prototype (validated)
│   ├── chunk_demo.py        (fitz+pdfplumber baseline)
│   ├── chunk_docling.py     (Docling table fix)
│   ├── fetch_docs.sh        (pull seed corpus → docs/, gitignored)
│   ├── chunks_preview.md
│   └── docs/                (gitignored — SAP © PDFs)
├── corpus/              ← focused raw-corpus fetch + manifest (raw files gitignored)
├── ingest/              ← TODO: full ingest (parse→chunk→tag→embed→HANA)
├── retrieve/            ← TODO: hybrid retrieval (+rerank v2)
├── eval/                ← TODO: gold Q&A + LLM-as-judge harness
└── app/                 ← TODO: FastAPI query API (+ optional Streamlit UI)
```

---

## 9. Reference facts (verified 2026-06)
- **OpenAI v1 provider:** `text-embedding-3-small` default dimension 1536; chat/judge model configurable in `.env`.
- **Gen AI Hub target provider:** Claude/GPT/Gemini/Mistral/Granite and embedding models via SAP AI Core when entitlement is available.
- **HANA Cloud:** native vector (`REAL_VECTOR`, `COSINE_SIMILARITY`); BM25/full-text; **cross-encoder reranker** via NLP service + `CROSS_ENCODE` SQL (Mar 2026); `langchain-hana` has vector + BM25 + hybrid (RRF) retrievers; `SAP/generative-ai-toolkit-for-sap-hana-cloud` bundles embeddings+vector+cross-encoder.
- **Cloud Foundry** = BTP's PaaS runtime (≈ Heroku/Elastic Beanstalk): `cf push` (deploy), `cf bind-service` (inject creds via `VCAP_SERVICES`), `cf logs`.
- SAP cockpit ≈ AWS console: entitle → create service instance → service key (creds) → consume from code; role collections ≈ IAM; Cloud Foundry/Kyma ≈ Beanstalk/EKS; HANA/AI Core ≈ RDS/SageMaker.

Docs: help.sap.com (SAP AI Core, Gen AI Hub, HANA Cloud Vector), architecture.learning.sap.com (Generative AI on BTP reference arch), github.com/SAP/generative-ai-toolkit-for-sap-hana-cloud.

---

## 10. Suggested first agent tasks
1. Stand up HANA Cloud in BTP Trial and confirm SQL access.
2. Wire TOC tagging into a unified `ingest/` module (fitz prose + Docling tables + TOC `section_path`/`parent_section_id` + metadata schema from §3).
3. Build `ingest/out/chunks.jsonl` and `eval/gold.jsonl` locally.
4. Create HANA schema (chunk table: text, metadata cols, `REAL_VECTOR(1536)`, full-text index).
5. Embed + load through the OpenAI provider adapter; implement HANA vector retrieval; add BM25 hybrid if quick.
6. Implement FastAPI `POST /ask` with citations + strict "Not in knowledge base." path.
7. Build rule-based eval checks + LLM-as-judge harness; record demo; write case study.
