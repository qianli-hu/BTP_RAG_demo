"""Unit tests for the provenance view invariants (BUILD_PLAN §2.2–2.4).

These are the GOVERNANCE tests: if one fails, generated content is leaking into a view
that must stay authoritative (citation), or the judge has drifted from what the model
actually saw (eval-what-you-serve at the judge level)."""
import config
import views
from views import (GENERATED_LABEL, build_answering_context, build_citation,
                   build_judge_context, build_retrieval_text)


def _node():
    return {"id": "doc-a#abc123def456#00", "doc": "doc-a", "section_path": "Setup › Install",
            "source_url": "https://example.com/doc-a", "text": "The default value is 256.",
            "generated_context": "This passage explains the scheduler's sequence limit.",
            "synthetic_queries": ["What is the default max_num_seqs?"]}


def test_judge_view_is_answering_view_by_reference():
    # one builder, two callers — for ANY node and ANY flag state
    n = _node()
    assert build_judge_context(n) == build_answering_context(n)
    config.ANSWERING_INCLUDES_GENERATED = True
    try:
        assert build_judge_context(n) == build_answering_context(n)
    finally:
        config.ANSWERING_INCLUDES_GENERATED = False
    assert config.VIEW_POLICY["judge"] == "answering"     # policy encodes the reference


def test_synthetic_queries_never_reach_answering_or_judge():
    n = _node()
    for flag in (False, True):
        config.ANSWERING_INCLUDES_GENERATED = flag
        try:
            assert n["synthetic_queries"][0] not in build_answering_context(n)
            assert n["synthetic_queries"][0] not in build_judge_context(n)
        finally:
            config.ANSWERING_INCLUDES_GENERATED = False


def test_generated_context_staged_out_by_default_labeled_when_promoted():
    n = _node()
    out = build_answering_context(n)
    assert n["generated_context"] not in out               # default: retrieval-only staging
    config.ANSWERING_INCLUDES_GENERATED = True
    try:
        out = build_answering_context(n)
        assert n["generated_context"] in out               # promoted: present...
        assert GENERATED_LABEL in out                      # ...but ALWAYS labeled
        assert out.index(GENERATED_LABEL) < out.index(n["generated_context"])
    finally:
        config.ANSWERING_INCLUDES_GENERATED = False


def test_citation_view_never_contains_generated_content():
    n = _node()
    c = build_citation(n)
    blob = " ".join(str(v) for v in c.values())
    assert n["generated_context"] not in blob
    assert n["synthetic_queries"][0] not in blob
    assert c["quoted_text"] == n["text"]                   # authoritative raw text only
    assert c["source_url"] == n["source_url"]


def test_retrieval_view_is_maximally_rich():
    n = _node()
    out = build_retrieval_text(n)
    for expected in (n["text"], n["generated_context"], n["synthetic_queries"][0],
                     "doc-a", "Setup › Install"):
        assert expected in out


def test_answering_format_matches_legacy_when_fields_empty():
    # M1 nodes have no generated fields yet — rendering must equal the pre-views format,
    # so wiring the renderers is a refactor, not a behavior change on today's corpus.
    n = {"id": "d#aaaaaaaaaaaa#01", "doc": "d", "section_path": "S", "text": "T"}
    assert build_answering_context(n) == "[d#aaaaaaaaaaaa#01] (d · S)\nT"
