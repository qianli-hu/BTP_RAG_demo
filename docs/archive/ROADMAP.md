> **ARCHIVED design history** — superseded by [docs/DESIGN.md](../DESIGN.md). Kept for decision provenance.

# BTP_RAG — Roadmap & Serving-Design Decisions

Consolidates the **v1 / v1.5 / v2 scope** and the **serving-phase design decisions** worked
out in design discussion. Companions: [`HANDOFF.md`](HANDOFF.md) (context/architecture),
[`CORPUS.md`](CORPUS.md) (corpus), [`ingest/DESIGN.md`](CHUNKING_DESIGN.md) (chunking),
[`V2_DESIGN.md`](../FUTURE.md) (v2 deep-dives).

---

## 1. Scope: v1 / v1.5 / v2

### v1 — definition of done
**Core path** (eval-what-you-serve: online + eval share one engine):
```
query → retrieve(HANA COSINE_SIMILARITY dense ⊕ app BM25 sparse → RRF)
      → generate(gpt-5-mini: grounded answer + chunk-id citations | "Not in knowledge base.")
   ├─ online:  FastAPI /ask
   └─ offline: gold.jsonl → same engine → results.jsonl → rule gates + gpt-5 judge
```
**v1 additions — must:** metadata filtering (`filters=`) · refusal gate (calibrated) ·
minimal demo UI (Streamlit over `/ask`) · portfolio deliverables (README + 1-page case study + 3–5 min video).
**v1 additions — if-time:** retrieved-chunk dedup · structured request log · per-query latency/cost capture.

### v1.5 — add only if the first eval shows a specific gap
- **Small-to-big** (expand `prev/next` + `parent_section_id`) — *if hit-rate@k is good but judge-faithfulness is low.*
- **Local cross-encoder rerank** (`ms-marco-MiniLM` on top-k) — *if precision@top is weak.*
- Both have seams already in the design — flip on, don't rebuild.

### v2 — backlog (see [`V2_DESIGN.md`](../FUTURE.md) for deep-dives)
- **Retrieval quality:** HANA `CROSS_ENCODE` rerank · HNSW index (at ~50k–100k+ vectors) · metadata score boosting + recency · query rewrite/HyDE · semantic re-chunking
- **Data layer:** Blockify-style KnowledgeBlocks (distill/dedup) · section summaries / contextual-retrieval · cross-reference graph
- **Providers/infra:** SAP Gen AI Hub + Orchestration · cost/latency routing + caching · fallback chains / circuit breakers · Cloud Foundry deploy · multilingual · multimodal
- **Security:** RBAC (role→content) + XSUAA auth — the `filters=` param is the ready hook
- **Eval/ops:** regression CI + prompt registry/A-B · judge panels + calibration + per-claim · citation entailment (NLI) · drift dashboards (retrieval + question-embedding PSI/KL)

---

## 2. Modeling = the whole config surface (experiment fingerprint)

The "model" in a RAG system is **not just the LLM** — it's the entire configuration, and
every knob moves the metric:

> embedding model · retrieval method + `k`/RRF · metadata filters · query rewrite ·
> reranker · **answer LLM** · **judge LLM** · **prompts** · the gold/adversarial set

So we **version the whole config as one experiment fingerprint**, stamped on every eval run
and every request-log line:
```jsonc
fingerprint = { embed_model, answer_model, judge_model,
                dense_k, sparse_k, rrf_k, top_k, sim_threshold,
                filters, prompt_version, gold_hash }
```
**Why:** reproducibility (attribute a number to an exact config) · A/B (compare configs on
the gold set) · regression (a config change is a "change" the eval gate must pass) ·
debugging (know which knob moved when a metric shifts).
**v1:** `prompts.py` `VERSION` + a `config.fingerprint()` helper (config.py already
centralizes the knobs). **v2:** full experiment tracking / prompt registry.

---

## 3. Key v1 serving-design decisions

**Refusal gate ("Not in knowledge base.")** — two layers:
(a) a deterministic **score threshold** (`SIM_THRESHOLD` on the top fused/cosine score) →
refuse below it; (b) the **prompt** instructs the LLM to refuse if the answer isn't in the
provided context. **Calibration:** tune `SIM_THRESHOLD` on the gold set so it refuses all
**7 adversarial negatives** (out-of-corpus → low top score) and answers the **33 positives**
(in-corpus → high top score) — sweep for the best separating boundary. Small calibration
set → rough; widen in v2.

**Retrieved-chunk dedup (deterministic, no LLM)** — before sending top-k to the LLM: drop
**exact dups** (same `content_sha256`) and **near-dups** (token-set **Jaccard > ~0.8** or
containment); keep the higher-ranked, **backfill** from the next candidates to keep *k
distinct*. Our 1-sentence overlap (~7% of a ~300-tok chunk) is **not** flagged — only true
dups are.

**Small-to-big context assembly (deferred to v1.5)** — retrieve **small** chunks (precise
match) → expand to **neighbors** (`prev/next`) + **parent section** (`parent_section_id`)
via stored IDs before generation. Fixes **"context too narrow"** (hit-rate good, faithfulness
low), **not** "wrong chunk retrieved" (that's a retrieval fix — rerank / better embeddings /
query rewrite). Trigger off the first eval numbers.

**Structured request log** — one JSONL record per request:
```jsonc
{ ts, query, filters, config_fingerprint,
  retrieved:[{id,score,method}…], answer, citations:[chunk_id…], answerable,
  scores:{top_sim, judge_faithfulness}, latency_ms:{embed,retrieve,generate}, tokens, cost }
```
Same schema as the eval `results.jsonl` (**eval = this log run over the gold set**). Feeds v2
regression (failures → cases) and drift monitoring (logged query embeddings).

**Eval (eval-what-you-serve)** — the eval pipeline *is* the serving core run over the gold
set + scored. **Rule gates** (no LLM): hit-rate@k · exact-negative refusal ·
citation-validity (chunk-id: exists-in-corpus **and** in-retrieved-set). **LLM-judge**
(gpt-5): faithfulness + relevance. Every run stamped with the experiment fingerprint.

---

## 4. DRY / maintainability principles (locked)
- **All knobs in `config.py`** — no hardcoded models/params/thresholds anywhere else.
- **Provider adapter (`providers.py`)** — OpenAI now, swappable (Gen AI Hub / local); SDK-native retry + timeout, no hand-rolled loops.
- **Prompts in `prompts.py` with `VERSION`** — never inline.
- **One core engine (`core.ask`)** shared by online (`/ask`) and offline (eval).
- **`filters=` param** = v1 metadata filtering **and** the v2 RBAC/recency hook.
- **`boost()` no-op after RRF** = the v2 metadata-scoring seam.
