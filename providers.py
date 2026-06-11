"""
Provider adapter: embeddings + chat behind one interface, with retry + timeout.

v1 = OpenAI. To add SAP Generative AI Hub or a local model, branch on
config.EMBEDDING_PROVIDER / config.LLM_PROVIDER here — call sites never change.
Resilience (retry w/ exponential backoff + per-call timeout) is the OpenAI SDK's own,
configured from config — no hand-rolled retry loops.
"""
import config

_openai = None
def _client():
    global _openai
    if _openai is None:
        from openai import OpenAI
        _openai = OpenAI(timeout=config.REQUEST_TIMEOUT, max_retries=config.MAX_RETRIES)
    return _openai

def embed(texts):
    """list[str] -> list[list[float]] (one vector per text), via config.EMBEDDING_MODEL."""
    if config.EMBEDDING_PROVIDER != "openai":
        raise NotImplementedError(f"embedding provider '{config.EMBEDDING_PROVIDER}' not wired (v1: openai)")
    resp = _client().embeddings.create(model=config.EMBEDDING_MODEL, input=texts)
    return [d.embedding for d in resp.data]

def chat(messages, model=None):
    """OpenAI chat messages -> (text, usage). usage = {model, prompt_tokens, completion_tokens, cost}.
    No response_format (some gpt-5/o-series models reject it) — we instruct JSON in the prompt and
    parse robustly downstream."""
    if config.LLM_PROVIDER != "openai":
        raise NotImplementedError(f"llm provider '{config.LLM_PROVIDER}' not wired (v1: openai)")
    model = model or config.ANSWER_MODEL
    resp = _client().chat.completions.create(model=model, messages=messages)
    u = resp.usage
    usage = {"model": model, "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
             "cost": config.cost(model, u.prompt_tokens, u.completion_tokens)}
    return resp.choices[0].message.content, usage
