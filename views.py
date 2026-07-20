"""
views.py — the four provenance-governed renderings of a retrieved node (BUILD_PLAN §2.2).

One stored node -> four query-time views, each seeing only what config.VIEW_POLICY
allows. The discriminator is always PROVENANCE:

  retrieval — the string that gets embedded / sparse-indexed. Maximally rich: structural
              context + generated enrichment + synthetic queries + raw text. (Today's
              stored embed_text was built by ingest in this shape; M2 re-indexing
              switches ingest to THIS function so there is one source of truth.)
  answering — the context block the answer LLM sees: authoritative raw text +
              deterministic source context. generated_context appears ONLY when
              config.ANSWERING_INCLUDES_GENERATED is True, and then in a labeled
              "not citable evidence" block. synthetic_queries NEVER appear (no code
              path exists — enforced by tests).
  citation  — what a citation resolves to: authoritative source identity only.
  judge     — EXACTLY the answering view, by reference (one builder, two callers):
              the judge scores what the model saw, and the two cannot drift.

Node fields consumed (M1: generated_context / synthetic_queries are empty until M2.3):
  text (raw_text) · doc / section_path / source_url / page (source_context) ·
  generated_context · synthetic_queries
"""
import config

GENERATED_LABEL = "[GENERATED CONTEXT — indexing metadata, NOT citable evidence]"


def _policy(view):
    p = config.VIEW_POLICY[view]
    return config.VIEW_POLICY[p] if isinstance(p, str) else p   # "judge" -> "answering"


def build_retrieval_text(node):
    """What gets embedded + BM25-indexed for this node (M2's single indexing source)."""
    f = _policy("retrieval")
    parts = []
    if "source_context" in f:
        head = " › ".join(x for x in (node.get("doc"), node.get("section_path")) if x)
        if head:
            parts.append(head)
    if "generated_context" in f and node.get("generated_context"):
        parts.append(node["generated_context"])
    if "synthetic_queries" in f and node.get("synthetic_queries"):
        parts.append("Likely questions: " + " ".join(node["synthetic_queries"]))
    if "raw_text" in f:
        parts.append(node.get("text", ""))
    return "\n\n".join(p for p in parts if p)


def build_answering_context(node):
    """One node's block in the answer LLM's CONTEXT (and, by reference, the judge's)."""
    f = _policy("answering")
    head = f"[{node['id']}]"
    if "source_context" in f:
        head += f" ({node.get('doc', '?')} · {node.get('section_path', '?')})"
    generated = ""
    if config.ANSWERING_INCLUDES_GENERATED and node.get("generated_context"):
        generated = f"{GENERATED_LABEL}\n{node['generated_context']}\n"
    return f"{head}\n{generated}{node.get('text', '')}"


def build_judge_context(node):
    """By reference (VIEW_POLICY['judge'] == 'answering'): the judge sees EXACTLY what
    the answering model saw. Never render the judge's context any other way."""
    return build_answering_context(node)


def build_citation(node):
    """Authoritative source identity only — never generated text (no laundering)."""
    return {"id": node["id"], "doc": node.get("doc"), "section_path": node.get("section_path"),
            "source_url": node.get("source_url"), "quoted_text": node.get("text")}
