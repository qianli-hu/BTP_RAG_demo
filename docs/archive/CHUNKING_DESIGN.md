> **ARCHIVED design history** — superseded by [docs/DESIGN.md](../DESIGN.md). Kept for decision provenance.

# Ingest & Chunking — Design for Review

**Status:** Draft for review (pre-implementation) · **Owner:** Qianli Hu · **Date:** 2026-06-09
**Scope:** the `ingest/` phase — how raw corpus files become embeddable, retrievable,
citable chunks. Covers the chunk schema, chunking algorithm, and the gold-eval set.
**Out of scope:** embedding API calls, HANA loading/retrieval SQL (separate docs).
The current v1 model provider is OpenAI; SAP Generative AI Hub is a future provider
adapter once AI Core entitlement is available.

Companions: [`../CORPUS.md`](CORPUS.md) (what the corpus is), [`../HANDOFF.md`](HANDOFF.md) (§3 design rationale), [`../corpus/MANIFEST.json`](../../corpus/MANIFEST.json) (measured sizes).

---

## How to review this

Please challenge, specifically:
1. **Chunking strategy** (§D2) — is structure-aware + sentence-packing right for this
   corpus, or are we leaving recall on the table by skipping semantic chunking?
2. **The chunk schema** (§D6) — does it carry everything needed for citation, hybrid
   retrieval, and small-to-big assembly, with nothing redundant?
3. **The embed-text prefix** (§D5) — does prefixing the vector input (but not the BM25
   text) help or hurt? Any failure mode?
4. **Params** (§D8) — token target/overlap sane for SQL-reference + UI-procedure content?
5. **Open questions** (§9) — pick any and weigh in.

This is a v1 portfolio build with a hard deadline (interview ~2026-06-11); bias feedback
toward "ships and is correct" over "maximally sophisticated." v2 ideas → §8.

---

## 1. Context

The corpus is **secured and measured** (`corpus/raw/`, gitignored; integrity in
`MANIFEST.json`): 3 documents, **615 pages / ~224k tokens / ~1,500 est. chunks**.

| doc | format | pages | ~tokens |
|---|---|--:|--:|
| `sap-ai-core` | PDF | 190 | 74k |
| `sap-hana-vector` | HTML ×51 | 51 | 25k |
| `sap-ai-launchpad` | PDF | 374 | 124k |

The chunker's job: turn these into `ingest/out/chunks.jsonl` — one portable record per
chunk — plus a `chunks_report.md`. That JSONL is the **only** handoff to the next phase.

## 2. The decoupling principle (why we build this now, with no DB)

Chunking depends on **nothing external**. The pipeline is layered:

```
chunk          pure local Python            ← THIS DOC. No entitlements, no DB.
  → embed      provider adapter            ← v1 OpenAI; v2 SAP Gen AI Hub
  → load+index HANA (REAL_VECTOR + FULLTEXT)← consumes embedded rows
  → retrieve   HANA vector + BM25 + RRF
```

`chunks.jsonl` is the clean seam: produced offline today, embedded and loaded when
access lands. **The BM25 inverted index, the vector ANN index, and RRF fusion are all
built and owned by HANA** over the columns we load — they are *not* stored in the chunk.
Therefore chunking is not blocked by database provisioning.

---

## Design decisions

### D1 — Chunking is database-independent
The chunk record is storage-agnostic JSON. HANA specifics (column types, indexes) are
applied at load time, not baked into chunks. **Rationale:** parallelizes with access
setup; keeps the corpus portable (re-point at SEC 10-Ks etc. per the corpus-agnostic goal).

### D2 — Structure-aware chunking, NOT semantic chunking
Boundaries come from document **structure** (TOC headings / HTML `h1`–`h4`), not from
embedding-similarity valleys.
**Rationale:** this corpus is a clean heading tree; SAP reference pages
(`COSINE_SIMILARITY`, `CREATE VECTOR INDEX`, …) are already atomic semantic units. Pure
semantic chunking is for *unstructured* prose; here it would add cost (needs embeddings
mid-ingest — chicken/egg), non-determinism, and little recall. **Trade-off accepted:**
a few very long borderless-table sections may chunk coarsely; acceptable for v1.
*(Semantic re-chunking of outlier sections → §8 v2.)*

