"""
retrieve/generate.py — grounded answer generation with the two-layer refusal gate.

  layer 1 (cheap): if the top dense cosine < SIM_THRESHOLD -> refuse with NO LLM call.
  layer 2 (LLM):   the prompt instructs the model to refuse if the answer isn't in context.

generate()        -> {answer, citations, answerable, usage, gated}  (one JSON parcel)
generate_stream() -> yields ("token", text)... then ("done", same dict)  (SSE path)
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
    # layer-1 refusal: best chunk below the calibrated threshold -> out-of-KB, skip the LLM
    if top_cosine is not None and top_cosine < config.SIM_THRESHOLD:
        return {"answer": config.NOT_IN_KB, "citations": [], "answerable": False,
                "usage": None, "gated": "score"}

    text, usage = providers.chat(prompts.answer_messages(query, hits[:config.TOP_K]),
                                 model=config.ANSWER_MODEL)
    d = prompts.parse_json(text) or {}
    answer = (d.get("answer") or text).strip()
    citations = d.get("citations") or []
    answerable = d.get("answerable", True)

    # layer-2 refusal: model said not-answerable, or emitted the refusal string
    if not answerable or answer == config.NOT_IN_KB:
        return {"answer": config.NOT_IN_KB, "citations": [], "answerable": False,
                "usage": usage, "gated": "llm"}
    return {"answer": answer, "citations": citations, "answerable": True,
            "usage": usage, "gated": None}


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
            prompts.answer_stream_messages(query, hits[:config.TOP_K]),
            model=config.ANSWER_MODEL):
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
