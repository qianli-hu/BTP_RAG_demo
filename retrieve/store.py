"""
retrieve/store.py — the ONLY storage-backend-specific layer (ROADMAP §4).

Ownership:
  FIXED, app-owned (identical across backends): embedding (providers), BM25 sparse
    (rank_bm25 over store.records()), RRF fusion, dedup, refusal, generation, eval.
  STORE-owned (the genuinely backend-specific swap unit): persistence + dense_search.
    HanaStore.dense_search -> in-DB COSINE_SIMILARITY [+HNSW]; SqliteStore.dense_search
    -> numpy cosine over cached vectors. Pick via config.STORE.

To add a backend (pgvector, Qdrant, ...), implement this one interface.
"""
import json
import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np

import config

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["id", "doc", "element_type", "section_path", "section_id", "parent_section_id",
          "prev_id", "next_id", "page", "topic_slug", "source_url", "doc_version",
          "token_count", "content_sha256", "text", "embed_text"]
def _str(v):
    if v is None: return ""
    return v if isinstance(v, str) else (v.read() if hasattr(v, "read") else str(v))

def match_filter(meta, filters):
    """Equality / set-membership metadata filter (shared by every backend + BM25)."""
    if not filters:
        return True
    for k, v in filters.items():
        mv = meta.get(k)
        if isinstance(v, (list, tuple, set)):
            if mv not in v: return False
        elif mv != v:
            return False
    return True


class VectorStore(ABC):
    @abstractmethod
    def ensure_chunks(self, chunks_path): ...        # idempotent load of chunks.jsonl -> int count
    @abstractmethod
    def pending_embeddings(self): ...                # [(id, embed_text)] missing a vector
    @abstractmethod
    def set_embeddings(self, items): ...             # [(id, list[float])]
    @abstractmethod
    def records(self): ...                           # [{**FIELDS}] (no vector) for BM25 + filters + assembly
    @abstractmethod
    def dense_search(self, qvec, k, filters=None): ...  # STORE-OWNED -> [(id, cosine_score)]
    @abstractmethod
    def get(self, ids): ...                          # {id: record}
    @abstractmethod
    def count_embedded(self): ...                    # (embedded, total)


def get_store():
    return {"sqlite": SqliteStore, "hana": HanaStore}[config.STORE]()


