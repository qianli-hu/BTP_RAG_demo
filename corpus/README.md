# corpus/ — secured raw source files + retrieval

Canonical home for the BTP_RAG corpus (defined in [`../CORPUS.md`](../docs/archive/CORPUS.md)).
Raw files under `raw/` are **gitignored** (SAP © content); everything here is
reproducible from the scripts below.

## Layout
```
corpus/
├── fetch.sh               # one command: secure all 3 docs + rebuild manifest
├── crawl_hana_vector.py   # doc 2 crawler (HTML-only guide, via SAP Help JSON API)
├── build_manifest.py      # integrity + sizing record -> MANIFEST.json
├── MANIFEST.json          # COMMITTED: per-doc bytes/sha256/pages/tokens/est_chunks
└── raw/                   # GITIGNORED — the secured source files:
    ├── sap-ai-core.pdf
    ├── sap-ai-launchpad.pdf
    └── sap-hana-vector/
        ├── _deliverable.json   # deliverable id/build/version + run summary
        ├── _pages.jsonl        # per-page provenance: loio, slug, source_url, title, sha, sizes
        └── NNNN-<slug>.html    # raw citable HTML body per topic, in TOC order
```

## (Re)build the corpus
```bash
./corpus/fetch.sh          # downloads 2 PDFs, crawls doc 2, writes MANIFEST.json
```
Or piecemeal:
```bash
python3 corpus/crawl_hana_vector.py   # doc 2 only
python3 corpus/build_manifest.py      # refresh MANIFEST.json from raw/
```

## What's in the corpus
| doc | format | source | pages | ~tokens | ~chunks |
|-----|--------|--------|------:|--------:|--------:|
| `sap-ai-core` | PDF | [SAP AI Core](https://help.sap.com/docs/sap-ai-core) (2026-06-04) | 190 | 74k | 471 *(measured)* |
| `sap-hana-vector` | HTML×51 | [HANA Vector Engine Guide](https://help.sap.com/docs/hana-cloud-database/sap-hana-cloud-sap-hana-database-vector-engine-guide/introduction) (2026_1_QRC) | 51 | 25k | ~173 |
| `sap-ai-launchpad` | PDF | [SAP AI Launchpad](https://help.sap.com/docs/ai-launchpad) (2026-06-04) | 374 | 124k | ~849 |
| **total** | | | **615** | **224k** | **~1,500** |

Chunk counts are token-based estimates (`tokens / 146`, the avg chunk size measured by
the `chunking/` prototype); `sap-ai-core` is the firm 471 from that prototype. The exact
total lands when the unified `ingest/` chunker runs on all three.

## Note on doc 2 (the HTML crawler)
The Vector Engine Guide has no combined PDF — it's served by the new SAP Help Portal SPA.
`crawl_hana_vector.py` calls the same JSON endpoints the SPA uses:
`deliverableMetadata` (→ deliverable id, build, and the 55-topic TOC) then `pagecontent`
(→ each topic's HTML `body`). It resolves everything from the human-readable slug, so it
stays correct across SAP build bumps. 4 of 55 TOC nodes are section containers with no
backing page and are skipped → **51 content pages**.
