"""Unit tests for the eval gates + gold-set invariants (no network, no corpus, no secrets)."""

import config
from eval.score_retrieval import ADVERSARIAL, NOT_IN_KB, citation_gate, hit_rate_at_k, matches
from retrieve.generate import generate


def _gold():
    from eval.dataset import load_gold
    return load_gold()


# ---- gold-set invariants (the dataset itself is an artifact under test) ----
def test_gold_shape_and_negatives():
    gold = _gold()
    assert len(gold) == 40
    negs = [g for g in gold if g["answer_type"] == "negative"]
    assert len(negs) == 7
    for g in negs:                                  # negatives must demand the EXACT refusal
        assert g["expected"] == NOT_IN_KB
        assert g.get("gold_doc") is None
    adv = [g for g in gold if g["answer_type"] in ADVERSARIAL]
    assert len(adv) == 17                           # 7 negatives + 10 answerable traps
    for g in gold:                                  # every positive carries an anchor
        if g["answer_type"] != "negative":
            assert g.get("gold_doc") and g.get("gold_section_path")


# ---- gate logic on synthetic results ----
def _item():
    return {"gold_doc": "d1", "gold_section_path": "Target Section", "answer_type": "factual", "id": "g1"}


def test_matches_is_doc_scoped_section_substring():
    it = _item()
    assert matches({"doc": "d1", "section_path": "Intro › Target Section › Detail"}, it)
    assert not matches({"doc": "d2", "section_path": "Target Section"}, it)   # wrong doc
    assert not matches({"doc": "d1", "section_path": "Other Section"}, it)


def test_hit_rate_at_k_counts_top_k_only():
    gold = [_item()]
    wrong = {"doc": "d1", "section_path": "Other"}
    right = {"doc": "d1", "section_path": "Target Section"}
    res = {"g1": {"retrieved": [wrong, wrong, right]}}
    assert hit_rate_at_k(gold, res, k=1) == (0, 1)
    assert hit_rate_at_k(gold, res, k=3) == (1, 1)


def test_citation_gate_requires_exists_and_retrieved():
    gold = [_item()]
    corpus_ids = {"c1", "c2"}
    ok = {"g1": {"citations": ["c1"], "retrieved": [{"id": "c1"}]}}
    not_retrieved = {"g1": {"citations": ["c2"], "retrieved": [{"id": "c1"}]}}
    not_in_corpus = {"g1": {"citations": ["zz"], "retrieved": [{"id": "zz"}]}}
    assert citation_gate(gold, ok, corpus_ids) == (1, 1)
    assert citation_gate(gold, not_retrieved, corpus_ids) == (0, 1)
    assert citation_gate(gold, not_in_corpus, corpus_ids) == (0, 1)


# ---- refusal gate (layer 1 is pure logic: below threshold -> refuse, NO LLM call) ----
def test_score_gate_refuses_below_threshold_without_llm():
    out = generate("anything", hits=[], top_cosine=config.SIM_THRESHOLD - 0.01)
    assert out["answer"] == NOT_IN_KB
    assert out["answerable"] is False
    assert out["gated"] == "score"
    assert out["usage"] is None                     # proves no LLM was called


def test_sim_threshold_is_calibrated_between_neg_and_pos():
    # measured on the gold set: max negative top-cosine 0.550, min positive 0.572
    assert 0.550 < config.SIM_THRESHOLD < 0.572
