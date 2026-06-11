# eval/ — datasets, scorers, gates, registry

**Design:** rules judge retrieval (we have gold labels → deterministic, free, exact);
the LLM judges only generation (faithfulness/correctness/relevance — things rules can't read).
Every run is stamped with `config.fingerprint()` (models + retrieval params + prompt_version
+ gold_hash + corpus_hash) and recorded in the registry.

## The gold/adversarial dataset — `gold/` (one file per answer type)
| file | n | tests |
|---|--:|---|
| `factual.jsonl` | 12 | grounded recall |
| `table.jsonl` | 6 | Docling table extraction (metering/pricing) |
| `cross-doc.jsonl` | 5 | multi-doc synthesis (the hard retrieval case) |
| `distractor.jsonl` | 5 | precision under lexical traps |
| `false-premise.jsonl` | 3 | anti-sycophancy (premise must be corrected) |
| `entity-confusion.jsonl` | 2 | product/function disambiguation |
| `negative.jsonl` | 7 | out-of-corpus → must answer exactly `Not in knowledge base.` |

Load via `eval.dataset.load_gold()` (concatenated, sorted by id); `gold_hash` is
content-addressed (stable across file moves, changes when an item changes).
Reporting is per-type + aggregate (see `run_eval.report`).

## Code
- `dataset.py` — gold loader + content hash
- `score_retrieval.py` — gold validator (anchors + expect_terms vs chunks) + deterministic scorers
- `run_eval.py` — full eval-what-you-serve: core.ask + gpt-5 judge → report (~2 min, ~$0.35)
- `baseline.py` — no-RAG ablation (same model, no retrieval) → RAG-vs-no-RAG table
- `gates.py` — CI pass/fail: hit-rate@5 ≥ 70%, refusal separation, no false refusals
- `registry.py` — append-only run ledger

## Artifacts — `out/` (what each file is)
| path | what | committed? |
|---|---|---|
| `../registry.jsonl` | **run ledger**: ts + kind + fingerprint + summary metrics + results_file | ✅ |
| `out/runs/<ts>-<kind>.jsonl` | **immutable per-item results** of each full run (answer, citations, retrieved, judge scores) — the registry's evidence | ✅ |
| `out/results.jsonl` / `results_norag.jsonl` | latest-run convenience copies (overwritten) | ✗ |
| `out/requests.jsonl` | serving request log (one trace per /ask call) | ✗ |
| `out/logs/` | console transcripts of long runs | ✗ |

**Drill-down recipe:** pick a row in `registry.jsonl` → open its `results_file` →
each line = one gold item with the exact answer, citations, retrieved chunks and judge verdict
under that fingerprint.

## Run
```bash
PYTHONPATH=. python3 eval/score_retrieval.py   # validate gold vs current chunks (free)
PYTHONPATH=. python3 eval/gates.py             # deterministic CI gates (~$0.0001)
PYTHONPATH=. python3 eval/run_eval.py          # full eval + judge (~$0.35, ~2 min)
PYTHONPATH=. python3 eval/baseline.py          # no-RAG ablation + comparison table
```
