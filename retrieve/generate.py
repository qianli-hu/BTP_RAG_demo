"""
retrieve/generate.py — grounded answer generation with the two-layer refusal gate.

  layer 1 (cheap): if the top dense cosine < SIM_THRESHOLD -> refuse with NO LLM call.
  layer 2 (LLM):   the prompt instructs the model to refuse if the answer isn't in context.

ONE code path (BUILD_PLAN §2.5): generate_stream() is the canonical generation;
generate() is its buffered wrapper (consumes the stream, returns the final dict) — so
/ask, /ask/stream, and the eval harness all exercise IDENTICAL generation logic, and
"streaming" is purely a transport difference.

generate()        -> {answer, citations, answerable, usage, gated}
generate_stream() -> yields ("token", text)... then ("done", same dict)
Citations are chunk ids.
"""
import re

import config
import prompts
import providers

# a chunk id looks like: sap-hana-vector#f6f95e2fe101#00  (doc#sha1-12hex#2-digit ordinal)
_CITE_RE = re.compile(r"\[([a-z0-9-]+#[0-9a-f]{12}#\d{2})\]")


def _extract_citations(text, hits):
    """Inline [chunk-id]s from the finished text, kept only if actually retrieved
    (the citation-validity rule survives streaming), deduped in order of appearance."""
    retrieved = {h["id"] for h in hits}
    seen, out = set(), []
    for cid in _CITE_RE.findall(text):
        if cid in retrieved and cid not in seen:
            seen.add(cid); out.append(cid)
    return out


def generate(query, hits, top_cosine=None):
    """Buffered wrapper over generate_stream(): same gates, same prompt, same citation
    extraction — just consumed to completion instead of forwarded token-by-token."""
    for kind, payload in generate_stream(query, hits, top_cosine=top_cosine):
        if kind == "done":
            return payload


def generate_stream(query, hits, top_cosine=None):
    """Streaming twin of generate(). Yields each token onward the moment it arrives,
    while keeping a private copy — citations/answerable need the FINISHED text, so they
    travel in one final ("done", {...}) event (same shape as generate()'s return)."""
    # layer-1 refusal: instant, $0, nothing to stream
    if top_cosine is not None and top_cosine < config.SIM_THRESHOLD:
        yield "done", {"answer": config.NOT_IN_KB, "citations": [], "answerable": False,
                       "usage": None, "gated": "score"}
        return

    acc, usage = [], None
    for kind, payload in providers.chat_stream(
            prompts.answer_messages(query, hits[:config.TOP_K]),
            model=config.ANSWER_MODEL,
            reasoning_effort=config.ANSWER_REASONING_EFFORT):
        if kind == "token":
            acc.append(payload)
            yield "token", payload
        else:
            usage = payload
    text = "".join(acc).strip()

    # layer-2 refusal: the model streamed the refusal string
    if text == config.NOT_IN_KB or not text:
        yield "done", {"answer": config.NOT_IN_KB, "citations": [], "answerable": False,
                       "usage": usage, "gated": "llm"}
        return
    yield "done", {"answer": text, "citations": _extract_citations(text, hits),
                   "answerable": True, "usage": usage, "gated": None}
