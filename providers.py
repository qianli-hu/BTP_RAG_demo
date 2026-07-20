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

def chat(messages, model=None, reasoning_effort=None):
    """OpenAI chat messages -> (text, usage). usage = {model, prompt_tokens, completion_tokens, cost}.
    reasoning_effort is EXPLICIT per call (gpt-5 family only; None -> provider default) —
    call sites pass config.ANSWER_REASONING_EFFORT or config.JUDGE_REASONING_EFFORT so the
    answer path and the judge stay independently controlled."""
    if config.LLM_PROVIDER != "openai":
        raise NotImplementedError(f"llm provider '{config.LLM_PROVIDER}' not wired (v1: openai)")
    model = model or config.ANSWER_MODEL
    kw = {}
    if reasoning_effort and model.startswith("gpt-5"):
        kw["reasoning_effort"] = reasoning_effort
    resp = _client().chat.completions.create(model=model, messages=messages, **kw)
    u = resp.usage
    usage = {"model": model, "prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens,
             "cost": config.cost(model, u.prompt_tokens, u.completion_tokens)}
    return resp.choices[0].message.content, usage

def chat_stream(messages, model=None, reasoning_effort=None):
    """Streaming twin of chat(): yields ("token", text) as pieces arrive, then one
    ("usage", dict) at the end. stream_options.include_usage makes OpenAI send token
    counts in the final chunk — without it, streamed calls would be invisible to our
    cost accounting. reasoning_effort: explicit per call, same contract as chat()."""
    if config.LLM_PROVIDER != "openai":
        raise NotImplementedError(f"llm provider '{config.LLM_PROVIDER}' not wired (v1: openai)")
    model = model or config.ANSWER_MODEL
    kw = {}
    if reasoning_effort and model.startswith("gpt-5"):
        kw["reasoning_effort"] = reasoning_effort
    stream = _client().chat.completions.create(model=model, messages=messages, stream=True,
                                               stream_options={"include_usage": True}, **kw)
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield "token", chunk.choices[0].delta.content
        if chunk.usage:
            u = chunk.usage
            yield "usage", {"model": model, "prompt_tokens": u.prompt_tokens,
                            "completion_tokens": u.completion_tokens,
                            "cost": config.cost(model, u.prompt_tokens, u.completion_tokens)}
