# retrieve/ — hybrid retrieval (vector + BM25, RRF)

Hybrid retrieval = HANA vector (`COSINE_SIMILARITY`) + BM25, fused via RRF.

- **Sparse/BM25 runs in the APP layer** (`bm25_search.py`, `rank_bm25`) over the chunk
  `TEXT` pulled from HANA. **Why:** HANA Cloud **free tier (`hana-free`) does not support
  `CREATE FULLTEXT INDEX` or PAL BM25** — verified empirically — so in-DB `CONTAINS()`/
  `SCORE()` is unavailable. On a **paid** instance this moves in-DB (full-text `SCORE()`
  or PAL `PAL_SEARCH_DOCS_BY_KEYWORDS`) with no other code change.
- **Dense** stays in HANA (`COSINE_SIMILARITY` over `EMBEDDING`, after the embed step).
- Chunks do not store BM25 data; the BM25 index is built in-process from the `TEXT` column.

v1 query embedding comes from the same embedding provider used at ingest time
(default OpenAI `text-embedding-3-small`, dimension 1536).

Small-to-big context assembly:
- retrieve precise top-k chunks first
- optionally fetch `prev_id` / `next_id` chunk rows for local continuity
- optionally expand `parent_section_id` with `WHERE section_id = :parent_section_id`

The LLM sees parent context only after this query-time assembly step. In v1, parent
context is original source text from parent-section chunks, not a generated summary.

v2: HANA cross-encoder rerank (`CROSS_ENCODE`) on top-N; optional generated section
summaries / KnowledgeBlocks with `source_chunk_ids` provenance.
