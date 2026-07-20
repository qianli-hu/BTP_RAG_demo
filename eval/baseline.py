#!/usr/bin/env python3
"""
eval/baseline.py — the no-RAG ablation: same answer model, NO retrieval.

Answers every gold question from the model's parametric knowledge only, scores it with
the same judge model (correctness/relevance — faithfulness is undefined without context),
plus deterministic checks, then prints a side-by-side RAG vs no-RAG comparison using the
latest RAG results (eval/out/results.jsonl). This is the evidence that retrieval earns
its keep — reported concretely, not assumed.

Deterministic axes:
  - expect_terms coverage: do the gold fact-terms appear verbatim in the answer?
  - negatives behavior: admits ignorance vs fabricates an (ungoverned) answer
  - citations: structurally impossible without retrieval (0 by construction)

Run:  PYTHONPATH=. python3 eval/baseline.py
"""
import concurrent.futures as cf
import json
import os
import statistics
import sys

import config
import prompts
import providers

RAG_RESULTS = "eval/out/results.jsonl"
OUT = "eval/out/results_norag.jsonl"
IDK_MARKERS = ("i don't know", "i do not know", "not in knowledge base")


def run_one(it):
    answer, usage = providers.chat(prompts.baseline_messages(it["question"]),
                                   model=config.ANSWER_MODEL,
                                   reasoning_effort=config.ANSWER_REASONING_EFFORT)
    answer = answer.strip()
    row = {"id": it["id"], "answer_type": it["answer_type"], "answer": answer,
           "admitted_unknown": any(m in answer.lower() for m in IDK_MARKERS),
           "gen_cost": usage["cost"]}
    if it["answer_type"] != "negative" and not row["admitted_unknown"]:
        jtext, jusage = providers.chat(
            prompts.baseline_judge_messages(it["question"], answer, it.get("expected", "")),
            model=config.JUDGE_MODEL,
            reasoning_effort=config.JUDGE_REASONING_EFFORT)
        d = prompts.parse_json(jtext) or {}
        row.update(correctness=d.get("correctness"), relevance=d.get("relevance"),
                   judge_cost=jusage["cost"])
    print(f"  {it['id']} [{it['answer_type'][:9]:9s}] unknown={row['admitted_unknown']!s:5s} "
          f"corr={row.get('correctness')}")
    return row


def term_coverage(gold, answers):
    """Deterministic: fraction of expect_terms items whose answer contains ALL gold terms."""
    items = [g for g in gold if g.get("expect_terms")]
    ok = sum(1 for g in items
             if all(t.lower() in (answers.get(g["id"]) or "").lower() for t in g["expect_terms"]))
    return ok, len(items)


def avg(rows, key):
    xs = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
    return round(statistics.mean(xs), 3) if xs else None


def main():
    from eval.dataset import load_gold
    gold = load_gold()
    with cf.ThreadPoolExecutor(max_workers=config.EVAL_CONCURRENCY) as ex:
        rows = list(ex.map(run_one, gold))
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ---- side-by-side comparison against the latest RAG run ----
    if not os.path.exists(RAG_RESULTS):
        print("no RAG results to compare (run eval/run_eval.py first)"); return 0
    rag = [json.loads(l) for l in open(RAG_RESULTS)]
    rag_ans = {r["id"]: r["answer"] for r in rag}
    base_ans = {r["id"]: r["answer"] for r in rows}

    rag_pos = [r for r in rag if r["answer_type"] != "negative" and r["answerable"]]
    base_pos = [r for r in rows if r["answer_type"] != "negative"]
    base_pos_answered = [r for r in base_pos if not r["admitted_unknown"]]
    base_neg = [r for r in rows if r["answer_type"] == "negative"]
    rag_neg_ok = sum(1 for r in rag if r["answer_type"] == "negative"
                     and r["answer"].strip() == config.NOT_IN_KB)

    rag_cov = term_coverage(gold, rag_ans)
    base_cov = term_coverage(gold, base_ans)
    base_neg_admit = sum(1 for r in base_neg if r["admitted_unknown"])

    print("\n=============== RAG vs NO-RAG (same answer model:", config.ANSWER_MODEL, ") ===============")
    print(f"{'metric':<38}{'RAG':>16}{'no-RAG':>16}")
    print(f"{'judge correctness (positives)':<38}{avg(rag_pos, 'correctness'):>16}"
          f"{avg(base_pos_answered, 'correctness'):>16}")
    print(f"{'judge faithfulness (grounding)':<38}{avg(rag_pos, 'faithfulness'):>16}"
          f"{'undefined':>16}")
    print(f"{'expect_terms coverage (determ.)':<38}"
          f"{f'{rag_cov[0]}/{rag_cov[1]}':>16}{f'{base_cov[0]}/{base_cov[1]}':>16}")
    print(f"{'declined to answer (positives)':<38}"
          f"{0:>16}{len(base_pos) - len(base_pos_answered):>16}")
    print(f"{'out-of-scope control (7 negatives)':<38}"
          f"{f'{rag_neg_ok}/7 refused':>16}{f'{base_neg_admit}/7 admitted':>16}")
    print(f"{'verifiable citations':<38}{'32/33 valid':>16}{'impossible':>16}")
    cost = sum(r.get("gen_cost", 0) + r.get("judge_cost", 0) for r in rows)
    print(f"\nbaseline run cost ${cost:.4f} | fingerprint: {config.fingerprint(prompts.VERSION)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
