# HANDOFF — continuing on a new machine

*Written 2026-07-22 on the primary Mac, branch `sanechips-rag`, after the M1-complete
push. Audience: the next working session (new Mac + Claude Code). Read top to bottom
before building anything.*

## 1. Where the project stands

- **M1 is complete** (see `docs/BUILD_PLAN.md` — every M1.x entry carries a ✅ with
  results; `README.md` §3 has the baseline table). One-line summary: the eval now drives
  the exact served path (canonical plain-text contract, prompts v3), reasoning effort
  `low` was chosen by sweep, gold is 48 items with a dev/test split and
  chunking-independent evidence atoms, and the M1 baseline is recorded as two registry
  rows (dev: ER@5 0.833 · test: ER@5 0.781) — the line every M2 change must beat.
- **Nothing of M2 is built yet.** The user's decision: do **Qdrant** and **dockerize**
  FIRST (before the retrieval-quality A/Bs), because they gate the vLLM GPU rental.
- Two design HTMLs are the orientation docs: `docs/DESIGN.html` (current system — pipeline,
  serving topology, generation path, views, refusal gate, streaming, eval spine, decision
  log D0–D11 with evidence) and `docs/FUTURE_DESIGN.html` (forward design — planned infra,
  serving-at-scale, RAPTOR-rejected, the LLM-Wiki alternative).

## 2. ⚠️ What is NOT in git — transfer these manually (or rebuild)

git clone alone is NOT enough to run. These are gitignored:

| item | why it matters | transfer or rebuild |
|---|---|---|
| `.env` | OPENAI_API_KEY (+ old HANA creds) | **transfer** (never commit). Or mint a new key |
| `/PLAN.md` | the personal 3-phase career plan (root, gitignored) | **transfer** — career-personal, keep out of the repo |
| `ingest/out/chunks.jsonl` + `ingest/out/btp_rag.db` | the corpus chunks + SQLite store **with all 1,216 embeddings** | transfer, or rebuild: `./corpus/fetch.sh && python3 ingest/chunk.py && PYTHONPATH=. python3 ingest/embed.py` (~$0.005, ~10 min) |
| `corpus/raw/` | the © SAP source docs | rebuilt by `./corpus/fetch.sh` |
| `eval/out/results*.jsonl`, `requests.jsonl` | latest local run details (registry + runs/ archives ARE committed) | optional; regenerate with any eval run |
| `human-input/` | screenshots etc. | optional |

**Sanity check after setup** (all should pass before building anything):

```bash
pip install -e ".[dev]"
python3 -m pytest -q                      # 20/20
PYTHONPATH=. python3 eval/score_retrieval.py    # GOLD SET: ✓ valid (48 items, atoms verified)
PYTHONPATH=. python3 core.py "What is the max dimensionality of REAL_VECTOR?"  # cited answer
```

## 3. Next build: M2.4 — Qdrant server-side hybrid + docker compose

All design decisions are ALREADY MADE (BUILD_PLAN M2.4 + review findings + user's
explicit choice: dense + sparse + RRF fusion ALL inside Qdrant; app becomes stateless).
Implementation checklist:

1. **Run Qdrant** (Docker Desktop): pinned image, e.g.
   `docker run -d --name btp-qdrant -p 6333:6333 -v "$PWD/qdrant_data:/qdrant/storage" qdrant/qdrant:v1.15.1`
   `pip install "qdrant-client[fastembed]"` (add to requirements — already listed).
2. **Load script** (`ingest/load_qdrant.py`): read records + embeddings from the SQLite
   store (NO re-embedding — reuse the 1,216 stored vectors); create collection with a
   named dense vector (1536, COSINE, HNSW default) + named sparse vector (IDF modifier);
   sparse vectors client-side via fastembed `Qdrant/bm25`, built from **raw `text`**
   (NOT embed_text — parity first; enriched sparse is the M2.1 A/B, a separate reindex).
   Point ids: `uuid5(NAMESPACE_URL, chunk_id)`; full chunk record in payload
   (all `FIELDS` from `retrieve/store.py`).
3. **`QdrantStore(VectorStore)`** in `retrieve/store.py` + a `hybrid_search()` method:
   one Query API call — prefetch dense + prefetch sparse → `FusionQuery(RRF)`, top-30.
   **Gate wrinkle (review finding 7):** fused RRF scores are NOT cosines and Qdrant
   doesn't expose prefetch sub-scores — batch a dense-only query alongside and read the
   refusal gate's `top_cosine` from it.
4. **`Retriever` branch**: if the store exposes `hybrid_search`, skip app-side
   BM25/RRF entirely (no in-RAM index); keep app-side dedup (sha256 + Jaccard) on the
   fused candidates; `meta` map still built from `store.records()` once at startup.
