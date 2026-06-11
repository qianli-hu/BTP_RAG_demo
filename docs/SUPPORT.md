# BTP_RAG — Support Reference (ops, data, runbook)

*Deep reference behind [DESIGN.md](DESIGN.md). For full decision history see [archive/](archive/).*

## Corpus (3 docs, pinned in `corpus/MANIFEST.json`)
| doc id | source | format | pages | chunks |
|---|---|---|--:|--:|
| `sap-ai-core` | SAP AI Core guide (incl. Gen AI Hub), 2026-06-04 | PDF | 190 | 406 |
| `sap-hana-vector` | HANA Cloud Vector Engine Guide, 2026_1_QRC | HTML ×51 (crawled via SAP Help JSON API) | 51 | 192 |
| `sap-ai-launchpad` | SAP AI Launchpad guide, 2026-06-04 | PDF | 374 | 618 |
| **total** | | | **615** | **1,216** |

Raw files are **gitignored** (© SAP); `./corpus/fetch.sh` reproduces them. Integrity:
per-doc sha256 (MANIFEST) → per-page sha256 (`_pages.jsonl`) → per-chunk `content_sha256`.

## Chunk schema (one JSONL row → one store row)
`id` (`doc#section_id#ordinal`) · `doc` · `element_type` (prose|table) · `section_path` ·
`section_id` · `parent_section_id` (nearest ancestor section with chunks — small-to-big hook) ·
`prev_id`/`next_id` (reading order) · `page` · `topic_slug` · `source_url` · `doc_version` ·
`token_count` · `content_sha256` · `text` (verbatim, BM25 + citation) · `embed_text`
(breadcrumb prefix + text — the vector input) · `embedding` (1536-d, store side).
Params: target 300 tok, max 512 (tables exempt), min 25, 1-sentence overlap.
Full rationale: [archive/CHUNKING_DESIGN.md](archive/CHUNKING_DESIGN.md).

## HANA Cloud runbook (free tier — `hana-free`, BTP trial)
- **Provision:** subaccount → Entitlements (add SAP HANA Cloud: `hana-free` + `tools`) →
  Service Marketplace → create instance (set `systempassword`, `whitelistIPs: ["0.0.0.0/0"]`)
  → subscribe `tools` → assign `SAP HANA Cloud Administrator` role → re-login.
- **Daily:** instance **auto-stops nightly** → start it in HANA Cloud Central (~3 min).
  **Deleted after 30 idle days.** Free: 16 GB / 80 GB / 1 vCPU, 0 CU cost.
- **Verified capabilities:** `REAL_VECTOR` ✅ `COSINE_SIMILARITY` ✅ `CREATE HNSW VECTOR INDEX` ✅
  — `CREATE FULLTEXT INDEX` ❌, PAL BM25 ❌ (→ sparse runs app-layer; in-DB on paid).
- **Load path:** `ingest/load_hana.py` (chunks) → `STORE=hana python3 ingest/embed.py` (vectors)
  → `BTP_RAG.CHUNKS` table.
- **Common error:** `Socket closed by peer` = instance asleep → start it, retry.

## Local store
SQLite at `ingest/out/btp_rag.db` (`config.SQLITE_PATH`). Access: `sqlite3` CLI for ad-hoc SQL,
or `retrieve.store.get_store()` (the supported API, identical interface to HANA).

## Secrets & config
All config via `.env` → `config.py` (12-factor; prod injects the same names via k8s
Secrets / CF `VCAP_SERVICES`). Never committed: `.env`, `corpus/raw/`, `ingest/out/*.jsonl`,
`eval/out/logs|requests|results*`. **Rotate `OPENAI_API_KEY` + HANA password before publishing.**
Pricing table (`config.PRICING`) is estimates — token counts in logs are exact.

## Eval operations
See [eval/README.md](../eval/README.md): dataset layout (one file per answer type),
artifact map (registry → per-run archives → drill-down recipe), and run commands.

## Drift triage (nightly eval rebuilds from live SAP docs)
| corpus hash diff | gates | meaning | action |
|---|---|---|---|
| unchanged | 🔴 | our regression | fix code/config |
| changed | 🔴 | drift that matters or stale gold | diff changed pages; update gold; re-run |
| changed | 🟢 | benign drift (w.r.t. gold coverage) | optionally adopt via gated refresh |
**Adopting** new docs = re-ingest → re-embed → full eval → only then promote; gold is
re-validated against the new chunks and versioned with it (`gold_hash` + `corpus_hash`).

## Known limitations (v1, by choice)
Query rewrite & reranker not wired (config seams exist; evidence said not needed yet / queued) ·
judge is single-model holistic (panel/per-claim = v2) · drift visibility bounded by gold
coverage · HANA path loaded but the dense-search leg awaits a verification pass.
