# BTP_RAG v2 Build Plan — Milestones M1 & M2

*Status: v2 REVISED per external review · 2026-07-18 · branch `sanechips-rag`*
*v1 → v2 changelog at the end (§9). All 8 review findings accepted; none rejected.*

Self-contained design doc: context, measured findings, decisions with rationale, and the
two-milestone build breakdown. Reviewers need no other context than this file (repo code
references are given as `path:line`).

---

## 0. Current state (v1, shipped)

- **Pipeline:** hybrid retrieval (dense cosine ⊕ app-layer BM25 → RRF → dedup → top-5) →
  two-layer refusal gate (score gate `SIM_THRESHOLD=0.56`, then LLM refusal string) →
  grounded generation with chunk-id citations validated against the retrieved set.
- **Eval:** gold set (**40 items**: 33 positives across 6 answer types + 7 negatives)
  driven through the same `Engine` the API serves; rules score retrieval (hit@k,
  refusals, citation validity); gpt-5 judges generation (faithfulness / correctness /
  relevance); runs stamped with an experiment fingerprint and appended to a registry
  with immutable per-run archives. 13 tests pass (`tests/`).
- **Serving:** FastAPI `/ask` (JSON protocol: model emits a JSON envelope,
  `prompts.py:13`) and `/ask/stream` (SSE; model emits plain text + inline citations,
  `prompts.py:31`) + Streamlit UI with live judge.
- **Stores:** SQLite (default; vectors cached in RAM, brute-force cosine) and HANA Cloud
  (in-DB cosine; **trial ended** — code kept as integration proof).

## 1. Measured findings that force this plan

**F1 — The serving surface diverged from the eval, in TWO ways.**
(a) `run_eval.py:34` drives `eng.ask()` while the demo UI drives `ask_stream` —
different prompt AND different reasoning effort. (b) Deeper (review finding): the two
public endpoints themselves use **different answer protocols** (JSON envelope vs plain
text + inline citations), so "make eval call ask_stream" alone would still validate only
one of two live paths. Fix in M1.1: **one canonical generation contract** (§2.5).

