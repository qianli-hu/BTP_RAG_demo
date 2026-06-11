"""Unit tests for the deterministic retrieval machinery (no network, no corpus, no secrets)."""
import config
from retrieve.hybrid import dedup, rrf, tokenize
from retrieve.store import match_filter


def test_rrf_fuses_by_rank_not_score():
    dense = [("a", 0.99), ("b", 0.55), ("c", 0.54)]
    sparse = [("c", 12.0), ("a", 3.0)]           # wildly different score scale — must not matter
    fused = rrf([dense, sparse], K=60)
    ids = [cid for cid, _ in fused]
    assert ids[0] == "a"                          # rank 1 + rank 2
    assert set(ids) == {"a", "b", "c"}
    assert all(s > 0 for _, s in fused)


def test_rrf_single_list_preserves_order():
    fused = rrf([[("x", 1.0), ("y", 0.5)]], K=60)
    assert [c for c, _ in fused] == ["x", "y"]


def _meta(**texts):
    return {k: {"content_sha256": f"h_{v}", "text": v} for k, v in texts.items()}


def test_dedup_drops_exact_duplicates():
    meta = _meta(a="alpha beta gamma", b="alpha beta gamma")
    meta["b"]["content_sha256"] = meta["a"]["content_sha256"]      # same hash = exact dup
    kept = dedup([("a", 0.9), ("b", 0.8)], meta)
    assert [c for c, _ in kept] == ["a"]


def test_dedup_drops_near_duplicates_keeps_distinct():
    base = "the hnsw index speeds up approximate nearest neighbor vector search in hana cloud"
    meta = _meta(a=base, b=base + " extra", c="completely different topic about metering tokens")
    kept = dedup([("a", 0.9), ("b", 0.8), ("c", 0.7)], meta, jaccard=0.8)
    assert [c for c, _ in kept] == ["a", "c"]     # b is a near-dup of a; c survives


def test_tokenize_preserves_sql_identifiers():
    assert "real_vector" in tokenize("REAL_VECTOR columns")
    assert "cosine_similarity" in tokenize("use COSINE_SIMILARITY(v1, v2)")


def test_match_filter_equality_and_membership():
    meta = {"doc": "sap-hana-vector", "element_type": "table"}
    assert match_filter(meta, None)
    assert match_filter(meta, {"doc": "sap-hana-vector"})
    assert not match_filter(meta, {"doc": "sap-ai-core"})
    assert match_filter(meta, {"doc": ["sap-ai-core", "sap-hana-vector"]})
    assert not match_filter(meta, {"doc": [], "element_type": "table"})


def test_fingerprint_carries_the_config_surface():
    fp = config.fingerprint(prompt_version="test")
    for key in ("embed_model", "answer_model", "judge_model", "top_k", "sim_threshold",
                "prompt_version", "gold_hash"):
        assert key in fp
    assert fp["prompt_version"] == "test"
