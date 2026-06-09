# BTP_RAG — Handoff

**Status:** design locked, chunking prototyped. Ready for v1 build.
**Owner:** Qianli Hu (PhD math; strong ML/RAG background — multi-agent systems, LoRA fine-tuning, eval harnesses, prior hybrid retrieval with bge-m3 + BM25).
**Last updated:** 2026-06-09.

---

## 1. What we're building & why

A **RAG knowledge assistant** that answers questions grounded in **SAP BTP documentation**, running entirely on **SAP BTP services** (SAP AI Core + Generative AI Hub + HANA Cloud), with **citations** and an **eval harness**.

**Why it exists (the goal that constrains decisions):**
- It's a **portfolio artifact** for an SAP interview — *AI Customer Adoption Expert Advisor* (req 449530), interview ~2026-06-11. The pitch line: *"I built and deployed RAG on SAP AI Core + Generative AI Hub + HANA Cloud Vector — grounded answers with citations and an LLM-as-judge eval harness. The same pipeline re-points at any customer corpus."*
- Secondary: it must **demonstrate hands-on BTP/AI** and be **corpus-agnostic** (swap SAP docs → SEC 10-Ks for finance roles, etc.).
- Deliverables for the portfolio: **GitHub repo + a 3–5 min demo video + a one-page case study.**

This is real production-minded work, not a toy: grounding, citations, eval gates, and a "no answer found" path are required, not optional.

---

## 2. Target architecture