**F2 — Single-run latency claims did not reproduce.**
The 1.1 headline (TTFT 1.36 s vs 4.85 s total) was a 1-in-6 lucky run. Repeated runs
(n=15 across configs): at `minimal` effort the streaming path **refuses multi-evidence
("synthesis") questions ~5/6** — layer-2 refusal is a *judgment* ("is the answer present
in the context?") that needs reasoning tokens when evidence is scattered across chunks;
at `medium` it answers 3/3 but TTFT ≈ 12 s (thinking-bound; streaming removes only
write-wait, not think-wait). Fact-lookups are fine at minimal (3/3, ~1.2 s).
*Reproducibility (review):* these probes were ad-hoc; M1.1 re-runs them via a committed
script (`eval/measurements/`) so the raw artifact exists. Published latency/refusal
claims = median of ≥5 runs of the selected configuration.

**F3 — `hit@k` is a single-evidence metric and cannot see retrieval-structure wins.**
`eval/score_retrieval.matches` is satisfied by ONE gold chunk in top-k. An
evidence-dense question (answer requires {A,B,C,D} across chunks) scores identically
whether retrieval found 1 or all 4 pieces. Any A/B of chunking/context/rerank changes is
blind on the axis it targets (and F2's refusals are exactly evidence-dense failures).
*Additional constraint (review):* gold evidence today is keyed to **chunk ids**, which
are chunking-dependent (`id = doc#section_id#ordinal`, `ingest/chunk.py:244`) — a
chunking A/B would invalidate the labels themselves. Fix: stable evidence atoms (§2.6).

**F4 — RAPTOR evaluated and rejected.**
(a) global re-cluster + re-summarize on every corpus update — heavy recompute, no clean
incremental insert; (b) stochastic clustering (UMAP/GMM) fights fingerprint
reproducibility; (c) summary nodes break citation semantics. Replaced by an
all-incremental contextual-retrieval stack (M2), every technique per-chunk or query-time.

**F5 — HANA trial ended.**
Migrate to Qdrant (local Docker; server-side hybrid). Keep SQLite as the zero-dependency
default and the A/B baseline; keep HANA code as integration proof. The old shared-cursor
concurrency bug was HANA-specific; Qdrant's client is thread-safe HTTP.

**F6 — (review) The fingerprint cannot identify M2 corpora.**
`corpus_hash` hashes `corpus/MANIFEST.json` = source-document shas + counts. It does NOT
cover chunk text, `embed_text`, enrichment prompts/outputs, sparse representation, or
index configuration — so "changing `embed_text` produces a new corpus_hash" (claimed in
v1 of this doc) is **false**. Fix: `index_hash` (§2.7).

## 2. Core design

### 2.1 Node fields (stored once, in the store payload)

| field | content | provenance |
|---|---|---|
| `raw_text` | verbatim source passage (today's `text`) | authoritative |
| `source_context` | doc title, heading path, prev/next ids, page (today partially in `embed_text`, `ingest/chunk.py:249`) | deterministic — safe |
| `generated_context` | LLM-written situating sentence ("doc2context") | generated — useful, not citable |
| `synthetic_queries` | doc2query questions the chunk might answer | generated — retrieval feature only |
| `provenance_evidence_ids` | stable evidence atoms this node covers (§2.6) | derived at ingest |

### 2.2 Four views (query-time renderings; only the retrieval view is embedded/stored)

| view | sees | rationale |
|---|---|---|
| **retrieval** (dense embed + sparse BM25) | all four content fields, maximally rich | recall; enrichment can be aggressive here |
| **answering** (context given to the answer LLM) | `raw_text` + `source_context`; `generated_context` ONLY when explicitly promoted, then labeled `[GENERATED — NOT EVIDENCE]`; `synthetic_queries` never | grounding without provenance confusion |
| **citation** | `raw_text` + doc/heading/page only | citations resolve to authoritative source text only — no laundering |
| **judge** | **exactly the answering view** + gold | eval-what-you-serve at the judge level |

Renderer support for prev/next *extractive* snippets is built in M1.2 but **inactive in
the M1 baseline** (review §7-3); expansion activates only in M2.2 so the baseline does
not silently change.

### 2.3 Judge metrics (revised per review)

- **answer_context_faithfulness** → did the answer stay within **what the model saw**
  (the answering view)? Model-level anti-hallucination. Judging vs raw_text only would
  mislabel faithful use of provided context as hallucination.
- **source_groundedness** *(new — review finding 4)* → is every factual claim in the
  answer supported by **authoritative raw evidence** (`raw_text` of retrieved nodes)?
  Product-level guarantee. Under retrieval-only staging the two coincide; once
  `generated_context` is promoted they diverge, and the pair attributes faults:
  groundedness FAIL + context-faithfulness PASS ⇒ **pipeline** fault (bad enrichment);
  both FAIL ⇒ **model** fault.
- **correctness** → vs gold/truth.
- **relevance** → vs the question.
- Additionally, `generated_context` is validated against `raw_text` **at ingestion**
  (entailment check: generated claims must not exceed the source) — catch bad enrichment
  before it is ever indexed, not only at answer time.

**Invariant:** the judge's context is built by the SAME renderer as the answering view
(one builder, two callers) so the two cannot drift by construction.

### 2.4 View policy as config (fingerprinted)

```python
VIEW_POLICY = {
    "retrieval": ["source_context", "generated_context", "synthetic_queries", "raw_text"],
    "answering": ["source_context", "raw_text"],
    "citation":  ["raw_text"],
    "judge":     "answering",          # by reference: judge == answering view (+ gold)
}
ANSWERING_INCLUDES_GENERATED = False   # staging flag; flipping it is a fingerprinted A/B
```

**Staging:** `generated_context` / `synthetic_queries` start **retrieval-only**.
Promotion into the answering view is a separate, fingerprinted A/B.

### 2.5 One canonical generation contract (review finding 1)

The model **always** writes plain text with inline `[chunk-id]` citations (the current
streaming prompt). JSON is a **transport concern, assembled by the endpoint**, never by
the model:

- `/ask/stream`: tokens forwarded as SSE; citations extracted server-side from the
  finished text; `done` event carries the envelope.
- `/ask`: same generation, buffered server-side; citations extracted the same way; the
  endpoint assembles the same JSON response schema clients already use (no client-facing
  change).
- The JSON-emitting prompt (`ANSWER_SYSTEM`, `prompts.py:13`) is **retired from
  serving**; `prompts.VERSION` bumps. One prompt, one protocol, two transports — eval of
  the canonical path now covers both endpoints.

**Reasoning effort is split** (review): `ANSWER_REASONING_EFFORT` and
`JUDGE_REASONING_EFFORT`, passed explicitly per call site — a shared global would let an
answer-side sweep silently change the judge. Both fingerprinted.

### 2.6 Stable evidence atoms (review finding 3)

Gold evidence must survive chunking changes. Evidence is defined against the **source
document**, not the chunking:

```
evidence_id = sha( doc_sha + source_url + page + normalized_quote )
```

- Gold items store evidence atoms (verbatim, whitespace/case-normalized quotes from the
  source doc + location).
- At ingest, each node computes `provenance_evidence_ids` = atoms whose normalized quote
  is contained in the node's `raw_text` (for future summary/expanded nodes: contained in
  any leaf's raw_text in its provenance).
- The matcher then works uniformly for chunks, parents, adjacency-expanded contexts, and
  any future node type — and a chunking A/B can never invalidate the labels.

### 2.7 `index_hash` — identify the index, not just the sources (review finding 2)

New manifest, hashed into the fingerprint as `index_hash`, covering:

- canonical node payloads (raw_text + source_context + generated_context +
  synthetic_queries) and the **rendered retrieval text** per node;
- chunker version, enrichment prompt versions (doc2context / doc2query);
- embedding model + dim, sparse model/tokenizer identity;
- store index configuration (Qdrant collection config, HNSW params, image version);
- vector count.

Fingerprint additions beyond `index_hash`: `STORE`, serving protocol/endpoint arm,
answer-context token budget (§2.8), `RERANK_TOP_N` + candidate counts, expansion limits,
truncation policy, gate version, `ANSWER_REASONING_EFFORT` / `JUDGE_REASONING_EFFORT`.
(`corpus_hash` stays = source-document identity; `index_hash` = derived-index identity.)

### 2.8 Context-budget controls (review finding 6)

Parent/adjacency expansion must not win by simply spending more context. Fingerprinted
and held constant across A/B arms: candidate count before rerank, expansion limits,
**final answer-context token budget**, truncation policy. Every eval row reports context
tokens, retrieval latency, TTFT, total latency, and cost alongside quality — a win must
survive the cost column.

### 2.9 Dev/test split (review finding 5)

Tuning on the regression set converts it into a training set. Split (stratified by
answer_type):

- **dev/calibration** — effort sweep, `SIM_THRESHOLD` recalibration, RRF params,
  tokenizer choices;
- **locked test** — final comparisons only; published claims come from here.

M1.3's new evidence-dense questions are split the same way (authored from the docs
BEFORE the new retrieval runs, to avoid flattering the stack). The 0.56 refusal
threshold is **recalibrated on dev whenever the retrieval representation changes**
(embed_text content, embedding model, store scoring, enrichment) — M2.3's generated
context shifts cosine distributions even with the embedding model unchanged.

**Run-count policy (review §7-2):** retrieval metrics — 1 run (deterministic);
generation during tuning — 3 runs for synthesis/adversarial types; published
latency/refusal claims — 5 runs of the selected configuration, medians + distributions.

---

## 3. Milestone M1 — the honest ruler (local, no new infra; OpenAI API remains external; ≈ $1–2)

*Goal: after M1, any retrieval change can be A/B'd truthfully. No retrieval improvements
in M1 itself.*

- **M1.1 Eval-integrity fix** *(F1, F2 — do first)* — **✅ DONE 2026-07-19.**
  Canonical contract shipped (`generate()`/`ask()` are buffered wrappers over the
  streaming twins — one code path, two transports; `ANSWER_SYSTEM` JSON prompt retired,
  prompts v3); efforts split (`ANSWER_`/`JUDGE_REASONING_EFFORT`, explicit per call);
  gold split dev 22 / test 18 (stratified alternation); F2 probe + sweep committed under
  `eval/measurements/`. **Sweep verdict (registry, dev split): minimal = 33% false
  refusals (false-premise 6/6 — premise correction is also a reasoning task; NEW finding
  beyond F2), low = 0/48 false refusals · corr 0.989 · TTFT ½ of medium, medium = no
  quality gain over low. Operating point: `low` (config default), confirmed 5×
  sequential: 10/10 answered, TTFT median 2.1 s synthesis / 1.2 s lookup (vs medium
  ~7.4 s).** 13/13 tests pass.
  - Canonical generation contract (§2.5): both endpoints on the plain-text protocol;
    JSON assembled at the endpoint; retire `ANSWER_SYSTEM`; bump `prompts.VERSION`.
  - Split `ANSWER_REASONING_EFFORT` / `JUDGE_REASONING_EFFORT` (explicit per call).
  - `run_eval.py` drives `Engine.ask_stream` (join tokens) = the canonical path.
  - Commit the F2 repeat-probe script + raw results under `eval/measurements/`.
  - Effort sweep on **dev split** ∈ {minimal, low, medium} × answer_type (3 runs on
    synthesis/adversarial types); pick operating point; 5-run confirmation of the
    selected config; registry rows per arm. (~$1, budget-capped.)
- **M1.2 Four-view schema + policy config** *(§2.1–2.5)* — **✅ DONE 2026-07-19.**
  `views.py` renderers (retrieval / answering / citation; judge = answering BY
  REFERENCE); `VIEW_POLICY` + `ANSWERING_INCLUDES_GENERATED` in config and fingerprint;
  prompts + endpoint citations wired through the renderers; judge context now rendered
  by the SAME builder as the answering view (previously the judge saw a poorer
  `[id] text` rendering — invariant enforced, judge input slightly enriched). 6
  governance tests in `tests/test_views.py`: judge≡answering under both flag states,
  synthetic_queries unreachable in answering/judge, generated staged out by default +
  labeled when promoted, citation never carries generated content, legacy-format
  equality on today's nodes. 19/19 tests pass; live smoke OK.
  - Node fields (+ empty `generated_context`/`synthetic_queries`,
    `provenance_evidence_ids`); `build_retrieval_text / build_answering_context /
    build_citation` (+ judge by reference) reading `VIEW_POLICY`; prev/next renderer
    support built but **inactive**; wire prompts/serving/eval through the renderers.
- **M1.3 Evidence-dense eval** *(F3, §2.6)* — **✅ DONE 2026-07-19** (pending user
  review of the 8 authored questions). Atom machinery in `score_retrieval.py`
  (`norm`/`atom_covered`/`atoms_of`/`evidence_recall_at_k`); existing items' atoms
  derived from their verified `expect_terms`; atom-quote validation added to gold
  validation (every quote must exist verbatim in its doc); `evidence-recall@5` +
  `complete@5` wired into run_eval report + registry rows. NEW
  `eval/gold/evidence-dense.jsonl`: 8 questions (q41–q48, 4 dev / 4 test, 3–4 verbatim
  atoms each, authored from the corpus BEFORE any new retrieval exists; two are
  cross-doc). Gold now 48 items; 20/20 tests.
  - Author multi-evidence gold questions annotated with FULL evidence-atom sets
    (new answer_type `evidence_dense`), authored from the docs before new retrieval
    exists; assign dev/test membership at authoring time.
  - Migrate existing gold evidence references to evidence atoms.
  - Metrics: **evidence-recall@k** (graded fraction of required atoms in top-k) AND
    **complete@k** (strict all-atoms-present), macro-averaged (review §7-1);
    provenance-aware matcher per §2.6.
- **M1.4 Registry schema v2 + baseline record** *(review finding 8)* — **✅ DONE
  2026-07-20.** `summarize()` in run_eval: full metric set per split (hit5,
  evidence-recall@5, complete@5, negative/false refusals, citation validity, judge
  triad, latency median/p95 + TTFT, prompt-token median, cost); one full run → separate
  dev and test registry rows. **BASELINE (the line M2 must beat):
  dev — ER@5 0.833, complete@5 15/19, hit5 0.818, corr 0.99, 1 false refusal (q47);
  test — ER@5 0.781, complete@5 11/16, hit5 0.737, corr 0.908, 0 false refusals;
  negatives 7/7, citations 40/40 valid.** The new metric immediately shows the M2
  target: 9/35 atom-carrying items are missing ≥1 evidence piece in top-5; weakest
  cells: false-premise corr 0.6, q42 corr 0.65 (evidence-dense, test), q47 refused.
  Cost $0.47; fingerprint carries view policy + efforts + splits + new gold_hash.
  - Registry rows carry the full acceptance metric set: hit@k, evidence-recall@k,
    complete@k, citation validity, false refusals, negative refusal, judge metrics
    (incl. source_groundedness), context tokens, latency distributions
    (median/p95/TTFT), cost.
  - Run the full new eval on the current system (no retrieval changes), dev and test
    reported separately → the baseline every M2 step must beat, with **paired
    per-question comparisons** (same items, side-by-side) as the analysis unit.

**M1 acceptance:** eval drives the canonical served path (one protocol, both
transports); answer/judge efforts independent, operating point chosen on dev and
confirmed 5×; locked dev/test separation recorded; four views rendered from
fingerprinted config; stable evidence atoms in gold + `provenance_evidence_ids` on
nodes; `index_hash` implemented and in fingerprint; context budget fixed and
fingerprinted; registry schema v2 carries every acceptance metric; baseline rows (dev +
test) in registry; F2 measurement artifacts committed.

## 4. Milestone M2 — retrieval improvements + real infra (each step A/B'd on M1's ruler)

- **M2.1 Contextual sparse** — build BM25 from the context-enriched text instead of raw
  `text` (`retrieve/hybrid.py:52` indexes raw text while dense already embeds enriched
  `embed_text` — close the asymmetry). ~Free. New `index_hash`.
- **M2.2 Reranker + parent/adjacency expansion** — local cross-encoder (`RERANK_*` seam
  exists) over fused candidates; THEN expand survivors via `parent_section_id` /
  `prev_id` / `next_id`; dedup overlaps (sha256 + Jaccard); all under the fixed context
  budget (§2.8) — expansion trades breadth for depth inside the same token budget.
  **Gate wrinkle:** the refusal gate keeps reading the DENSE cosine, not rerank scores.
- **M2.3 doc2context** — per-chunk LLM `generated_context` (incremental, prompt-cached,
  ~$2 one-time), retrieval-only per staging; ingest-time entailment validation (§2.3);
  re-embed → new `index_hash`; recalibrate `SIM_THRESHOLD` on dev (§2.9).
  **doc2query only if** evidence-recall still shows a gap afterward.
- **M2.4 Qdrant migration + docker compose** *(F5)*
  - `QdrantStore(VectorStore)`; server-side hybrid: named dense vector (HNSW+cosine) +
    named sparse vector (Qdrant built-in BM25, `modifier=idf`) + server-side RRF via the
    Query API — app drops its in-RAM BM25 index and becomes stateless.
  - **Gate score (review finding 7):** Qdrant's fused response does not expose per-
    prefetch dense scores — issue an explicit **batched** query (hybrid + dense-only in
    one request) and read the gate cosine from the dense-only result. Pin the Qdrant
    image version (goes into `index_hash`).
  - Tokenizer: **built-in BM25 first** (review §7-4); our custom identifier-preserving
    tokenizer (`hybrid.py:22`) is retained only if identifier-heavy parity tests
    (`REAL_VECTOR`-style queries) show meaningful regression.
  - `docker-compose.yml`: `app` (Dockerfile) + `qdrant` (pinned official image) +
    volume.
  - **Parity gate:** full eval on Qdrant must match/beat the SQLite baseline (incl.
    evidence-recall + identifier-heavy subset) before switching defaults.

**M2 acceptance:** each step has a registry A/B row vs M1.4 baseline (paired
per-question, dev for tuning / test for the verdict, quality + context-tokens + latency
+ cost); Qdrant passes the parity gate; `docker compose up` brings up the full stack.

## 5. Deferred / out of scope

- **Late chunking** (token-level embeddings pooled per chunk): needs a self-hosted
  long-context embedding model (OpenAI's API returns no token embeddings) → deferred to
  the vLLM/self-hosting track.
- **RAPTOR:** rejected (F4). Revisit only for large cross-document thematic corpora.
- Async conversion + load-test ladder, and vLLM first contact: next phases after M2.

## 6. Risks & mitigations

| risk | mitigation |
|---|---|
| effort sweep shifts the operating point → prior numbers move | registry keeps arms attributable via fingerprint; dev/test split isolates tuning |
| evidence-dense gold authored to flatter the new stack | authored from docs BEFORE new retrieval runs; authorship note + dev/test membership fixed at authoring time |
| Qdrant tokenizer regresses exact-identifier recall | identifier-heavy parity subset; SQLite baseline switchable via `STORE`; custom-tokenizer fallback path |
| expansion wins by spending more context | fixed answer-context token budget, fingerprinted; cost/latency reported next to quality (§2.8) |
| generated context introduces wrong claims | ingest-time entailment validation; retrieval-only staging; source_groundedness metric; promotion is a separate fingerprinted A/B |
| judge silently changed by answer-side tuning | separate `JUDGE_REASONING_EFFORT`; judge model + effort fingerprinted |
| gold/threshold overfitting | locked test split; threshold recalibrated on dev only (§2.9) |
| single-run claims (repeat of F2) | run-count policy (§2.9); committed measurement scripts + raw artifacts |
| index changes invisible to fingerprint | `index_hash` (§2.7) covers payloads, renderings, enrichment prompts, models, index config |

## 7. Review Q&A (resolved)

1. **Evidence-recall definition** → report BOTH graded macro evidence-recall@k and
   strict complete@k, over stable evidence atoms (not chunk ids).
2. **Sweep repeats** → retrieval 1×, tuning-time generation 3× on synthesis/adversarial
   types, published claims 5× of the selected config.
3. **prev/next in answering view** → renderer support in M1, **inactive** in baseline;
   activates in M2.2.
4. **Qdrant sparse** → built-in BM25 + pinned image first; custom tokenizer only on
   demonstrated identifier regression.
5. **M1 acceptance gaps** → added: locked dev/test separation, stable evidence ids,
   `index_hash`, fixed context budget, independent answer/judge effort, threshold
   recalibration procedure, paired per-question comparisons, registry schema v2.

## 8. External review artifacts

- Review conducted 2026-07-18 (Codex): 8 findings (2 critical, 4 high, 2 medium) + 5
  §7 answers. All accepted. `pytest -q` 13/13 pass at review time; no code changed
  during review.

## 9. v1 → v2 changelog

| finding | change |
|---|---|
| 1 (critical) — two protocols, shared effort knob | §2.5 canonical plain-text contract, JSON assembled at endpoint, `ANSWER_SYSTEM` retired; split ANSWER/JUDGE effort |
| 2 (critical) — corpus_hash can't see index changes | §2.7 `index_hash` + expanded fingerprint; false v1 claim corrected (F6) |
| 3 (high) — evidence labels chunking-dependent | §2.6 stable evidence atoms + `provenance_evidence_ids` |
| 4 (high) — correctness insufficient net | §2.3 source_groundedness + ingest-time entailment validation |
| 5 (high) — tuning on the regression set | §2.9 dev/test split + threshold recalibration policy |
| 6 (high) — expansion wins by context spend | §2.8 fixed context budget + cost columns |
| 7 (medium) — Qdrant prefetch scores | M2.4 batched dense-only query for the gate; pinned image |
| 8 (medium) — unreproducible claims | 40 items corrected; ~$1 sweep; F2 artifacts committed (M1.1); "all local" reworded; registry schema v2 (M1.4) |
