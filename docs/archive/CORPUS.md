> **ARCHIVED design history** — superseded by the [README](../../README.md). Kept for decision provenance.

# BTP_RAG — Corpus Definition (v1)

Defines **exactly** what goes into the knowledge base: which documents, where each
chunk comes from, where chunks live at rest, and how many. Companion to `HANDOFF.md` §3.

**Decided:** 2026-06-09. **Scope:** "Focused" — 3 documents, the exact SAP stack the
system is deployed on (self-referential demo). Big enough to justify RAG, small enough
to parse-validate, embed, and demo before the **2026-06-11** interview.
Cost is a non-issue (roughly cents to embed the whole corpus with `text-embedding-3-small`);
the real constraint is parsing-validation time per doc, so we cap at 3.

---

## 1. The documents

| # | doc id | Source (help.sap.com) | Version | Format | Pages | Chunks |
|---|--------|-----------------------|---------|--------|------:|-------:|
| 1 | `sap-ai-core` | [SAP AI Core](https://help.sap.com/doc/c31b38b32a5d4e07a4488cb0f8bb55d9/CLOUD/en-US/f17fa8568d0448c685f2a0301061a6ee.pdf) (incl. Generative AI Hub) | 2026-06-04 | PDF | **190** | **471** *(measured)* |
| 2 | `sap-hana-vector` | [SAP HANA Cloud — Vector Engine Guide](https://help.sap.com/docs/hana-cloud-database/sap-hana-cloud-sap-hana-database-vector-engine-guide/introduction) | 2026_1_QRC | **HTML ×51** | **51** | ~173 *(est)* |
| 3 | `sap-ai-launchpad` | [SAP AI Launchpad](https://help.sap.com/doc/5945759df2d34b69b681c53bb2dd7b9f/CLOUD/en-US/038a6194f65c4ef68885f6f16360dbc4.pdf) | 2026-06-04 | PDF | **374** | ~849 *(est)* |

> **Secured & measured 2026-06-09** — all three are on disk under `corpus/raw/`
> (gitignored), with bytes + sha256 + counts in [`corpus/MANIFEST.json`](../../corpus/MANIFEST.json).
> Reproduce with `./corpus/fetch.sh`.

**Why these three** — they *are* the pitch: AI Core (model serving + Gen AI Hub) →
HANA Vector (the vector store) → AI Launchpad (the cockpit/ops layer, which also serves
the secondary "hands-on SAP cockpit" goal). The assistant answers questions about the
very stack it runs on, citing the real SAP docs back.

**Note on doc 3:** the full AI Launchpad manual is 374pp and UI/screenshot-heavy. The
`≥25-token` content filter (below) auto-drops image-only / one-line procedure pages, so
we ingest it whole and let the filter cut the junk rather than hand-selecting chapters.
If a tighter corpus is wanted later, cap it to its Generative-AI-Hub chapters (~60–90pp).

---

## 2. Where each chunk comes *from* (provenance)

Every chunk is traceable to a specific SAP Help location so answers can cite it:

- `doc` — one of the three ids above
- `section_path` — breadcrumb from the **embedded PDF TOC** (PDF) or `h1`–`h4` (HTML),
  e.g. `What Is SAP AI Core? › 1.1.1 Metering and Pricing for Generative AI`
- `page` — source page for PDFs; `null` for HTML
- `topic_slug` — HTML page slug when the source is an HTML topic; `null` for PDFs
- `source_url` — deep link back to help.sap.com

Tagging is deterministic (no LLM) — see `HANDOFF.md` §3.

## 3. Where chunks *live* at rest (storage)

Two hops: **local intermediate → HANA Cloud.**

```
ingest/out/chunks.jsonl          # local, gitignored: text + metadata + hash
        └─ embed + load ─▶  BTP_RAG.CHUNKS   # HANA Cloud table:
```

```sql
CREATE COLUMN TABLE BTP_RAG.CHUNKS (
  id           NVARCHAR(256) PRIMARY KEY, -- positional: doc#section#ordinal
  doc          NVARCHAR(32),              -- sap-ai-core | sap-hana-vector | sap-ai-launchpad
  section_path NVARCHAR(512),
  section_id   NVARCHAR(32),
  parent_section_id NVARCHAR(32),         -- nearest ancestor SECTION w/ chunks; expand via WHERE section_id = parent_section_id
  prev_id      NVARCHAR(256),
  next_id      NVARCHAR(256),             -- neighbor CHUNK ids (reading order); never duplicated text
  page         INTEGER,
  topic_slug   NVARCHAR(256),
  source_url   NVARCHAR(512),
  element_type NVARCHAR(16),              -- prose | table | heading
  doc_version  NVARCHAR(32),              -- 2026-06-04 or 2026_1_QRC
  content_sha256 NVARCHAR(64),
  text         NCLOB,                     -- cited text; BM25 indexes this
  embed_text   NCLOB,                     -- deterministic breadcrumb prefix + text
  embedding    REAL_VECTOR(1536)          -- v1: OpenAI text-embedding-3-small
);
-- BM25 / full-text for the hybrid retriever:
CREATE FULLTEXT INDEX FTI_CHUNKS ON BTP_RAG.CHUNKS(text);
```

`parent_section_id` is intentionally a section reference, not a chunk reference. During
context assembly the retriever can fetch parent context with
`WHERE section_id = :parent_section_id`. The returned parent context is original SAP
source text in v1, not a generated summary.

`REAL_VECTOR(1536)` = native dim of the v1 OpenAI `text-embedding-3-small` provider.
Embeddings are precomputed by the configured provider and inserted into HANA; v1 does
not call HANA's in-database `VECTOR_EMBEDDING()` function. If the embedding provider or
model changes, fully re-embed the corpus and all future queries because vectors from
different embedding models cannot be mixed, even at the same dimension. If the dimension
changes, recreate/reload the vector column/table with the matching dimension. HANA
`REAL_VECTOR` supports dims 1–65000.

## 4. Sizing summary

| | Pages | ~Tokens | Chunks (measured) |
|---|------:|--------:|-------:|
| sap-ai-core | 190 | 74k | 362 |
| sap-hana-vector | 51 | 25k | 192 (incl. 23 HTML tables) |
| sap-ai-launchpad | 374 | 124k | 560 |
| **Total** | **615** | **224k** | **1,114** |

Counts are **measured** by `ingest/chunk.py` (target 300 tok). Borderless **PDF tables**
(Docling pass, `ingest/chunk_tables.py`) are added on top once that pass completes — they
flow through the same `finalize()` so this table + `MANIFEST.json` update together.

Embedding cost for ~224k tokens is negligible for v1; the practical bottleneck is
parser/chunker validation, not embedding spend.

## 5. Chunking parameters

- target **~300 tokens/chunk**, hard max **512 tokens** except tables/code, **1-sentence overlap**, chunk on heading boundaries
- **content filter:** keep chunks `≥25 tokens` (drops image-only / boilerplate pages)
- merge tiny adjacent subsections up to target when they belong to the same parent section
- strip repeating header/footer boilerplate (>25% of pages) + TOC dot-leader / front-matter
- prose via **fitz**, borderless PDF tables via **Docling** → Markdown, HTML tables via bs4
- `section_path` from **font-size heading detection** (validated) for PDFs and `h1`–`h4` for
  HTML; `parent_section_id` = nearest ancestor section that has chunks. *(Embedded-TOC
  cross-check is a future enhancement; font-size is the implemented primary.)*

## 6. Status + next step

- [x] **Doc 1 secured + measured** — `sap-ai-core.pdf`, 190pp → 471 chunks (prototype).
- [x] **Doc 2 secured + crawled** — Vector Engine Guide is **HTML-only**; 51 content
  pages pulled via `corpus/crawl_hana_vector.py` (SAP Help JSON API), ~25k tok.
- [x] **Doc 3 secured** — `sap-ai-launchpad.pdf`, 374pp, ~124k tok.
- [x] **Integrity recorded** — bytes + sha256 + counts in `corpus/MANIFEST.json`.
- [ ] **Firm the chunk total** — run the unified `ingest/` chunker over all three
  (prose via fitz, tables via Docling, HTML via `h1`–`h4`) to replace the doc-2/doc-3
  estimates with measured counts. This is the next build step.
