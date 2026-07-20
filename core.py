"""
core.py — the ONE engine both the online API (/ask, /ask/stream) and the offline eval
call, so we eval exactly what we serve. ask_stream() is the canonical path
(retrieve -> generate_stream -> trace + request log); ask() is its buffered wrapper —
streaming is purely a transport difference (BUILD_PLAN §2.5).
"""
import json
import os
import time

import config
import prompts
from retrieve.generate import generate_stream
from retrieve.hybrid import Retriever

LOG = "eval/out/requests.jsonl"

def _log(trace):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(trace, ensure_ascii=False) + "\n")

class Engine:
    def __init__(self):
        self.retriever = Retriever()          # builds the BM25 index once; reuse across queries

    def ask(self, query, filters=None, log=True):
        """Buffered wrapper over ask_stream(): identical retrieval, gates, generation,
        logging — the caller just gets the finished trace instead of a token stream."""
        for kind, payload in self.ask_stream(query, filters=filters, log=log):
            if kind == "done":
                return payload

    def ask_stream(self, query, filters=None, log=True):
        """THE canonical path: yields ("token", text)... then ("done", trace).
        latency_ms includes TTFT (time-to-first-token) alongside retrieve/generate/total."""
        t0 = time.perf_counter()
        hits, dbg = self.retriever.retrieve(query, k=config.TOP_K, filters=filters)
        t1 = time.perf_counter()
        top_cos = dbg["dense"][0][1] if dbg["dense"] else 0.0

        ttft, gen = None, None
        for kind, payload in generate_stream(query, hits, top_cosine=top_cos):
            if kind == "token":
                if ttft is None:
                    ttft = time.perf_counter()
                yield "token", payload
            else:
                gen = payload
        t2 = time.perf_counter()

        u = gen["usage"]
        trace = {
            "query": query, "filters": filters,
            "fingerprint": config.fingerprint(prompt_version=prompts.VERSION),
            "top_cosine": round(top_cos, 4),
            "retrieved": [{"id": h["id"], "doc": h["doc"], "section_path": h["section_path"],
                           "source_url": h["source_url"], "score": h["score"]} for h in hits],
            "answer": gen["answer"], "citations": gen["citations"],
            "answerable": gen["answerable"], "gated": gen["gated"],
            "latency_ms": {"retrieve": round((t1 - t0) * 1000),
                           "ttft": (round((ttft - t0) * 1000) if ttft else None),
                           "generate": round((t2 - t1) * 1000),
                           "total": round((t2 - t0) * 1000)},
            "tokens": ({"prompt": u["prompt_tokens"], "completion": u["completion_tokens"]} if u else None),
            "cost": (u["cost"] if u else 0.0),
        }
        if log:
            _log(trace)
        yield "done", trace


def main():
    import sys
    eng = Engine()
    q = " ".join(sys.argv[1:]) or "What distance functions does the SAP HANA vector engine support?"
    tr = eng.ask(q, log=False)
    print(f"Q: {q}\nA: {tr['answer']}\ncitations: {tr['citations']}\n"
          f"answerable={tr['answerable']} gated={tr['gated']} top_cos={tr['top_cosine']}\n"
          f"latency_ms={tr['latency_ms']} cost=${tr['cost']:.5f} tokens={tr['tokens']}")

if __name__ == "__main__":
    main()