```
corpus (SAP Help docs; HTML-first, PDF fallback)
  → parse:  fitz (prose)  +  Docling (borderless tables → Markdown)
  → chunk:  structure-aware, sentence-overlap, boilerplate-stripped
  → tag:    section_path + parent_id from the EMBEDDED PDF TOC (no LLM)
  → embed:  text-embedding-3-large via Generative AI Hub
  → store:  HANA Cloud  (REAL_VECTOR column + full-text/BM25 index)
  → retrieve: HYBRID  (vector + BM25, fused via Reciprocal Rank Fusion)
            → rerank: HANA cross-encoder (CROSS_ENCODE)   [v2]
  → generate: Claude (Sonnet/Opus 4.6) via Generative AI Hub
            → answer + citations  (+ "not in knowledge base" path)
  → eval:   LLM-as-judge faithfulness/relevance over a gold Q&A set
  → serve:  Streamlit/FastAPI  →  local (v1)  →  Cloud Foundry (v2)
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
- **`section_path` + `parent_id` come from the embedded PDF TOC** (`doc.get_toc()` → heading tree with level+page; map each chunk to the nearest preceding heading by (page, y); the level-above heading is its parent). **Fallbacks:** font-size heading detection (no outline), or HTML `h1`–`h4` tags (HTML source). All deterministic — no model.
- "Embedded TOC" = the bookmark/outline tree stored *inside* the PDF; unrelated to vector embeddings. Tagging happens **before** any embedding.

**Metadata — three planes (don't conflate):**
1. **Embedded text** (retrieval key): the chunk + a *short* situating prefix only. Do NOT stuff the full overview/neighbors here — it dilutes the vector and kills discrimination.
2. **Stored metadata** (filter/cite/navigate): `doc, section_path, section_id, parent_id, prev_id, next_id, page, version, source_url, hash, element_type`.
3. **Assembled context** (what the LLM sees): retrieved chunk **+ neighbors/parent** pulled via IDs at query time (small-to-big). Store neighbors as **IDs, not duplicated text**.

**Embeddings.** `text-embedding-3-large` via Generative AI Hub (keeps it all-on-SAP). Match `REAL_VECTOR(n)` dim to the model.

**Retrieval.** Hybrid = HANA **vector** (`COSINE_SIMILARITY`) + HANA **BM25/full-text**, fused via **RRF** (ranks, not scores). SAP's `langchain-hana` exposes `HANABm25Retriever` / `HANAHybridRetriever`. **Rerank** with HANA's **cross-encoder** (`CROSS_ENCODE`, NLP service) on top-N — entire retrieve→fuse→rerank runs **inside HANA** (data never leaves the DB; strong talking point).

**LLM.** Claude (Sonnet/Opus 4.6) via Generative AI Hub; GPT-5.x/Gemini also available. Route cheap models for table-summaries/captions, Claude for the final answer. Optionally wrap calls in the **Orchestration Service** (templating + grounding + content filtering).

**Hosting.** v1 local (Streamlit hitting cloud services) → v2 Cloud Foundry (`cf push`, bind HANA + AI Core, public route) → optional Kyma + XSUAA auth.

**Rule-book / cross-reference handling** (relevant if corpus is rule-like): atomic clause-level chunks; capture clause numbering + cross-refs (`"see 7.3"`) via regex → a cross-reference **graph** (HANA Knowledge Graph) for hybrid vector+graph retrieval; carry `version`/`effective_date` so retrieval filters to the rule in force.

---

## 4. Current state (what's done)

In `chunking/` (prototyped & validated on the SAP AI Core doc):
- `chunk_demo.py` — fitz+pdfplumber baseline: heading-aware sectioning, boilerplate strip (265 lines), sentence-overlap chunks (504 chunks/190pp). **Prose: production-ready. Tables: fails on borderless.**
- `chunk_docling.py` — Docling: same borderless tables → clean Markdown. **Fix confirmed.**
- `chunks_preview.md` — sample output.
- `fetch_docs.sh` — pulls the seed PDF into `docs/` (gitignored, SAP ©).
- **TOC→breadcrumb mapping verified** (deterministic, cross-validates the font-size method) — needs to be wired into the chunker as the primary `section_path`/`parent_id` source (see §6).

---

## 5. v1 scope (the cut line)

**Build for v1:**
1. Ingestion: parse (fitz prose + Docling tables) → chunk → **tag section_path/parent_id from embedded TOC** → metadata.
2. Embed via Gen AI Hub → store in HANA (`REAL_VECTOR`).
3. Retrieval: vector similarity (add BM25 hybrid if quick).
4. Generation: Claude via Gen AI Hub, **answer + citations**, "not in KB" path.
5. **Eval harness:** 30–50 gold Q&A, LLM-as-judge faithfulness/relevance, pass/fail table.
6. Run locally (Streamlit).

**Defer to v2:** cross-encoder rerank, contextual-retrieval prefix, cross-reference graph, multimodal/image captioning, Cloud Foundry deploy, XSUAA auth.

---

## 6. Open items / do FIRST (blockers)

1. **Account type — make-or-break.** Generative AI Hub generally is **NOT** on the free BTP Trial → likely need **Pay-As-You-Go (CPEA)** with a little credit. Resolve before coding. (Qianli has a BTP trial subaccount `…etrial` already; confirm Gen AI Hub availability or switch to PAYG.)
2. **Region:** provision AI Core + Gen AI Hub + HANA Cloud in the **same** supported region.
3. **Model enablement:** allowlist embedding + Claude models in Gen AI Hub; accept terms.
4. **Cost guardrails:** HANA Cloud bills hourly — **stop when idle**; set a budget; embedding a few thousand pages ≈ a few million tokens ≈ small $.
5. **Pin SDK versions:** `generative-ai-hub-sdk`, `hana-ml`/`hdbcli`, `langchain-hana`, `docling`.
6. **Wire TOC tagging into the chunker** (currently a separate validated snippet; make it the primary `section_path`/`parent_id` source with font-size fallback).

---

## 7. Secrets & hygiene
- **Never commit** service keys, HANA creds, or the SAP BTP user id. Use `.env` (gitignored); see `.env.example`.
- **`docs/` is gitignored** — SAP docs are copyrighted; `fetch_docs.sh` reproduces them locally.
- Don't commit the raw corpus or full-text dumps; code + short samples only.

---

## 8. Repo layout
```
BTP_RAG/
├── HANDOFF.md            ← this file
├── README.md            ← quickstart
├── requirements.txt
├── .env.example         ← AI Core + HANA creds + model names (fill, don't commit .env)
├── .gitignore
├── chunking/            ← DONE: parsing + chunking prototype (validated)
│   ├── chunk_demo.py        (fitz+pdfplumber baseline)
│   ├── chunk_docling.py     (Docling table fix)
│   ├── fetch_docs.sh        (pull seed corpus → docs/, gitignored)
│   ├── chunks_preview.md
│   └── docs/                (gitignored — SAP © PDFs)
├── ingest/              ← TODO: full ingest (parse→chunk→tag→embed→HANA)
├── retrieve/            ← TODO: hybrid retrieval (+rerank v2)
├── eval/                ← TODO: gold Q&A + LLM-as-judge harness
└── app/                 ← TODO: Streamlit/FastAPI query UI
```

---

## 9. Reference facts (verified 2026-06)
- **Gen AI Hub models:** Claude Sonnet/Opus 4.6, GPT-5.x, Gemini 3, Mistral, IBM Granite; embeddings `text-embedding-3-large/small`. ("codex" deprecated → use GPT-5.x.)
- **HANA Cloud:** native vector (`REAL_VECTOR`, `COSINE_SIMILARITY`); BM25/full-text; **cross-encoder reranker** via NLP service + `CROSS_ENCODE` SQL (Mar 2026); `langchain-hana` has vector + BM25 + hybrid (RRF) retrievers; `SAP/generative-ai-toolkit-for-sap-hana-cloud` bundles embeddings+vector+cross-encoder.
- **Cloud Foundry** = BTP's PaaS runtime (≈ Heroku/Elastic Beanstalk): `cf push` (deploy), `cf bind-service` (inject creds via `VCAP_SERVICES`), `cf logs`.
- SAP cockpit ≈ AWS console: entitle → create service instance → service key (creds) → consume from code; role collections ≈ IAM; Cloud Foundry/Kyma ≈ Beanstalk/EKS; HANA/AI Core ≈ RDS/SageMaker.

Docs: help.sap.com (SAP AI Core, Gen AI Hub, HANA Cloud Vector), architecture.learning.sap.com (Generative AI on BTP reference arch), github.com/SAP/generative-ai-toolkit-for-sap-hana-cloud.

---

## 10. Suggested first agent tasks
1. Resolve §6 access/account (human-in-loop — needs Qianli's BTP account).
2. Wire TOC tagging into a unified `ingest/` module (fitz prose + Docling tables + TOC `section_path`/`parent_id` + metadata schema from §3).
3. Stand up HANA schema (chunk table: text, vector, metadata cols; full-text index).
4. Embed + load; implement vector retrieval; add BM25 hybrid.
5. Generation w/ citations + "not in KB" path.
6. Build the gold Q&A eval set + LLM-as-judge harness.
7. Streamlit UI; record demo; write case study.
