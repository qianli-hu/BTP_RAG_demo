# BTP_RAG — Future Design (v1.5 / v2)

*What comes next, and why it isn't in v1. Companion to [DESIGN.md](DESIGN.md).*

## v1.5 — queued levers, gated on measured need
- **Cross-encoder reranker** (local `ms-marco-MiniLM`, config seam `RERANK_*` already in place):
  the diagnosed fix for the cross-doc ranking gap (gold chunks at rank 6–10). Maps to HANA's
  in-DB `CROSS_ENCODE` on a paid instance. A/B against the registry baseline before adopting.
- **Small-to-big context assembly** (`prev/next` + `parent_section_id` expansion): only if
  hit-rate is good but faithfulness drops — fixes narrow context, not wrong retrieval.
- **Query decomposition / rewrite** (LLM → structured sub-queries + filters): only for true
  recall failures, which v1 doesn't have; costs ~1–3 s/query.
- **Reliability & load track**: SSE streaming on /ask, async + connection pool, load test
  (p95/p99 under N users), chaos drill — kill HANA mid-request → fall back to SqliteStore live.

---

# v2 & Hardening — Design Notes (forward-looking)

Concepts we understand and will layer in as **v1-lite hardening** or after v1 ships.
This note exists to (a) record the design so the architecture stays open to them, and
(b) showcase the reasoning for the interview.

**Theme:** most of these have a cheap **v1-lite** form worth pulling forward, and a richer
**v2** form. Eval especially is the single most important piece — quality is meaningless
without a gate — and the rule-based half of it is trivial to build, so several "v1-lite"
items below are flagged as **v1 candidates**. See the verdict table at the end.

Context that constrains these: v1 embeds with OpenAI `text-embedding-3-small`
(`REAL_VECTOR(1536)`) behind a provider adapter; the canonical refusal string is exactly
`Not in knowledge base.`; retrieval is HANA hybrid (vector + BM25 + RRF). See
[`CORPUS.md`](archive/CORPUS.md), [`ingest/DESIGN.md`](archive/CHUNKING_DESIGN.md).

---

## 1. Two-layer eval: rule/code gates + LLM-as-judge

Eval gates every change (prompt, chunk params, model, reranker). Two complementary layers:

**Layer A — deterministic rule/code checks** (no LLM → free, fast, zero variance, CI-safe):
- **retrieval hit-rate@k** — is the gold `section_id`/`source_url` in the top-k? (set membership)
- **citation validity** — every cited `source_url` exists in the corpus *and* was in the
  retrieved context (see §2)
- **negative handling** — adversarial out-of-KB items must answer **exactly**
  `Not in knowledge base.`
- **structural** — answer non-empty, within a token cap, ≥1 citation when not a refusal
- **exact-fact match** — gold numeric facts (e.g. metering rates) appear verbatim
These catch the majority of regressions for almost no effort.

**Layer B — LLM-as-judge** (for what rules can't measure): **faithfulness** (is every claim
grounded in retrieved context — the anti-hallucination metric), **answer relevance**,
**completeness**, scored against a rubric. *Hardening (v2):* a **panel of ≥3 judges** with
majority/mean to cut judge variance; **calibrate** the judge against a small human-labeled
subset; score **per-claim** rather than whole-answer.

> **Verdict:** Layer A (all rule checks) + single-judge faithfulness/relevance → **pull to v1**.
> Judge panels, calibration, per-claim attribution → v2.

## 2. Citation verification

The pitch is "grounded answers **with citations**," so a fabricated or non-supporting
citation is the worst-case failure. Two levels:

- **Existence + retrieval check** (rule-based, ~free): every citation the LLM emits must
  resolve to a real chunk `id`/`source_url` in the corpus **and** be a member of the
  retrieved-context set. Deterministically kills invented sources and "cited but never
  retrieved" claims. → **v1 candidate**.
- **Support / entailment check** (model-based): does the cited chunk actually *entail* the
  sentence it's attached to? An NLI model or LLM judge at **citation granularity** (finer
  than whole-answer faithfulness). Flags unsupported-but-cited claims. → v2.

> **Verdict:** existence+retrieval check → **pull to v1**; entailment → v2.

## 3. Regression dataset

