#!/usr/bin/env python3
"""
App-layer BM25 (sparse / keyword) search over the chunks stored in HANA Cloud.

HANA Cloud FREE TIER does not support in-DB full-text (CREATE FULLTEXT INDEX) or PAL BM25,
so the sparse half of hybrid retrieval runs HERE in Python (rank_bm25) over the chunk TEXT
pulled from BTP_RAG.CHUNKS. Dense (vector) search stays in HANA (COSINE_SIMILARITY); the
two are fused via RRF in retrieve/hybrid.py. On a PAID HANA instance this can move in-DB.

  pip install hdbcli python-dotenv rank_bm25
  python3 retrieve/bm25_search.py "how do I create a vector index"
"""
import os
import re
import sys

from dotenv import load_dotenv
from hdbcli import dbapi
from rank_bm25 import BM25Okapi

load_dotenv()
SCHEMA, TABLE = "BTP_RAG", "CHUNKS"
tokenize = lambda s: re.findall(r"[a-z0-9_]+", s.lower())   # keeps SQL ids (real_vector) intact

def connect():
    return dbapi.connect(
        address=os.environ["HANA_HOST"], port=int(os.getenv("HANA_PORT", "443")),
        user=os.environ["HANA_USER"], password=os.environ["HANA_PASSWORD"],
        encrypt=True, sslValidateCertificate=True)

def _str(v):                                                # NCLOB may come back as a LOB
    if v is None: return ""
    return v if isinstance(v, str) else (v.read() if hasattr(v, "read") else str(v))

def load_chunks(cur):
    cols = ["id", "doc", "element_type", "section_path", "source_url", "text"]
    cur.execute(f"SELECT {','.join(c.upper() for c in cols)} FROM {SCHEMA}.{TABLE}")
    return [dict(zip(cols, [_str(c) if i == 5 else c for i, c in enumerate(r)], strict=True))
            for r in cur.fetchall()]

def build_bm25(chunks):
    return BM25Okapi([tokenize(c["text"]) for c in chunks])

def sparse_search(query, bm25, chunks, k=10):
    scores = bm25.get_scores(tokenize(query))
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return [(round(float(scores[i]), 3), chunks[i]) for i in order[:k] if scores[i] > 0]

def main():
    query = " ".join(sys.argv[1:]) or "create vector index hnsw"
    conn = connect(); cur = conn.cursor()
    chunks = load_chunks(cur)
    cur.close(); conn.close()
    bm25 = build_bm25(chunks)
    hits = sparse_search(query, bm25, chunks, k=10)
    print(f"BM25 (app-layer) over {len(chunks)} HANA chunks — {query!r}\n")
    for score, c in hits:
        print(f"[{score:6.2f}] {c['doc']} · {c['section_path'][:55]}")
        print(f"          {c['id']}")
        print(f"          {c['text'][:110].strip()}…\n")

if __name__ == "__main__":
    main()
