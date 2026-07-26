#!/usr/bin/env python3
"""
eval/measurements/sweep_effort.py — M1.1 operating-point sweep (BUILD_PLAN M1.1).

For each ANSWER_REASONING_EFFORT arm: run the gold DEV split through the canonical path
(Engine.ask); 3 repeats on refusal-prone types (synthesis/adversarial — where F2 showed
effort-dependent refusals), 1 run otherwise; gpt-5 judge on run 1 of answerable
positives (judge effort = config.JUDGE_REASONING_EFFORT, independent of the swept
answer knob). Writes raw rows here (committed) + one registry row per arm.

Runs use EVAL_CONCURRENCY for wall-clock; TTFT under concurrency is contended, so
published latency comes from effort_repeat_probe.py (sequential), not from this sweep.

Usage:  PYTHONPATH=. python3 eval/measurements/sweep_effort.py [efforts] [split]
        # defaults: minimal,low,medium  dev
"""
import concurrent.futures as cf
import datetime
import json
import statistics
import sys
from pathlib import Path

import config
import prompts
import providers
from core import Engine
from eval.dataset import load_gold
from eval.registry import record
from eval.score_retrieval import matches

REPEAT_TYPES = {"cross-doc", "distractor", "false-premise", "entity-confusion", "negative",
                "evidence-dense"}
REPEATS = 3

def judge(question, chunks, answer, gold):
    text, usage = providers.chat(prompts.judge_messages(question, chunks, answer, gold),
                                 model=config.JUDGE_MODEL,
                                 reasoning_effort=config.JUDGE_REASONING_EFFORT)
    d = prompts.parse_json(text) or {}
    return {"faithfulness": d.get("faithfulness"), "correctness": d.get("correctness"),
            "relevance": d.get("relevance"), "judge_cost": usage["cost"]}

def run_item(eng, it, run):
    tr = eng.ask(it["question"], log=False)
    row = {"id": it["id"], "answer_type": it["answer_type"], "run": run,
           "answerable": tr["answerable"], "gated": tr["gated"],
           "ttft_ms": tr["latency_ms"]["ttft"], "total_ms": tr["latency_ms"]["total"],
           "cost": tr["cost"], "n_citations": len(tr["citations"]),
           "hit5": any(matches(c, it) for c in tr["retrieved"][:5])
                   if it["answer_type"] != "negative" else None,
           "answer_prefix": tr["answer"][:80]}
    if run == 1 and tr["answerable"] and it["answer_type"] != "negative":
        chunks = [eng.retriever.meta[r["id"]] for r in tr["retrieved"]]
        row.update(judge(it["question"], chunks, tr["answer"], it.get("expected", "")))
    return row

def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 3) if xs else None

def sweep_arm(eng, effort, items, out):
    config.ANSWER_REASONING_EFFORT = effort          # arm override (read at call time)
    work = [(it, run) for it in items
            for run in range(1, (REPEATS if it["answer_type"] in REPEAT_TYPES else 1) + 1)]
    with cf.ThreadPoolExecutor(max_workers=config.EVAL_CONCURRENCY) as ex:
        rows = list(ex.map(lambda w: run_item(eng, *w), work))
    for r in rows:
        out.write(json.dumps({"effort": effort, **r}, ensure_ascii=False) + "\n")
    out.flush()

    pos = [r for r in rows if r["answer_type"] != "negative"]
    neg = [r for r in rows if r["answer_type"] == "negative"]
    per_type_refusal = {}
    for t in sorted({r["answer_type"] for r in pos}):
        g = [r for r in pos if r["answer_type"] == t]
        per_type_refusal[t] = f"{sum(1 for r in g if not r['answerable'])}/{len(g)}"
    m = {"effort": effort, "split": items[0].get("split", "?"), "items": len(items),
         "runs": len(rows),
         "false_refusal_rate": round(sum(1 for r in pos if not r["answerable"]) / len(pos), 3),
         "per_type_refusal_runs": per_type_refusal,
         "negative_refusal_runs": f"{sum(1 for r in neg if not r['answerable'])}/{len(neg)}",
         "hit5": round(sum(1 for r in pos if r["run"] == 1 and r["hit5"]) /
                       max(1, sum(1 for r in pos if r["run"] == 1)), 3),
         "faithfulness": _avg([r.get("faithfulness") for r in pos]),
         "correctness": _avg([r.get("correctness") for r in pos]),
         "relevance": _avg([r.get("relevance") for r in pos]),
         "ttft_median_ms": int(statistics.median(r["ttft_ms"] for r in rows if r["ttft_ms"])),
         "total_median_ms": int(statistics.median(r["total_ms"] for r in rows)),
         "cost_usd": round(sum(r.get("cost", 0) + r.get("judge_cost", 0) for r in rows), 4)}
    record("effort-sweep", m)
    print(json.dumps(m, indent=2))
    return m

def main():
    efforts = (sys.argv[1] if len(sys.argv) > 1 else "minimal,low,medium").split(",")
    split = sys.argv[2] if len(sys.argv) > 2 else "dev"
    items = load_gold(split=split)
    print(f"sweep: {efforts} × {len(items)} {split} items "
          f"(3 repeats on {sorted(REPEAT_TYPES)})")
    eng = Engine()
    providers._client()
    day = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
    path = Path(__file__).resolve().parent / f"{day}-effort-sweep.jsonl"
    with path.open("a", encoding="utf-8") as out:
        arms = [sweep_arm(eng, e, items, out) for e in efforts]
    print(f"\nraw rows -> {path.name}; {len(arms)} registry rows appended")

if __name__ == "__main__":
    main()
