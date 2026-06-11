"""
core.py — the ONE engine both the online API (/ask) and the offline eval call, so we
eval exactly what we serve. ask() = retrieve -> generate, and emits a structured
request-log line (fingerprint + retrieved + answer + per-step latency + tokens + cost).
"""
import json
import os
import time

import config
import prompts
from retrieve.generate import generate
from retrieve.hybrid import Retriever

LOG = "eval/out/requests.jsonl"

class Engine:
    def __init__(self):
        self.retriever = Retriever()          # builds the BM25 index once; reuse across queries

    def ask(self, query, filters=None, log=True):
        t0 = time.perf_counter()
        hits, dbg = self.retriever.retrieve(query, k=config.TOP_K, filters=filters)
        t1 = time.perf_counter()
        top_cos = dbg["dense"][0][1] if dbg["dense"] else 0.0
        gen = generate(query, hits, top_cosine=top_cos)
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
                           "generate": round((t2 - t1) * 1000),
                           "total": round((t2 - t0) * 1000)},
            "tokens": ({"prompt": u["prompt_tokens"], "completion": u["completion_tokens"]} if u else None),
            "cost": (u["cost"] if u else 0.0),
        }
        if log:
            os.makedirs(os.path.dirname(LOG), exist_ok=True)
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(trace, ensure_ascii=False) + "\n")
        return trace


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