# ============================================================ SQLite (local dev/demo)
class SqliteStore(VectorStore):
    def __init__(self):
        self.path = ROOT / config.SQLITE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY, doc TEXT, element_type TEXT, section_path TEXT,
            section_id TEXT, parent_section_id TEXT, prev_id TEXT, next_id TEXT,
            page INTEGER, topic_slug TEXT, source_url TEXT, doc_version TEXT,
            token_count INTEGER, content_sha256 TEXT, text TEXT, embed_text TEXT,
            embedding BLOB)""")
        self.db.commit()
        self._vids = self._vmat = self._meta = None     # caches

    def ensure_chunks(self, chunks_path):
        n = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        if n:
            return n
        rows = [json.loads(l) for l in Path(chunks_path).read_text().splitlines() if l.strip()]
        self.db.executemany(
            f"INSERT INTO chunks ({','.join(FIELDS)}) VALUES ({','.join('?' * len(FIELDS))})",
            [[r.get(f) for f in FIELDS] for r in rows])
        self.db.commit()
        return len(rows)

    def pending_embeddings(self):
        return [(r[0], r[1]) for r in
                self.db.execute("SELECT id, embed_text FROM chunks WHERE embedding IS NULL").fetchall()]

    def set_embeddings(self, items):
        self.db.executemany("UPDATE chunks SET embedding=? WHERE id=?",
                            [(np.asarray(v, dtype=np.float32).tobytes(), cid) for cid, v in items])
        self.db.commit()
        self._vids = self._vmat = None

    def records(self):
        return [dict(zip(FIELDS, row, strict=True))
                for row in self.db.execute(f"SELECT {','.join(FIELDS)} FROM chunks").fetchall()]

    def _vectors(self):
        if self._vmat is None:
            rows = self.db.execute("SELECT id, embedding FROM chunks WHERE embedding IS NOT NULL").fetchall()
            self._vids = [r[0] for r in rows]
            M = (np.stack([np.frombuffer(r[1], dtype=np.float32) for r in rows])
                 if rows else np.zeros((0, config.EMBEDDING_DIM), np.float32))
            norms = np.linalg.norm(M, axis=1, keepdims=True); norms[norms == 0] = 1.0
            self._vmat = M / norms
        return self._vids, self._vmat

    def _metamap(self):
        if self._meta is None:
            self._meta = {r["id"]: r for r in self.records()}
        return self._meta

    def dense_search(self, qvec, k, filters=None):
        vids, M = self._vectors()
        if not vids:
            return []
        q = np.asarray(qvec, dtype=np.float32); q = q / (np.linalg.norm(q) or 1.0)
        sims = M @ q
        meta = self._metamap() if filters else None
        out = []
        for i in np.argsort(-sims):
            cid = vids[i]
            if filters and not match_filter(meta[cid], filters):
                continue
            out.append((cid, float(sims[i])))
            if len(out) >= k:
                break
        return out

    def get(self, ids):
        ids = list(ids)
        if not ids:
            return {}
        q = f"SELECT {','.join(FIELDS)} FROM chunks WHERE id IN ({','.join('?' * len(ids))})"
        return {row[0]: dict(zip(FIELDS, row, strict=True)) for row in self.db.execute(q, ids).fetchall()}

    def count_embedded(self):
        e = self.db.execute("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL").fetchone()[0]
        t = self.db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        return e, t


# ============================================================ HANA (SAP production)
class HanaStore(VectorStore):
    def __init__(self):
        self.conn = config.hana_connect(); self.cur = self.conn.cursor()
        self.S, self.T = config.SCHEMA, config.TABLE

    def ensure_chunks(self, chunks_path):
        return self.cur.execute(f"SELECT COUNT(*) FROM {self.S}.{self.T}") or \
               self.cur.fetchone()[0]     # chunks are loaded by ingest/load_hana.py

    def pending_embeddings(self):
        self.cur.execute(f"SELECT ID, EMBED_TEXT FROM {self.S}.{self.T} WHERE EMBEDDING IS NULL")
        return [(r[0], _str(r[1])) for r in self.cur.fetchall()]

    def set_embeddings(self, items):
        lit = lambda v: "[" + ",".join(f"{x:.7g}" for x in v) + "]"
        self.cur.executemany(
            f"UPDATE {self.S}.{self.T} SET EMBEDDING=TO_REAL_VECTOR(?) WHERE ID=?",
            [(lit(v), cid) for cid, v in items])
        self.conn.commit()

    def records(self):
        self.cur.execute(f"SELECT {','.join(f.upper() for f in FIELDS)} FROM {self.S}.{self.T}")
        return [dict(zip(FIELDS, [_str(c) for c in row], strict=True)) for row in self.cur.fetchall()]

    def _where(self, filters):
        # parameterized equality filter; values bound by caller
        if not filters:
            return "", []
        clauses, vals = [], []
        for k, v in filters.items():
            if isinstance(v, (list, tuple, set)):
                clauses.append(f"{k.upper()} IN ({','.join('?' * len(v))})"); vals += list(v)
            else:
                clauses.append(f"{k.upper()}=?"); vals.append(v)
        return " AND " + " AND ".join(clauses), vals

    def dense_search(self, qvec, k, filters=None):
        wsql, wvals = self._where(filters)
        vec = "[" + ",".join(f"{x:.7g}" for x in qvec) + "]"
        self.cur.execute(
            f"SELECT TOP {int(k)} ID, COSINE_SIMILARITY(EMBEDDING, TO_REAL_VECTOR(?)) AS S "
            f"FROM {self.S}.{self.T} WHERE EMBEDDING IS NOT NULL{wsql} ORDER BY S DESC",
            [vec] + wvals)
        return [(r[0], float(r[1])) for r in self.cur.fetchall()]

    def get(self, ids):
        ids = list(ids)
        if not ids:
            return {}
        self.cur.execute(
            f"SELECT {','.join(f.upper() for f in FIELDS)} FROM {self.S}.{self.T} "
            f"WHERE ID IN ({','.join('?' * len(ids))})", ids)
        return {row[0]: dict(zip(FIELDS, [_str(c) for c in row], strict=True)) for row in self.cur.fetchall()}

    def count_embedded(self):
        self.cur.execute(f"SELECT SUM(CASE WHEN EMBEDDING IS NOT NULL THEN 1 ELSE 0 END), "
                         f"COUNT(*) FROM {self.S}.{self.T}")
        return tuple(self.cur.fetchone())
