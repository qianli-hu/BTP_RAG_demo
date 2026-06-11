"""
retrieve/generate.py — grounded answer generation with the two-layer refusal gate.

  layer 1 (cheap): if the top dense cosine < SIM_THRESHOLD -> refuse with NO LLM call.
  layer 2 (LLM):   the prompt instructs the model to refuse if the answer isn't in context.

Returns {answer, citations, answerable, usage, gated}. Citations are chunk ids.
"""
import config
import prompts
import providers


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
