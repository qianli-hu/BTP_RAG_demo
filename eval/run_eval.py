#!/usr/bin/env python3
"""
eval/run_eval.py — full eval-what-you-serve.

For each gold item: core.ask (real answer + citations + retrieved + latency/cost), then
the gpt-5 judge (faithfulness/correctness/relevance) on answerable items -> results.jsonl,
then a summary (rule gates + judge averages + cost/latency), fingerprint-stamped.

Run:  PYTHONPATH=. python3 eval/run_eval.py [limit]
"""
import json
import os
import statistics
import sys

import config
import prompts
import providers
from core import Engine
from eval.score_retrieval import NOT_IN_KB, matches

OUT = "eval/out/results.jsonl"

def judge(question, chunks, answer, gold):
    text, usage = providers.chat(prompts.judge_messages(question, chunks, answer, gold),
                                 model=config.JUDGE_MODEL)
    d = prompts.parse_json(text) or {}
    return {"faithfulness": d.get("faithfulness"), "correctness": d.get("correctness"),
            "relevance": d.get("relevance"), "rationale": d.get("rationale"),
            "judge_cost": usage["cost"]}

def run_one(eng, it):
    """Full per-item work (ask + judge) — the unit parallelized across the eval loop."""
    tr = eng.ask(it["question"], log=False)
    row = {"id": it["id"], "answer_type": it["answer_type"], "answer": tr["answer"],
           "citations": tr["citations"], "answerable": tr["answerable"],
           "retrieved": tr["retrieved"], "top_cosine": tr["top_cosine"],
           "latency_ms": tr["latency_ms"], "gen_cost": tr["cost"]}
    if tr["answerable"]:
        chunks = [eng.retriever.meta[r["id"]] for r in tr["retrieved"]]
        row.update(judge(it["question"], chunks, tr["answer"], it.get("expected", "")))
    print(f"  {it['id']} [{it['answer_type'][:9]:9s}] ans={tr['answerable']!s:5s} "
          f"faith={row.get('faithfulness')} corr={row.get('correctness')}")
    return row

def main():
    import concurrent.futures as cf
    import time
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    concurrency = int(sys.argv[2]) if len(sys.argv) > 2 else config.EVAL_CONCURRENCY
    from eval.dataset import load_gold
    gold = load_gold()
    if limit:
        gold = gold[:limit]
    eng = Engine()
    providers._client()                       # warm the OpenAI client singleton before threads

    t0 = time.perf_counter()
    if concurrency > 1:                        # I/O-bound -> threads give a near-linear speedup
        with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
            rows = list(ex.map(lambda it: run_one(eng, it), gold))
    else:
        rows = [run_one(eng, it) for it in gold]
    wall = time.perf_counter() - t0

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    report(rows, {g["id"]: g for g in gold}, eng)
    print(f"\nWALL-CLOCK: {wall:.1f}s for {len(gold)} items @ concurrency={concurrency} "
          f"({wall/len(gold):.1f}s/item)")

    # archive the PER-ITEM results immutably + ledger the run (results.jsonl is latest-only)
    if not limit:                              # only full runs enter the registry
        import datetime

        from eval.registry import record
        ts = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
        archive = f"eval/out/runs/{ts}-rag-eval.jsonl"
        os.makedirs(os.path.dirname(archive), exist_ok=True)
        with open(archive, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        pos = [r for r in rows if r["answer_type"] != "negative"]
        neg_ok = sum(1 for r in rows
                     if r["answer_type"] == "negative" and r["answer"].strip() == NOT_IN_KB)
        record("rag-eval", {
            "items": len(rows),
            "faithfulness": _avg([r.get("faithfulness") for r in pos]),
            "correctness": _avg([r.get("correctness") for r in pos]),
            "negative_refusal": f"{neg_ok}/7",
            "cost_usd": round(sum(r.get("gen_cost", 0) + r.get("judge_cost", 0) for r in rows), 4),
            "results_file": archive})
        print(f"archived per-item results -> {archive} (+ registry row)")

def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None

def report(rows, gmap, eng):
    corpus_ids = set(eng.retriever.meta)
    pos = [r for r in rows if r["answer_type"] != "negative"]
    neg = [r for r in rows if r["answer_type"] == "negative"]
    print("\n================= EVAL REPORT =================")
    # retrieval hit-rate
    for k in (1, 3, 5):
        h = sum(1 for r in pos if any(matches(c, gmap[r["id"]]) for c in r["retrieved"][:k]))
        print(f"hit-rate@{k}: {h}/{len(pos)} = {h/len(pos):.0%}" if pos else "")
    # refusal gate
    nok = sum(1 for r in neg if r["answer"].strip() == NOT_IN_KB)
    fa = sum(1 for r in pos if r["answer"].strip() == NOT_IN_KB)   # false refusals
    print(f"negative refusal (exact): {nok}/{len(neg)} | false-refusals on positives: {fa}/{len(pos)}")
    # citation validity (chunk-id exists + retrieved)
    cs = co = 0
    for r in pos:
        if not r["citations"]:
            continue
        cs += 1
        rset = {c["id"] for c in r["retrieved"]}
        if all(cid in corpus_ids and cid in rset for cid in r["citations"]):
            co += 1
    print(f"citation validity: {co}/{cs}")
    # judge averages (answerable positives)
    ans = [r for r in pos if r["answerable"]]
    print(f"judge (n={len(ans)}): faithfulness={_avg([r.get('faithfulness') for r in ans])} "
          f"correctness={_avg([r.get('correctness') for r in ans])} "
          f"relevance={_avg([r.get('relevance') for r in ans])}")
    # by category
    print("  faithfulness/correctness by type:")
    for t in sorted({r["answer_type"] for r in ans}):
        g = [r for r in ans if r["answer_type"] == t]
        print(f"    {t:16s} faith={_avg([r.get('faithfulness') for r in g])} "
              f"corr={_avg([r.get('correctness') for r in g])}  (n={len(g)})")
    # cost + latency
    gen_cost = sum(r["gen_cost"] for r in rows)
    judge_cost = sum(r.get("judge_cost", 0) for r in rows)
    lat = [r["latency_ms"]["total"] for r in rows]
    print(f"\ncost: gen=${gen_cost:.4f} + judge=${judge_cost:.4f} = ${gen_cost+judge_cost:.4f}")
    print(f"latency: median={int(statistics.median(lat))}ms max={max(lat)}ms (answer path)")
    print(f"fingerprint: {config.fingerprint(prompt_version=prompts.VERSION)}")

if __name__ == "__main__":
    main()