Distinct from the gold set. **Gold** measures *absolute* quality on a representative
sample; the **regression** set guards against *re-introducing fixed bugs* and silent drift.
Composition:
- every real failure we find + its fix, frozen as a case (query → expected answer/citation/behavior);
- a snapshot of currently-passing gold items with their **frozen top-k retrieval ids** and answers.

On each change we re-run and **diff**: a case dropping pass→fail **blocks** the change; a
change in frozen top-k ids **surfaces retrieval drift** for review. New cases come from the
production query log (§5). This is the CI gate that makes the pipeline safe to iterate on.

> **Verdict:** v2 (only meaningful once there's change-history) — but the harness is cheap
> and the **initial snapshot can be captured at v1** for free.

## 4. Reranker (cross-encoder, precision@top)

Hybrid retrieval (bi-encoder vector + BM25) optimizes **recall** and is approximate — the
#1 result isn't necessarily the most relevant. A **cross-encoder** jointly encodes
`(query, chunk)` in one forward pass → far better relevance ordering, but it costs O(k)
model calls, so it's only ever applied to the **top-N** candidates
(e.g. retrieve 50 → rerank → keep 5). That precision@top win is exactly what the generator
wants: fewer, better chunks in context = less dilution, fewer hallucinations.

- **v1-lite:** a local `sentence-transformers` cross-encoder (e.g. `ms-marco-MiniLM-L6-v2`)
  reranking the top-k in app code. No extra infra, CPU-fine, sub-second for k≤50.
- **v2:** HANA's in-DB **`CROSS_ENCODE`** (NLP service) so retrieve→fuse→rerank all run
  **inside HANA** during the reranking phase. This is a full-v2 SAP-native talking point;
  it does not apply to v1 OpenAI embedding/generation calls.

> **Verdict:** local cross-encoder on top-k is optional v1 insurance if HANA hybrid
> retrieval quality is weak; HANA `CROSS_ENCODE` → v2.

## 5. Continuous observability: retrieval & question drift

Log every request: `query → query embedding → retrieved ids+scores → answer → citations →
rule/judge scores → user feedback (👍/👎)`. This log powers both dashboards below **and**
feeds the regression set (§3) — closing the loop.

- **Retrieval-quality drift:** track top-k similarity scores, hit-rate on a fixed **canary**
  query set, **refusal rate** (`Not in knowledge base.` frequency), and latency over time.
  A drop after a corpus refresh or model swap signals degradation before users complain.
- **User-question drift (embedding-space):** monitor the distribution of incoming **query
  embeddings** against the **corpus embedding** distribution. Concretely: per query, measure
  distance to the nearest corpus chunks / to the corpus centroid; track the rolling
  distribution and flag when mean out-of-distribution distance trends up. Use **PSI** or
  **KL divergence** between successive windows of the query-embedding distribution. Rising
  drift means either a **corpus gap** (users asking what we don't cover → expand the corpus)
  or a topic shift in demand — the highest-signal early warning that the KB must grow.
  *(This is exactly the "does the user-question embedding drift from the corpus embedding
  over time?" question — yes, and it's directly measurable.)*

> **Verdict:** structured request logging → cheap **v1 stub** worth adding; the drift
> dashboards / PSI / KL monitoring → v2.

## 6. Blockify-inspired data-layer optimization: KnowledgeBlocks

Source pattern: the open-source
[`iternal-technologies-partners/blockify-agentic-data-optimization`](https://github.com/iternal-technologies-partners/blockify-agentic-data-optimization)
repo frames the RAG problem as a **data representation** problem, not only a retrieval
problem. Its docs describe converting raw content into structured "IdeaBlock" units with
fields such as a name, a critical question, a trusted answer, tags, entities, and
keywords, then distilling/deduplicating similar units before vector indexing.

We should **not** make Blockify a v1 dependency and should not accept public benchmark
claims without reproducing them on our own gold set. What we can borrow for v2 is the
data-layer shape:

**Borrow 1 — query-aligned `KnowledgeBlock` as a second retrieval representation.**
Keep v1 raw chunks as the source of truth for citations, but add a derived row/table:

```jsonc
{
  "block_id": "kb#sap-hana-vector#cosine-similarity#0",
  "source_chunk_ids": ["sap-hana-vector#vector-fn-ref/cosine-similarity#0"],
  "name": "COSINE_SIMILARITY function",
  "critical_question": "How does SAP HANA Cloud compute cosine similarity between vectors?",
  "trusted_answer": "COSINE_SIMILARITY computes similarity between two REAL_VECTOR values...",
  "tags": ["HANA Cloud", "Vector Engine", "SQL"],
  "entities": [{"name": "COSINE_SIMILARITY", "type": "SQL_FUNCTION"}],
  "keywords": ["COSINE_SIMILARITY", "REAL_VECTOR", "similarity search"],
  "doc_version": "2026_1_QRC",
  "source_url": "https://help.sap.com/docs/..."
}
```

Embedding input becomes `critical_question + trusted_answer + keywords`; answer
generation still cites the original `source_chunk_ids`. This lets retrieval match user
questions more directly without sacrificing provenance.

**Borrow 2 — distillation/dedup before loading the vector index.**
After initial chunking, cluster near-duplicate or overlapping blocks by embedding
similarity. For small corpora use exact pairwise/BFS; for larger corpora use an LSH or
ANN prefilter plus community clustering. Merge each cluster with an LLM only if it can
preserve every distinct fact and all source provenance. Store an ancestry map:

```text
KnowledgeBlock -> source_chunk_ids -> source_url/page/topic_slug/doc_version
```

This directly addresses duplicate SAP content across AI Core, AI Launchpad, and HANA
Vector docs while keeping citations auditable.

**Borrow 3 — metadata-aware retrieval and A/B evaluation.**
Use block metadata (`doc_version`, source authority, entity type, tags, effective date,
permissions if this becomes customer data) as filters or reranking features. Evaluate
raw chunks vs KnowledgeBlocks on our own metrics:

- retrieval hit-rate@k / MRR on `eval/gold.jsonl`
- exact negative refusal rate
- citation validity
- context token count per answer
- LLM-judge faithfulness/relevance

Only promote KnowledgeBlocks if they beat raw chunks on this repo's SAP gold set. The
benchmark is the product decision; the blog-style "260%" claim is not evidence for our
corpus until reproduced.

> **Verdict:** v2. The v1 chunk schema already preserves enough provenance to derive
> KnowledgeBlocks later. Do not delay v1 for LLM-generated data distillation.

## 7. Generated section summaries / parent summaries

v1 parent context is deliberately plain: `parent_section_id` expands to original source
chunks from the nearest parent section. The LLM may see those parent chunks during
query-time context assembly, but they are not summaries and are not embedded into the
child chunk.

For v2, add optional generated section summaries as a separate derived artifact:

```jsonc
{
  "summary_id": "summary#sap-ai-core#metering-pricing",
  "section_id": "metering-pricing",
  "source_chunk_ids": ["sap-ai-core#metering-pricing#00", "sap-ai-core#metering-pricing#01"],
  "summary_text": "This section explains how SAP AI Core meters generative AI usage...",
  "doc_version": "2026-06-04",
  "source_urls": ["https://help.sap.com/docs/sap-ai-core"]
}
```

Rules:
- summaries never replace raw chunks for citation
- every summary stores `source_chunk_ids`
- retrieval may use summaries for recall/context compression
- final citations still point to original chunk ids/source URLs
- promote only if eval shows better faithfulness or lower context-token cost

> **Verdict:** v2. This is useful once the raw-chunk RAG path works and we have eval
> results to compare against.

## 8. Scaling vector search: exact scan → HNSW index

v1 dense retrieval is an **exact brute-force scan**:
`ORDER BY COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) DESC`. At **1,216 vectors this is
sub-millisecond and 100% recall** — an ANN index would only add approximation error and
memory for zero speed benefit. So v1 uses **no vector index on purpose.**

As the corpus grows (re-pointed at a large customer corpus, or many more SAP docs), exact
scan is O(N) per query and eventually blows the latency budget. Then switch to an **HNSW
vector index** — confirmed available even on free-tier HANA (so this is a pure *performance*
choice, not an entitlement one):

```sql
CREATE HNSW VECTOR INDEX IDX_CHUNKS_EMB ON BTP_RAG.CHUNKS(EMBEDDING)
  SIMILARITY FUNCTION COSINE_SIMILARITY;          -- + BUILD/SEARCH CONFIGURATION for M / ef
```

**HNSW** (Hierarchical Navigable Small World) is a graph-based ANN index: a layered
"small-world" proximity graph (highways → arterials → local streets) navigated greedily,
giving ~**O(log N)** *approximate* search instead of O(N) *exact*. Knobs: **M** (graph
degree; HANA default 64), **ef_construction** (build quality), **ef_search** (per-query
recall↔latency). Trade-off: ~95–99% recall (tunable via `ef_search`) + extra graph memory,
in exchange for fast, scalable queries that handle millions–billions of vectors.

Decision rule: **stay on exact scan while p99 dense-search latency is within budget** (rough
crossover ~50k–100k vectors); add HNSW above that. The query SQL is unchanged — HANA uses
the index automatically (force it with the `VECTOR_INDEX` hint if needed). **Tune `ef_search`
against `eval/gold.jsonl` hit-rate** so the approximation never drops gold recall.

> **Verdict:** v1 = exact scan (no index, faster *and* exact at this size). HNSW when the
> corpus scales past the exact-scan latency budget. Free tier supports it, so it's drop-in.

## 9. Security & governance: authorization-aware retrieval + PII guard

Becomes mandatory the moment the corpus stops being public SAP docs (customer KB, tickets, contracts).

**Authorization-aware retrieval (RBAC).** Access control must be enforced **in retrieval**,
not in the prompt — an LLM told "don't reveal X" is not a security boundary; a chunk that
never reaches the context window is.
- **Chunking gains security metadata**: each chunk carries `acl_groups` / `classification` /
  `tenant`, inherited from source-system permissions at ingest — i.e. chunking gets dissected
  with more metadata, because the ACL decision is made per chunk, at ingest time.
- **Retrieval is guarded**: the authenticated user's roles (XSUAA/JWT on BTP) resolve to a
  mandatory filter merged into the existing `filters=` param (the v1 seam built for exactly
  this) — applied to BOTH dense (`WHERE` in HANA) and sparse (BM25 pre-filter), so an
  unauthorized chunk can never be retrieved, cited, or leaked via similarity search.
- Eval gains **leakage tests**: adversarial gold items asserting role A never surfaces
  role-B-only content (the permission analogue of the refusal gate).

**PII guard — three checkpoints, defense in depth:**
1. **Ingest-time** (strongest): detect PII (NER/regex, Presidio-style) during chunking;
   redact/pseudonymize *before* embedding — PII never enters vectors, the store, or a
   third-party embedding API; chunk metadata records `pii_redacted: true`.
2. **Query-time**: scrub PII from user queries before embedding/logging (request-log hygiene).
3. **Response-time**: output filter as a backstop (SAP Orchestration content filtering slots
   here on the Gen AI Hub path).

> **Verdict:** v2 — but the v1 architecture already carries the seams: `filters=` (becomes
> the ACL predicate), the per-chunk metadata schema (gains `acl/classification/pii` fields),
> and the adversarial-gold pattern (gains leakage tests).

---

## v1 pull-forward verdicts

| concept | v1-lite (recommended for v1) | full v2 |
|---|---|---|
| Rule/code eval (§1A) | hit-rate@k, citation-valid, exact-negative, structural | — |
| LLM-judge (§1B) | single-judge faithfulness/relevance | judge panel, calibration, per-claim |
| Citation verification (§2) | existence + in-retrieved-set check | NLI/entailment per citation |
| Regression dataset (§3) | capture initial passing snapshot | full diff-gated CI on change-history |
| Reranker (§4) | optional local cross-encoder on top-k if retrieval quality needs it | HANA in-DB `CROSS_ENCODE` |
| Observability (§5) | structured request logging | retrieval + question-drift (PSI/KL) dashboards |
| Blockify-style KnowledgeBlocks (§6) | preserve provenance in chunks so blocks can be derived later | query-aligned blocks + distillation/dedup + A/B gate |
| Section summaries (§7) | parent context expands to raw source chunks | generated summaries with `source_chunk_ids` provenance |
| Vector ANN index (§8) | exact brute-force cosine scan (1.2k vectors → faster + exact) | HNSW index once corpus scales past exact-scan latency budget |

**Recommendation:** make **rule-based eval gates (§1A)** and the **citation existence
check (§2)** mandatory v1 because they are cheap and directly support the demo claim.
Treat a **basic local reranker (§4)** as optional v1 insurance if hit-rate/precision is
weak after HANA hybrid retrieval. Defer regression CI, judge panels, HANA `CROSS_ENCODE`,
entailment, drift dashboards, Blockify-style KnowledgeBlocks, and generated section
summaries to v2.
