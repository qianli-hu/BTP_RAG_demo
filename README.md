# BTP_RAG — Grounded RAG over SAP BTP documentation

A retrieval-augmented assistant over **SAP BTP docs** (AI Core, HANA Cloud Vector Engine, AI
Launchpad) with **SAP HANA Cloud Vector** as the production store: grounded answers with
**chunk-level citations**, a calibrated **refusal gate** for out-of-scope questions, and an
**adversarial eval harness** wired into CI.

![Architecture](docs/architecture.svg)

*Ingest is offline and deterministic; serving and eval share one engine (`core.ask`); the
store is two-track (SQLite dev/demo · SAP HANA Cloud prod) behind one interface.*

**Design docs:** [`docs/DESIGN.html`](docs/DESIGN.html) (the current system: pipeline,
serving topology, canonical path, four views, refusal gate, streaming, eval spine, and the
decision log with the measurement behind each choice) ·
[`docs/FUTURE_DESIGN.html`](docs/FUTURE_DESIGN.html) (forward design: planned infra,
serving-at-scale, rejected/alternative architectures) ·
[`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) (externally-reviewed M1/M2 build plan) ·
[`docs/SUPPORT.md`](docs/SUPPORT.md) (ops runbook) · [`docs/FUTURE.md`](docs/FUTURE.md)
(roadmap) · [`docs/archive/`](docs/archive/) (history).

## 1. What this is (no tech required)

A question-answering assistant over SAP BTP documentation that:
- answers **only from the documents**, with **citations** linking every claim to the exact SAP Help page;
- **refuses** questions the documentation can't answer (`Not in knowledge base.`) instead of guessing;
- is **measured**: every quality claim below comes from a versioned, repeatable evaluation.

**Live demo** — a real query against the running system: grounded answer, clickable
citations into help.sap.com, per-request latency/cost, the **experiment fingerprint**
(sidebar — every answer attributable to an exact config), and the **gpt-5 judge auditing
the answer live** with its rationale (faithfulness / correctness / relevance):

<img src="docs/demo.png" alt="Live demo: cited answer with experiment fingerprint, latency/cost telemetry and live LLM-as-judge scores + rationale" width="900">


## 2. Why RAG — measured, not assumed

Same model (`gpt-5-mini`), same 40 questions, same `gpt-5` judge — with vs without retrieval:

| | **RAG (this system)** | plain LLM, no retrieval |
|---|---:|---:|
| answer correctness (LLM-judged) | **0.962** | 0.575 |
| gold facts present verbatim (deterministic) | **22/27** | 6/27 |
| gave up ("I don't know") | 0/33 | 13/33 |
| out-of-scope questions correctly declined | **7/7** | 1/7 — *answered 6/7 ungoverned* |
| verifiable citations | 32/33 valid | impossible |

The last two rows are the enterprise argument: without retrieval the model **answers
out-of-scope questions from memory** — plausible, uncited, unauditable. RAG turns
"trust me" into "here's the source."

## 3. Measured results (M1 baseline · 48-item gold set, 17 adversarial, dev/test split)

The gold set is split **dev 26 / test 22** at authoring time: dev is for tuning (sweeps,
thresholds), test is sealed for final verdicts — so the honest column is **test**.

| metric | dev | **test** | how scored |
|---|---|---|---|
| evidence-recall@5 *(new)* | 0.833 | **0.781** | rule: fraction of each answer's required **evidence atoms** (verbatim source quotes) covered in top-5 |
| complete@5 *(new)* | 15/19 | **11/16** | rule: ALL atoms covered |
| retrieval hit-rate@5 | 0.818 | **0.737** | rule: gold section in top-5 |
| correctness | 0.99 | **0.908** | gpt-5 judge vs gold answer |
| faithfulness (anti-hallucination) | 0.993 | **0.969** | gpt-5 judge vs the exact context the model saw |
| out-of-corpus refusal | 4/4 | **3/3** | exact-string rule (0 false refusals on test) |
| citation validity | 21/21 | **19/19** | rule: cited chunk-id exists **and** was retrieved |
| TTFT (streaming, `reasoning_effort=low`) | median 2.4 s | 2.4 s | request log; medians of repeated runs only |

Every run is fingerprint-stamped (models + params + prompt version + view policy + gold +
corpus hashes) and recorded in [`eval/registry.jsonl`](eval/registry.jsonl) with immutable
per-item archives; raw latency/refusal probes are committed under
[`eval/measurements/`](eval/measurements/) — **published claims are distributions, never
single runs** (a single-run TTFT headline was falsified by 3 repeats and corrected; see
[`docs/DESIGN.html`](docs/DESIGN.html) D6/D8).

Two findings the new metrics exposed (both invisible to plain hit@k):
- **9/35 atom-carrying questions are missing ≥1 evidence piece in top-5** — answers built
  on partial evidence (one scored correctness 0.65 for exactly this reason). This is the
  measured target for the M2 retrieval work.
- **Refusal quality is reasoning-bound**: at `reasoning_effort=minimal` the system falsely
  refused 33% of answerable dev questions (false-premise correction failed 6/6); `low`
  brought false refusals to 0/48 at half of medium's latency — chosen as the operating
  point by sweep, not by taste.

## 4. Current work — the M-series (reviewed build plan)

Full detail in [`docs/BUILD_PLAN.md`](docs/BUILD_PLAN.md) (externally reviewed; 8 findings
incorporated) and the two design docs linked at the top.

**M1 — build the honest ruler · ✅ complete (2026-07-20):**
- **One canonical generation contract** (prompts v3): the model always writes plain text
  with inline `[chunk-id]` citations; endpoints assemble JSON; `generate()`/`ask()` are
  buffered wrappers over the streaming twins — eval measures the served path *by
  construction*. Split `ANSWER_`/`JUDGE_REASONING_EFFORT` knobs; operating point `low`
  chosen by a 3-arm × 48-run dev sweep.
- **Four provenance views** (`views.py` + fingerprinted `VIEW_POLICY`): retrieval /
  answering / citation / judge renderings of one node; the judge view *is* the answering
  view (one builder, two callers); 6 governance tests make violations impossible.
- **Evidence atoms + evidence-dense gold**: labels = verbatim source quotes (survive any
  re-chunking); 8 new multi-evidence questions; `evidence-recall@5` + `complete@5`;
  dev/test split; registry schema v2 (full metric set per split); baseline recorded.

**M2 — retrieval improvements + real infra, each A/B'd against the M1 baseline (next):**
1. **Qdrant server-side hybrid** (dense HNSW + sparse BM25 + RRF fused in-DB → the app
   goes stateless) + **docker compose** (app + qdrant) — parity-gated on the full eval
2. **vLLM first contact**: rent a GPU, serve an open model behind vLLM's
   OpenAI-compatible endpoint — the adapter swap is two env vars; same eval → an honest
   API-vs-self-host economics row
3. Quality A/Bs on the new ruler: contextual sparse → local cross-encoder rerank +
   parent/adjacency expansion → doc2context (provenance-staged, retrieval-only first)

*(Evaluated and rejected with reasons — see decision log: RAPTOR (global recompute on
every ingest, stochastic clustering vs reproducibility, citation indirection).)*

## 5. Key design decisions (each: what / why)

Full decision log with evidence: [`docs/DESIGN.html`](docs/DESIGN.html) §9.

| decision | why |
|---|---|
| **One canonical generation contract** — model writes plain text + inline `[chunk-id]`; JSON assembled by endpoints; non-streaming = buffered wrapper over streaming | eval-what-you-serve *by construction*: eval, `/ask`, `/ask/stream` are one code path (v2 had two diverging protocols — caught by review, measured, fixed) |
| **`reasoning_effort=low`, chosen by sweep** | refusal layer-2 and false-premise correction are *reasoning tasks*: `minimal` falsely refused 33% (false-premise 6/6); `low` = 0/48 false refusals at half of `medium`'s TTFT |
| **Evidence atoms** — gold labels are verbatim source quotes, not chunk ids | labels survive re-chunking (chunking A/Bs can't invalidate their own ruler); enables `evidence-recall@5`, which sees *partial* evidence coverage that hit@k structurally cannot |
| **Dev/test gold split**, assigned at authoring | tuning on the regression set turns it into a training set; sweeps/thresholds use dev, verdicts use sealed test |
| **Four provenance views** over one node (`views.py`, fingerprinted policy) | M2's generated enrichment may boost *findability* but must never be quoted as *evidence* or leak into the judge; judge view ≡ answering view (one builder, two callers), enforced by tests |
| **Distributions, never single runs** | a single-run TTFT headline was falsified by 3 repeats; all published latency/refusal claims are medians of ≥5 runs with committed raw artifacts (`eval/measurements/`) |
| **Two-track store** (`SqliteStore` dev/demo, `HanaStore` prod) behind one `VectorStore` interface | dev/prod parity + demo resilience; only `dense_search` is backend-specific — embedding, BM25, RRF, dedup are one fixed core |
| **Hybrid retrieval**: dense (cosine) ⊕ sparse (BM25) fused by **RRF** | dense catches paraphrase, sparse catches exact SQL identifiers (`REAL_VECTOR`); RRF fuses by rank so score scales don't matter |
| **BM25 in the app layer** | HANA **free tier** doesn't support in-DB full-text (verified empirically); index rebuilds in 38 ms at startup — always statistically exact. Paid HANA moves it in-DB unchanged |
| **Exact cosine scan, no HNSW** | at 1,216 vectors exact is sub-ms and 100% recall; HNSW is the documented scale lever (free tier supports it — verified) |
| **Two-layer refusal gate** | layer 1: top-cosine < **0.56** → refuse with zero LLM cost; layer 2: prompt-level refusal. Threshold **calibrated** on the 7 adversarial negatives (clean separation: negatives ≤ 0.550, positives ≥ 0.572 → 40/40) |
| **Citations = chunk ids**, validated by rule | URL-level checks pass wrong-section citations; chunk-id must exist in corpus **and** in that query's retrieved set |
| **Eval split: rules judge retrieval, LLM judges generation** | we built gold section labels, so retrieval is scored deterministically (free, zero variance, CI-safe); the gpt-5 judge handles only what rules can't read (faithfulness/correctness/relevance). Judge ≠ answer model (less self-preference bias) |
| **Adversarial gold set** (17/40): negatives, distractors, false premises, entity confusion | tests refusal, precision under lexical traps, anti-sycophancy — negatives verified absent from the corpus before inclusion |
| **Experiment fingerprint + registry** | the "model" = embed+LLMs+params+prompt_version+**gold_hash**+**corpus_hash**; every run appends to `eval/registry.jsonl` with per-item results archived — any number is attributable and reproducible |
| **Config-driven everything** (`config.py`, `providers.py`, `prompts.py`) | no hardcoded models/prompts/params; provider adapter makes OpenAI→SAP Gen AI Hub a config change |
| **CI as quality gate** | every PR: lint + unit tests; retrieval-touching PRs: deterministic eval gates (hit-rate ≥ 70%, refusal separation); **nightly rebuilds from live SAP docs = drift detection** with a triage matrix (corpus-hash diff × gate verdict) |

## 6. Ownership & governance — who computes what, who stores what

Verified empirically on the live instance while the HANA trial was active (registry:
`gates-hana` — **identical gate results on both stores**, dev/prod parity measured).
*Status note: the HANA trial has since ended — the HANA code stays as verified integration
proof, SQLite remains the dev/demo track, and Qdrant (server-side hybrid) is the planned
replacement store (BUILD_PLAN M2.4).* Three reasons a piece runs where it runs:
**(C)** platform constraint (trial/free tier) · **(D)** deliberate design · **(S)** scale-gated.

| part | computed by | stored in | why here | with FULL HANA access (paid + entitlements) |
|---|---|---|---|---|
| fetch + chunking + ids/hashes | app (deterministic, no LLM) | — | **D** — ingestion is app logic everywhere | unchanged |
| chunk text + metadata | — | **HANA** `BTP_RAG.CHUNKS` (SQLite on dev track) | **D** — single source of truth in the DB | unchanged |
| **embedding computation** | **OpenAI API** (app orchestrates) | — | **C** — no Gen AI Hub entitlement on trial; free tier lacks the NLP service for in-DB `VECTOR_EMBEDDING()` | **in-platform**: HANA `VECTOR_EMBEDDING()` or Gen AI Hub embeddings — text never leaves SAP (data residency) |
| embedding storage | — | **HANA** `REAL_VECTOR(1536)` | works even on free tier | unchanged; re-embed on provider switch |
| **dense search** | **HANA** (`COSINE_SIMILARITY`, server-side) | — | **D** — the point of HANA Cloud Vector | + `HNSW VECTOR INDEX` at ~100k+ vectors (**S**; verified available even on free) |
| **sparse/BM25** | **app** (`rank_bm25`, in-memory index built at startup from HANA text) | index not persisted (38 ms rebuild) | **C** — free tier blocks `CREATE FULLTEXT INDEX` + PAL BM25 | **in-DB**: `CONTAINS()`/`SCORE()` full-text or PAL BM25 — sparse never leaves the DB |
| RRF fusion + dedup | app, at query time | — | **D** — pure rank math, backend-independent | optional move in-DB (`langchain-hana` hybrid); app-side stays legitimate |
| refusal gate (0.56) | app (threshold on HANA-computed score) | — | **D** | unchanged |
| answer + judge LLMs | OpenAI (`gpt-5-mini` / `gpt-5`) | — | **C** — no Gen AI Hub on trial | **Gen AI Hub** (+ Orchestration: content filtering, data masking, templating) — LLM calls in-platform, one SAP contract |
| reranker (v1.5) | local cross-encoder (planned) | — | **C** — `CROSS_ENCODE` needs the paid NLP service | **HANA `CROSS_ENCODE`** — retrieve→fuse→rerank fully in-DB |
| DB credentials | `DBADMIN` via `.env` | — | **C/D** — trial shortcut | least-privilege technical user (SELECT/INSERT on `BTP_RAG` only) + XSUAA/service-binding injection, no password in env |

**The one-line summary:** with full access, every **(C)** row collapses into the SAP platform —
embedding, sparse search, reranking, and LLM calls all move in-platform/in-DB and corpus text
never crosses a third-party boundary; the **(D)** rows (chunking, RRF, refusal, eval) stay in
the app because they belong there. The provider adapter and `VectorStore` interface mean those
moves are config changes, not rewrites.

## 7. Module map

| module | path | inspect with |
|---|---|---|
| shared kernel | `config.py` `providers.py` `prompts.py` `views.py` `core.py` | `python3 -c "import config,json;print(json.dumps(config.fingerprint('v3'),indent=2))"` |
| corpus | `corpus/` (+ committed `MANIFEST.json`) | `cat corpus/MANIFEST.json` |
| chunking | `ingest/` | `cat ingest/out/chunks_report.md` |
| retrieval | `retrieve/` (store, hybrid, generate) | `PYTHONPATH=. python3 retrieve/hybrid.py "your query"` |
| serving | `app/` (FastAPI: `/ask`, `/ask/stream` SSE, `/judge` + Streamlit UI w/ live gpt-5 judge) | `uvicorn app.main:app` → `localhost:8000/docs` |
| eval & gates | `eval/` (gold/ 8 files · scorers incl. evidence atoms · judge · registry v2) | [`eval/README.md`](eval/README.md) — incl. drill-down recipe |
| measurements | `eval/measurements/` (repeat probes, sweeps, raw artifacts) | `cat eval/measurements/README.md` |
| CI | `.github/workflows/` + `tests/` (20) | `pytest && ruff check . && PYTHONPATH=. python3 eval/gates.py` |

## 8. Run it

```bash
pip install -e ".[dev]"                        # or: uv sync
./corpus/fetch.sh                              # fetch the 3 SAP docs (© stays local, gitignored)
python3 ingest/chunk.py                        # → ingest/out/chunks.jsonl (1,216 chunks)
PYTHONPATH=. python3 ingest/embed.py           # embed → local SQLite (~$0.005)
PYTHONPATH=. python3 eval/gates.py             # deterministic quality gates
PYTHONPATH=. python3 -m uvicorn app.main:app   # serve → http://localhost:8000/docs
python3 -m streamlit run app/ui.py             # demo UI (answers + citations + live judge)
```
Needs `.env` (see `.env.example`): `OPENAI_API_KEY`; optionally HANA creds + `STORE=hana`
to run the identical pipeline on SAP HANA Cloud.
