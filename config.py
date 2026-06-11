"""
Single source of truth for BTP_RAG: models, params, thresholds, connections.
Every value comes from .env (see .env.example) — nothing model/credential-specific is
hardcoded anywhere else. Import this; don't re-read os.environ in feature code.

Run scripts from the repo root with the root on the path, e.g.:
    PYTHONPATH=. python3 ingest/embed.py
"""
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
_int = lambda k, d: int(os.getenv(k, d))
_float = lambda k, d: float(os.getenv(k, d))

# --- storage backend (swap unit: only dense_search differs; see retrieve/store.py) ---
STORE       = os.getenv("STORE", "sqlite")            # sqlite (local dev/demo) | hana (SAP prod)
SQLITE_PATH = os.getenv("SQLITE_PATH", "ingest/out/btp_rag.db")

# --- HANA Cloud ---
SCHEMA, TABLE = "BTP_RAG", "CHUNKS"
def hana_connect():
    """A fresh hdbcli connection (creds read at call time, so importing config never fails)."""
    from hdbcli import dbapi
    return dbapi.connect(
        address=os.environ["HANA_HOST"], port=_int("HANA_PORT", "443"),
        user=os.environ["HANA_USER"], password=os.environ["HANA_PASSWORD"],
        encrypt=True, sslValidateCertificate=True)

# --- providers / models (swappable via the provider adapter) ---
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "openai")   # openai | sap_genaihub | local
LLM_PROVIDER       = os.getenv("LLM_PROVIDER", "openai")
EMBEDDING_MODEL    = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIM      = _int("EMBEDDING_DIM", "1536")
ANSWER_MODEL       = os.getenv("ANSWER_MODEL", "gpt-5-mini")      # hot path (per query)
JUDGE_MODEL        = os.getenv("JUDGE_MODEL", "gpt-5")            # eval only (stronger, low volume)

# --- pricing: $ per 1M tokens (input, output). ESTIMATES — update with current OpenAI prices.
#     Token counts in the request log are EXACT (from usage); only the $ conversion is approximate. ---
PRICING = {
    "text-embedding-3-small": (0.02, 0.0),
    "gpt-5-mini":             (0.25, 2.0),
    "gpt-5":                  (1.25, 10.0),
}
def cost(model, prompt_tokens=0, completion_tokens=0):
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return round((prompt_tokens * pin + completion_tokens * pout) / 1_000_000, 6)

# --- provider resilience (v1-lite: SDK-native retry + timeout) ---
REQUEST_TIMEOUT = _int("REQUEST_TIMEOUT", "30")                  # seconds per call
MAX_RETRIES     = _int("MAX_RETRIES", "4")                       # exponential backoff (SDK)

# --- retrieval knobs ---
TOP_K    = _int("TOP_K", "5")        # chunks handed to the LLM
DENSE_K  = _int("DENSE_K", "30")     # candidates pulled from vector search (store-owned)
SPARSE_K = _int("SPARSE_K", "30")    # candidates pulled from app BM25
RRF_K    = _int("RRF_K", "60")       # Reciprocal Rank Fusion constant
EVAL_CONCURRENCY = _int("EVAL_CONCURRENCY", "8")   # parallel items in the eval loop (I/O-bound)

# --- reranker (v1.5 lever; LOCAL cross-encoder, no API latency — maps to HANA CROSS_ENCODE) ---
RERANK_ENABLED = os.getenv("RERANK_ENABLED", "false").lower() == "true"
RERANK_MODEL   = os.getenv("RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")  # | BAAI/bge-reranker-base
RERANK_TOP_N   = _int("RERANK_TOP_N", "30")   # fused candidates reranked before keeping TOP_K

# --- generation / refusal gate ---
SIM_THRESHOLD = _float("SIM_THRESHOLD", "0.56")   # below top-cosine -> refuse (calibrated on negatives)
NOT_IN_KB     = os.getenv("NOT_IN_KB", "Not in knowledge base.")

# --- experiment fingerprint (ROADMAP §2): the "model" = the whole config surface ---
# Stamp this on every eval run + request-log line so any metric is attributable to an
# exact configuration (reproducibility / A-B / regression / debugging).
def _file_hash(rel):
    p = Path(__file__).resolve().parent / rel
    return hashlib.sha1(p.read_bytes()).hexdigest()[:10] if p.exists() else None

def fingerprint(prompt_version=None):
    return {
        "embed_model": EMBEDDING_MODEL, "answer_model": ANSWER_MODEL, "judge_model": JUDGE_MODEL,
        "dense_k": DENSE_K, "sparse_k": SPARSE_K, "rrf_k": RRF_K, "top_k": TOP_K,
        "rerank": RERANK_MODEL if RERANK_ENABLED else None,
        "sim_threshold": SIM_THRESHOLD,
        "prompt_version": prompt_version,
        "gold_hash": _gold_hash(),                           # which gold set (content-addressed)
        "corpus_hash": _file_hash("corpus/MANIFEST.json"),   # which corpus snapshot (doc shas+versions)
    }

def _gold_hash():
    try:
        from eval.dataset import gold_hash  # lazy: avoids import cycles
        return gold_hash()
    except Exception:
        return None