5. **Parity gate before switching defaults**: `STORE=qdrant PYTHONPATH=. python3
   eval/run_eval.py` → the dev/test rows must match/beat the M1 baseline
   (dev ER@5 0.833 / hit5 0.818 · test ER@5 0.781 / hit5 0.737; negatives 7/7,
   0 test false-refusals). Watch the **identifier-heavy questions** (REAL_VECTOR,
   COSINE_SIMILARITY) — Qdrant's BM25 tokenizer replaces our identifier-preserving one
   (`hybrid.py:22`); if those regress, that's the pre-agreed trigger for pushing a
   custom-tokenized sparse vector instead. Record registry rows; note `store: qdrant`
   lands in the fingerprint automatically.
6. **Dockerize**: `Dockerfile` (python:3.11-slim, install deps, `uvicorn app.main:app`),
   `docker-compose.yml` (services: `app` build + `qdrant` pinned image + volume;
   `depends_on`; app reads `.env`; `QDRANT_URL=http://qdrant:6333` env override in
   config), `.dockerignore` (corpus/raw, ingest/out, .env NEVER baked into the image —
   pass at runtime). Smoke: `docker compose up` → `/health` → one `/ask`.
7. Commit as M2.4a (Qdrant) and M2.4b (docker) with eval evidence in the message.

## 4. Then: vLLM weekend (plan item 1.6) — the reason for the rush

Rent 1 GPU (RunPod/Lambda, ~4090/A10G, ~$10 budget) → vLLM serves Qwen2.5-7B-Instruct
with its OpenAI-compatible endpoint → the entire swap is env vars through the provider
adapter (`OPENAI_BASE_URL=http://<gpu>:8000/v1`, `ANSWER_MODEL=Qwen/...`) → run the SAME
eval → registry row: self-hosted 7B vs gpt-5-mini (correctness / latency / $-per-answer)
→ the API-vs-self-host economics chart. Measure: continuous-batching throughput curve
(N=1/8/32/64), prefix-caching TTFT (shared ANSWER_SYSTEM), TTFT with a non-reasoning
model (thinking time = 0 → TTFT ≈ prefill, expect ~0.3 s).

## 5. After that (M2 quality A/Bs, on the M1 ruler)

M2.1 contextual sparse (build sparse from enriched text — one reindex + eval) →
M2.2 local cross-encoder rerank + parent/adjacency expansion (RERANK_* seam exists;
gate keeps reading DENSE cosine; fixed context budget) → M2.3 doc2context
(retrieval-only staging per VIEW_POLICY; ingest-time entailment validation;
recalibrate SIM_THRESHOLD on dev after any representation change).

## 6. Open threads & known debt

- **PR `sanechips-rag` → `main`**: still open question; recommended (exercises CI
  eval-gates) but user hasn't decided. All new work is on `sanechips-rag`.
- **q47 falsely refused** once at baseline (evidence-dense, dev) — watch, don't fix yet.
- **false-premise** remains the weakest category (corr 0.6 at baseline, n=3).
- **README chart debt**: `docs/latency_accuracy.svg` (mixed ms/s log scale — user
  disliked it) was removed from README §3; replace with linear split-unit panels when
  new latency data exists (post-vLLM is the natural moment).
- **Nightly eval-gates** workflow runs on a path-filtered schedule — after Qdrant lands,
  check it still passes with `STORE=sqlite` default (CI has no Qdrant service).
- **Secrets hygiene**: GitHub PAT embedded in remote URLs (user knows; rotate after
  interviews); OPENAI key in `.env` + GitHub Actions secret; old HANA creds are dead
  with the trial.
- The former teaching HTMLs (`serving_topology`, `serving_streaming_and_load`,
  `llm_wiki_pattern`) and the two prior design HTMLs (`design_system`, `design_decisions`)
  have been consolidated into `docs/DESIGN.html` + `docs/FUTURE_DESIGN.html`; the originals
  now live under `docs/archive/`.

## 7. Working agreements (how this project is run)

- Every quality change is A/B'd on the eval harness against the baseline registry rows;
  dev tunes, test verdicts; paired per-question comparisons.
- Published latency/refusal numbers = medians of ≥5 runs with committed raw artifacts
  (`eval/measurements/`).
- Every result-changing knob goes through `config.py` and into `fingerprint()`.
- Never commit: `.env`, `corpus/raw/`, `ingest/out/*.jsonl|db`, `/PLAN.md`,
  `INTERVIEW_QA.md`, `HARNESS_NOTES.md`, `human-input/`.
- User preferences: explain concepts with analogies before code; repeated concepts
  (async/threads/pools) need re-explanation without friction; measure before claiming.
