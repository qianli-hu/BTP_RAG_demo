#!/usr/bin/env python3
"""
retrieve/hybrid.py — store-agnostic hybrid retrieval (ROADMAP §3).

  embed query (providers) -> store.dense_search (STORE-owned) ⊕ app BM25 (FIXED)
  -> RRF fuse -> dedup -> top-k.

`filters` (v1 metadata filtering) flows to both halves and is also the v2 RBAC/recency hook.
The BM25 index + metadata map are built once from store.records() (same code for any backend).

Run:  PYTHONPATH=. python3 retrieve/hybrid.py "how do I create a vector index"
"""
import re
import sys

from rank_bm25 import BM25Okapi

import config
import providers
from retrieve.store import get_store, match_filter

tokenize = lambda s: re.findall(r"[a-z0-9_]+", s.lower())   # keeps SQL ids (real_vector) intact

def rrf(ranked_lists, K):
    """Reciprocal Rank Fusion over ranked [(id, score)] lists -> [(id, rrf_score)]."""
    fused = {}
    for lst in ranked_lists:
        for rank, (cid, _) in enumerate(lst):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (K + rank + 1)
    return sorted(fused.items(), key=lambda x: -x[1])

def dedup(fused, meta, jaccard=0.8):
    """Drop exact (content_sha256) + near-dup (token Jaccard) chunks, keep higher-ranked."""
    kept, seen_hash, seen_tok = [], set(), []
    for cid, s in fused:
        h = meta[cid]["content_sha256"]
        if h in seen_hash:
            continue
        toks = set(tokenize(meta[cid]["text"]))
        if any(len(toks & t) / (len(toks | t) or 1) > jaccard for t in seen_tok):
            continue
        kept.append((cid, s)); seen_hash.add(h); seen_tok.append(toks)
    return kept


class Retriever:
    def __init__(self, store=None):
        self.store = store or get_store()
        recs = self.store.records()
        self.ids = [r["id"] for r in recs]
        self.meta = {r["id"]: r for r in recs}
        self.bm25 = BM25Okapi([tokenize(r["text"]) for r in recs])
        try:                                   # warm the store's dense cache so parallel
            self.store.dense_search([0.0] * config.EMBEDDING_DIM, 1)   # eval threads don't race on first load
        except Exception:
            pass

    def _sparse(self, query, k, filters):
        scores = self.bm25.get_scores(tokenize(query))
        out = []
        for i in sorted(range(len(scores)), key=lambda j: scores[j], reverse=True):
            if scores[i] <= 0:
                break
            cid = self.ids[i]
            if filters and not match_filter(self.meta[cid], filters):
                continue
            out.append((cid, float(scores[i])))
            if len(out) >= k:
                break
        return out

    def retrieve(self, query, k=None, filters=None):
        k = k or config.TOP_K
        qvec = providers.embed([query])[0]
        dense = self.store.dense_search(qvec, config.DENSE_K, filters)   # store-owned
        sparse = self._sparse(query, config.SPARSE_K, filters)          # fixed app BM25
        fused = dedup(rrf([dense, sparse], config.RRF_K), self.meta)[:k]
        hits = [{"id": cid, "score": round(s, 5), **self.meta[cid]} for cid, s in fused]
        return hits, {"dense": dense, "sparse": sparse}


def main():
    query = " ".join(sys.argv[1:]) or "how do I create a vector index"
    hits, _ = Retriever().retrieve(query, k=8)
    print(f"store={config.STORE} | {query!r}\n")
    for h in hits:
        print(f"[{h['score']:.4f}] {h['doc']} · {h['section_path'][:55]}")
        print(f"          {h['id']}  {h['text'][:95].strip()}…\n")

if __name__ == "__main__":
    main()