### D3 — Chunks store RAW TEXT, never a summary or an id-only stub
Each chunk persists verbatim source text. Three consumers require it:
- **BM25** tokenizes the literal words — a summary changes tokens, kills sparse recall.
- **The generator LLM** needs real text for **grounded, citable** answers (can't cite a summary).
- **Re-embedding** on a model upgrade needs the original text.

There is no "store the embedding id instead of text" — the embedding is a *column on the
same row*, derived from the text. **v1 produces no summaries** (ingest is deliberately
no-LLM: deterministic, free, reproducible). Even in v2, a summary/context would *augment*
the embedding input, never *replace* stored text. See the three planes:

| plane | content | where it lives |
|---|---|---|
| stored text | verbatim chunk | `text` col — BM25 indexes this, LLM cites this |
| embedded text | breadcrumb prefix + chunk | `embedding` col — vector of `embed_text` |
| assembled context | chunk + parent/neighbors by ID | built at query time, **not stored** |

### D4 — Sparse/hybrid retrieval is HANA's job; the chunk only carries text
No per-chunk inverted index, no precomputed term weights. HANA's `FULLTEXT INDEX` on
`text` provides BM25 (`SCORE()`/`CONTAINS`); `REAL_VECTOR` + `COSINE_SIMILARITY` provides
dense; fusion via **RRF** (ranks, not scores) in `langchain-hana`'s hybrid retriever.
**Rationale:** keep one source of truth for tokens (the text); let the DB do what it's good at.

### D5 — Deterministic breadcrumb prefix on the vector input only
`embed_text = "<doc title> › <section_path>\n\n<text>"`; `text` stays verbatim.
The **vector** is computed from `embed_text`; **BM25 indexes only `text`**.
**Rationale:** a bare reference chunk (`"Syntax: COSINE_SIMILARITY(x,y)…"`) lacks context
and embeds poorly; the breadcrumb is a cheap, no-LLM "contextual retrieval" that situates
it. We keep it off the BM25 text so the doc title doesn't inflate every chunk's term
stats. Prefix is one line — we do **not** stuff overview/neighbor text into the vector
(dilutes discrimination — HANDOFF §3). **Open:** persist `embed_text` or recompute? (§9.3)

### D6 — Chunk record schema
One JSON object per line in `chunks.jsonl`. Maps 1:1 to `BTP_RAG.CHUNKS` (CORPUS.md §3).

```jsonc
{
  "id":            "sap-hana-vector#vector-fn-ref/cosine-similarity#0", // doc#section#ordinal
  "doc":           "sap-hana-vector",
  "element_type":  "prose",                  // prose | table | heading | code
  "section_path":  "Vector Function Reference › COSINE_SIMILARITY Function (Vector)",
  "section_id":    "a1b2c3d4",               // sha1(doc + section_path)[:12]
  "parent_section_id": "9f8e7d6c5b4a",       // a SECTION_ID (nearest ancestor section w/ chunks), not a chunk id
  "prev_id":       null,                      // neighbor CHUNK ids (reading order)
  "next_id":       "sap-hana-vector#a1b2c3d4#01",
  "page":          null,                      // PDF page int; null for HTML
  "topic_slug":    "cosine-similarity-function-vector", // HTML topic slug; null for PDF
  "source_url":    "https://help.sap.com/docs/hana-cloud-database/.../cosine_similarity-function-vector",
  "doc_version":   "2026_1_QRC",
  "token_count":   188,
  "text":          "COSINE_SIMILARITY( <x>, <y> ) Computes ...",        // VERBATIM
  "embed_text":    "SAP HANA Cloud Vector Engine › Vector Function Reference › COSINE_SIMILARITY ...\n\nCOSINE_SIMILARITY( <x>, <y> ) ...",
  "content_sha256":"84afd140f5e772a8..."      // hash of `text` — dedup + integrity
}
```
`embedding REAL_VECTOR(1536)` is added in the embed phase for the v1 OpenAI
`text-embedding-3-small` provider (not present at chunk time). Embeddings are
precomputed externally and inserted into HANA; v1 does not call HANA `VECTOR_EMBEDDING()`.
If the provider/model changes, the corpus must be fully re-embedded even when the
dimension is unchanged. If the dimension changes, recreate/reload the HANA vector column
or table.

### D7 — Identifiers: positional chunk id, section id, content hash (three distinct spaces)
- `id` = **positional chunk id** (`doc#section_id#ordinal`) — stable, unique even when
  overlap repeats text, the target of `prev_id`/`next_id`, and the HANA primary key.
- `section_id` = `sha1(doc::section_path)[:12]` — identifies the *section* (tree node).
- `parent_section_id` = a **section_id** (the nearest ancestor section that actually has
  chunks; we walk up past heading-only containers). Parent expansion =
  `WHERE section_id = parent_section_id`. **Two id spaces on purpose:** a section holds
  several chunks, so pointing the parent at one arbitrary chunk would be wrong; `prev/next`
  are chunk ids, `parent_section_id` is a section id.
- `content_sha256` = hash of `text` — for **dedup** (overlap recurs) and integrity.

### D8 — Chunking parameters (tunable)
| param | value | note |
|---|---|---|
| target size | **~300 tok** | prototype ran 220 → avg 146 (many short sections); nudge up to reduce fragments |
| hard max | **512 tok** | split sentence-wise above this |
| min size | **25 tok** | drop/merge below (boilerplate, stray lines) |
| overlap | **1 sentence**, intra-section only | cross-section context via `parent/prev/next`, not overlap |
| pack | merge tiny adjacent subsections up to target | avoids 15-tok fragments |

### D9 — Element types; tables kept whole
`element_type ∈ {prose, table, heading, code}`. **Tables** (borderless → Docling →
Markdown) are emitted as a **single** `table` chunk, never split mid-row; if huge, it
stays one chunk (over max is acceptable for tables). `code`/SQL blocks (common in doc 2)
kept intact. **Rationale:** splitting a table or a `CREATE VECTOR INDEX` example destroys
its meaning.

### D10 — Per-format parsing
- **PDF (docs 1, 3):** fitz for prose (reading order, clean text layer) + Docling for
  borderless tables → Markdown (separate `chunk_tables.py` pass, merged through the same
  `finalize()`). `section_path` from **font-size heading detection** (implemented +
  validated primary); strip header/footer boilerplate (>25% of pages) and TOC dot-leader /
  front-matter (`Content`, bare outline numbers). *(Embedded-TOC `doc.get_toc()` cross-check
  is a future enhancement, not the v1 primary.)*
- **HTML (doc 2):** parse saved `body` fragments under `corpus/raw/sap-hana-vector/` with
  bs4; `section_path` from page title + `h1`–`h4`; native `<table>` → Markdown;
  `source_url`/`page=null` from `_pages.jsonl`. Pages are small (~2k chars) → 1–3 chunks each.

---

## 5. Algorithm (per document)

```
for each doc:
  parse → ordered elements [(type, text, heading_ctx, page)]      # fitz/Docling or HTML
  strip boilerplate (PDF)                                          # repeats >25% pages
  build heading tree → section_path + parent linkage (from TOC/h-tags)
  for each section (heading-bounded):
    if table/code: emit one chunk (whole)
    else: pack sentences → chunks (target 300, max 512, 1-sentence overlap)
    merge any sub-25-tok remainder into a neighbor
  assign ids (doc#section#ordinal), wire prev/next and parent_section_id
  build embed_text = breadcrumb(section_path) + "\n\n" + text
  compute token_count, content_sha256
emit chunks.jsonl  +  chunks_report.md  +  refresh MANIFEST chunk counts (measured)
```

## 6. Output artifacts
- `ingest/out/chunks.jsonl` — the chunks (gitignored: derived © text).
- `ingest/out/chunks_report.md` — per-doc counts, token histogram, 3 sample chunks/doc,
  table/boilerplate stats. **Committed** (no full © text, just samples + stats).
- Update `corpus/MANIFEST.json` `est_chunks` → **measured** `chunks` per doc.

**Acceptance:** every chunk has non-empty `text`, a resolvable `source_url`, and a
`section_path`; `prev_id`/`next_id` chunk ids resolve; `parent_section_id` is either null
or resolves to at least one chunk with that `section_id`; no chunk > 512 tok except
`table`; report shows a sane token distribution; doc-2 chunk count replaces the ~173
estimate.

---

## 7. Gold eval set (parallel track, also DB-independent)

`eval/gold.jsonl` — **30–50** items, hand-authored against real pages (LLM-*assisted*
drafting allowed, but **human-verified** — a gold set must not be unverified-LLM output):

```jsonc
{ "id":"q007", "question":"Which distance functions does the HANA vector engine support?",
  "answer_type":"factual",                  // factual | table | cross-doc | negative
  "expected":"COSINE_SIMILARITY and L2DISTANCE.",
  "gold_doc":"sap-hana-vector",
  "gold_source_url":"https://help.sap.com/docs/.../vector-function-reference",
  "gold_section_path":"Vector Function Reference" }
```

Mix: factual lookups, table reads (AI Core metering/pricing), **cross-doc** (AI Core ↔
HANA Vector), and **negatives** ("not in KB" — must trigger the refusal path).
Enables two independent scores: **retrieval hit-rate** (is the gold section in top-k?) —
runnable the moment retrieval exists — and **LLM-as-judge** faithfulness/relevance once
generation exists. `eval/score_retrieval.py` stub ships now.

## 8. Non-goals / deferred to v2
Ingest-local deferrals: semantic re-chunking of outlier sections · contextual-retrieval
summaries (LLM prefix) · cross-reference graph · image/figure captioning · multilingual.
None block v1.

Cross-cutting v2 concepts (eval, citation verification, regression set, reranker,
drift observability) — with their cheap **v1-lite** forms and **v1 pull-forward verdicts** —
are written up in [`../V2_DESIGN.md`](../FUTURE.md). Notably, rule-based eval gates,
a citation existence-check, and a basic local cross-encoder reranker are flagged there as
high-ROI **v1 candidates**, not pure v2.

## 9. Open questions for the reviewer
1. **Target size 300 vs 220** — bigger chunks = more context/hit but coarser retrieval &
   more dilution. For SQL-reference pages, which wins?
2. **Launchpad (374pp, UI-heavy)** — ingest whole and let `min 25 tok` drop screenshot/
   one-line pages, or pre-filter to its Gen-AI-Hub chapters? (Affects ~849 of ~1,500 chunks.)
3. **Persist `embed_text`** in the row (debug/reproducibility, +storage) or recompute at
   embed time?
4. **Overlap unit** — 1 sentence vs fixed 40-tok window? Sentences respect meaning but
   vary in size.
5. **Heading-only sections** — emit a `heading` stub chunk (helps "where is X documented")
   or skip (avoid near-empty vectors)?
6. **`section_id` collisions** — two different docs can share a `section_path` (e.g.
   "Overview"); `sha1(doc+section_path)` namespaces by doc — sufficient?

## 10. Risks
- **TOC↔body mapping drift** (chunk tagged to wrong heading) — mitigise: nearest preceding
  heading by (page, y); cross-check vs font-size method (HANDOFF §4 says verified).
- **Docling table quality** on the nastiest pages — VLM fallback exists but is v2.
- **Token estimate ≠ tokenizer truth** — `len/4` heuristic for budgeting; fine for chunk
  sizing, but final embed token cost uses the real tokenizer.
