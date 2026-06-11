#!/usr/bin/env python3
"""
Load ingest/out/chunks.jsonl into SAP HANA Cloud and enable sparse keyword search.

Stores TEXT + metadata now; EMBEDDING is left NULL (filled later by the embed step).
Then creates a FULLTEXT INDEX on TEXT so HANA does keyword/relevance search immediately
(CONTAINS() + SCORE(), TF-IDF). See CORPUS.md §3 and retrieve/bm25_search.py.

Prereqs:
  pip install hdbcli python-dotenv
  .env (gitignored) with:  HANA_HOST, HANA_PORT=443, HANA_USER, HANA_PASSWORD
  HANA Cloud instance RUNNING + "Allow all IP addresses" (or your IP) in HANA Cloud Central.
Run:
  python3 ingest/load_hana.py
"""
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from hdbcli import dbapi

load_dotenv()
ROOT = Path(__file__).resolve().parent.parent
CHUNKS = ROOT / "ingest" / "out" / "chunks.jsonl"
SCHEMA, TABLE = "BTP_RAG", "CHUNKS"
DIM = int(os.getenv("EMBEDDING_DIM", "1536"))

# column order = chunks.jsonl keys (EMBEDDING added later, so not inserted here)
COLS = ["id", "doc", "element_type", "section_path", "section_id", "parent_section_id",
        "prev_id", "next_id", "page", "topic_slug", "source_url", "doc_version",
        "token_count", "content_sha256", "text", "embed_text"]

DDL = f"""CREATE COLUMN TABLE {SCHEMA}.{TABLE} (
  ID NVARCHAR(256) PRIMARY KEY,
  DOC NVARCHAR(32),
  ELEMENT_TYPE NVARCHAR(16),
  SECTION_PATH NVARCHAR(512),
  SECTION_ID NVARCHAR(32),
  PARENT_SECTION_ID NVARCHAR(32),
  PREV_ID NVARCHAR(256),
  NEXT_ID NVARCHAR(256),
  PAGE INTEGER,
  TOPIC_SLUG NVARCHAR(256),
  SOURCE_URL NVARCHAR(512),
  DOC_VERSION NVARCHAR(32),
  TOKEN_COUNT INTEGER,
  CONTENT_SHA256 NVARCHAR(64),
  TEXT NCLOB,
  EMBED_TEXT NCLOB,
  EMBEDDING REAL_VECTOR({DIM})           -- NULL until the embed step; REAL_VECTOR needs a current HANA Cloud
)"""

def connect():
    return dbapi.connect(
        address=os.environ["HANA_HOST"], port=int(os.getenv("HANA_PORT", "443")),
        user=os.environ["HANA_USER"], password=os.environ["HANA_PASSWORD"],
        encrypt=True, sslValidateCertificate=True)        # HANA Cloud = always TLS on 443

def scalar(cur, sql, args=None):
    cur.execute(sql) if not args else cur.execute(sql, args)   # hdbcli: empty () params -> -10603
    return cur.fetchone()[0]

def main():
    chunks = [json.loads(l) for l in CHUNKS.read_text().splitlines() if l.strip()]
    print(f"loading {len(chunks)} chunks -> {SCHEMA}.{TABLE}")
    conn = connect(); cur = conn.cursor()

    if scalar(cur, "SELECT COUNT(*) FROM SYS.SCHEMAS WHERE SCHEMA_NAME=?", (SCHEMA,)) == 0:
        cur.execute(f"CREATE SCHEMA {SCHEMA}")
    if scalar(cur, "SELECT COUNT(*) FROM SYS.TABLES WHERE SCHEMA_NAME=? AND TABLE_NAME=?",
              (SCHEMA, TABLE)):
        cur.execute(f"DROP TABLE {SCHEMA}.{TABLE}")          # idempotent reload
    cur.execute(DDL)

    ins = (f"INSERT INTO {SCHEMA}.{TABLE} ({','.join(c.upper() for c in COLS)}) "
           f"VALUES ({','.join(['?'] * len(COLS))})")
    rows = [[c.get(k) for k in COLS] for c in chunks]
    for i in range(0, len(rows), 1000):                     # batch insert
        cur.executemany(ins, rows[i:i + 1000])
    conn.commit()
    print(f"inserted {scalar(cur, f'SELECT COUNT(*) FROM {SCHEMA}.{TABLE}')} rows")

    # sparse search: full-text index enables CONTAINS()/SCORE() keyword ranking.
    # NOTE: HANA Cloud FREE TIER does not support CREATE FULLTEXT INDEX -> handled below.
    fts_ok = True
    try:
        cur.execute(f"CREATE FULLTEXT INDEX FTI_{TABLE}_TEXT ON {SCHEMA}.{TABLE}(TEXT) "
                    f"TEXT ANALYSIS ON")                     # linguistic (stemming) keyword search
        print("created FULLTEXT INDEX FTI_CHUNKS_TEXT (TEXT ANALYSIS ON)")
    except dbapi.Error as e:
        msg = str(e).lower()
        if "exists" in msg:
            print("FULLTEXT index already present")
        elif "not supported" in msg:
            fts_ok = False
            print("NOTE: CREATE FULLTEXT INDEX is not supported on this instance (HANA Cloud free tier).")
            print("      -> chunks are loaded; native CONTAINS()/SCORE() sparse search is unavailable here.")
            print("      Run BM25 in the app layer (rank_bm25), or use a paid instance for in-DB full-text.")
        else:
            raise

    if fts_ok:
        cur.execute(f"""SELECT TOP 5 ID, ROUND(SCORE(),3) AS S, SUBSTRING(TEXT,1,70)
                        FROM {SCHEMA}.{TABLE}
                        WHERE CONTAINS(TEXT, ?, LINGUISTIC) ORDER BY S DESC""",
                    ("cosine similarity vector search",))
        print("\nsparse search smoke test — 'cosine similarity vector search':")
        for r in cur.fetchall():
            print(f"  {r[1]:>5}  {r[0][:40]:40s}  {r[2]}")
    print(f"\nDONE: {scalar(cur, f'SELECT COUNT(*) FROM {SCHEMA}.{TABLE}')} chunks in {SCHEMA}.{TABLE} "
          f"(embedding NULL until embed step).")
    cur.close(); conn.close()

if __name__ == "__main__":
    main()
